"""Thirty-day detail retention, permanent daily summaries and storage projection.

Retention (``apply_retention``) runs in the paired worker only after the daily
backup of the day was created and verified.  Detail strictly older than
``now - 30 days`` is archived as gzip JSON lines, the archive is decompressed
and compared with the original bytes (SHA-256 receipt), and only then removed;
a row exactly at the cutoff is kept.  Without a verified backup nothing is
deleted.

Archived content, explicitly: for each account, the decoded
``wallet_equity.sqlite`` ``observations`` older than the cutoff (``at`` plus
the decoded packet); for the primary account, the ``paper_publications.sqlite``
``paired_batches`` rows with status ``complete`` whose decoded packet
``created_at`` is older than the cutoff (packet, completed accounts, hash and
rowid).  The most recent complete batch and pending batches are never removed.
Removed complete batches are counted in ``paper_history_retired`` in the same
transaction, so the lifetime batch counter (``max_cycles``) survives retention.

Daily summaries are permanent: archived observations are folded into
``.history-retention/daily_summaries.sqlite`` with a per-account watermark in
the same transaction, and ``daily_summaries(root)`` merges them, read-only,
with the observations still in detail.  The storage projection keeps one
sample per UTC day for 31 days and reports the heartbeat
``storage_projection`` contract (``schema_version`` 1).

Limits: archives stay on the same volume and have no deletion or export
policy; retention bounds the detail tables, not the volume.  Packet objects
shared through ``paired_packet_objects`` are not reclaimed and SQLite files are
not vacuumed.  No source state is read outside the supplied root; no live
mode, secret or network call is used.
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from hermes_trading.archive_snapshot import _safe_root
from hermes_trading.paper_publication import _sync_dir, _temporary
from hermes_trading.paired_packet_storage import load_packet
from hermes_trading.paired_primary import resolve_primary
from hermes_trading.reflection_supervision import _json, _now, _time

RETENTION_DAYS = 30
SAMPLE_DAYS = RETENTION_DAYS + 1
PURPOSE = "paper_history_retention_v1"
RECEIPT_SCHEMA = 1
PROJECTION_SCHEMA = 1
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
HISTORY_DIR = ".history-retention"
SUMMARY_DB = HISTORY_DIR / Path("daily_summaries.sqlite")
DAY_SECONDS = 86400


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _timestamp(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    return _time(value).astimezone(timezone.utc)


def _cutoff(now):
    current = _now(now).astimezone(timezone.utc)
    return current - timedelta(days=RETENTION_DAYS)


def _archive_dir(root):
    directory = _safe_root(root / HISTORY_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _daily(date):
    return _timestamp(date).date().isoformat()


def _connect_summary_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5, isolation_level=None)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        # Archived part of each day only; live detail is merged on read.
        db.execute(
            """CREATE TABLE IF NOT EXISTS daily_summaries (
                account TEXT NOT NULL,
                date TEXT NOT NULL,
                decisions INTEGER NOT NULL,
                last_at TEXT NOT NULL,
                balance TEXT,
                costs TEXT,
                PRIMARY KEY (account, date)
            )"""
        )
        # Observations strictly before ``through`` are already summarized.
        db.execute(
            """CREATE TABLE IF NOT EXISTS summary_watermarks (
                account TEXT PRIMARY KEY,
                through TEXT NOT NULL
            )"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS storage_samples (
                date TEXT PRIMARY KEY,
                at TEXT NOT NULL,
                state_bytes INTEGER NOT NULL
            )"""
        )
        db.commit()
    except BaseException:
        db.rollback()
        db.close()
        raise
    return db


def _summarize_observation(observation):
    """Balance and costs carried by one wallet observation, as decimal strings."""
    known_cost_equity = observation.get("known_cost_equity")
    net_equity = observation.get("net_equity")
    balance = known_cost_equity if known_cost_equity is not None else net_equity
    costs = observation.get("known_accrued_costs")
    return {"balance": None if balance is None else str(balance),
            "costs": None if costs is None else str(costs)}


def _day_totals(observations):
    """``{date: {decisions, last_at, balance, costs}}`` for time-ordered observations."""
    days = {}
    for observation in observations:
        stamp = _timestamp(observation["at"])
        row = days.setdefault(stamp.date().isoformat(), {"decisions": 0, "last_at": None})
        row["decisions"] += 1
        if row["last_at"] is None or stamp >= _timestamp(row["last_at"]):
            row.update(_summarize_observation(observation), last_at=stamp.isoformat())
    return days


def _encode_rows(rows):
    """Serialize rows as newline-delimited JSON for byte-exact round trips."""
    lines = []
    for row in rows:
        lines.append(_json(row).encode("utf-8"))
        lines.append(b"\n")
    return b"".join(lines)


def _gzip_bytes(data, path, fault):
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix + "." + hashlib.sha256(os.urandom(16)).hexdigest()[:16] + ".tmp"
    temporary = _safe_root(path.with_suffix(suffix))
    try:
        with temporary.open("xb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6, mtime=0) as zipped:
                zipped.write(data)
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(temporary, path)
        _sync_dir(path.parent)
        if fault:
            fault("after_archive_write")
    finally:
        if temporary.exists():
            temporary.unlink()


def _verify_gzip_archive(archive_path, original_bytes, original_sha256):
    """Decompress and verify the archived content is byte-identical to the original."""
    archive_path = _safe_root(archive_path)
    if not archive_path.is_file():
        raise ValueError("retention archive is missing")
    size = archive_path.stat().st_size
    if size > MAX_ARCHIVE_BYTES:
        raise ValueError("retention archive exceeds byte bound")
    digest = hashlib.sha256()
    decoded_size = 0
    try:
        with gzip.open(archive_path, "rb") as source:
            while chunk := source.read(1024 * 1024):
                decoded_size += len(chunk)
                if decoded_size > MAX_ARCHIVE_BYTES:
                    raise ValueError("retention archive decodes beyond bound")
                digest.update(chunk)
    except (OSError, EOFError) as error:
        raise ValueError("retention archive is corrupt or unreadable") from error
    if decoded_size != len(original_bytes) or digest.hexdigest() != original_sha256:
        raise ValueError("retention archive does not match original content")


def _write_receipt(path, record):
    data = _json(record).encode("utf-8")
    temporary = _temporary(path, data)
    try:
        os.replace(temporary, path)
        _sync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _archive_content(rows, archive_path, receipt_path, kind, fault, now):
    """Create a verified gzip archive and its receipt for the supplied rows."""
    original_bytes = _encode_rows(rows)
    original_sha256 = _sha(original_bytes)
    _gzip_bytes(original_bytes, archive_path, fault)
    _verify_gzip_archive(archive_path, original_bytes, original_sha256)
    record = {
        "schema_version": RECEIPT_SCHEMA,
        "purpose": PURPOSE,
        "kind": kind,
        "archived_at": now.isoformat(),
        "row_count": len(rows),
        "original_bytes": len(original_bytes),
        "original_sha256": original_sha256,
        "archive_sha256": _sha(archive_path.read_bytes()),
    }
    _write_receipt(receipt_path, record)
    return record


def _archive_paths(archive_dir, stem, cutoff, rows):
    """A fresh archive name: the cutoff date, then a content suffix on a same-day rerun."""
    base = archive_dir / f"{stem}_{cutoff.date().isoformat()}.jsonl.gz"
    if not base.exists() and not base.with_suffix(".receipt.json").exists():
        return base, base.with_suffix(".receipt.json")
    tag = _sha(_encode_rows(rows))[:16]
    path = archive_dir / f"{stem}_{cutoff.date().isoformat()}.{tag}.jsonl.gz"
    if path.exists() or path.with_suffix(".receipt.json").exists():
        raise ValueError("retention archive for this content already exists")
    return path, path.with_suffix(".receipt.json")


def _fold_summaries(summary_db, account, observations, cutoff):
    """Fold archived observations into the permanent summary, once, with the watermark."""
    summary_db.execute("BEGIN IMMEDIATE")
    try:
        row = summary_db.execute(
            "SELECT through FROM summary_watermarks WHERE account=?", (account,)).fetchone()
        through = None if row is None else _timestamp(row[0])
        fresh = [obs for obs in observations if through is None or _timestamp(obs["at"]) >= through]
        for date, day in _day_totals(fresh).items():
            stored = summary_db.execute(
                "SELECT decisions,last_at,balance,costs FROM daily_summaries WHERE account=? AND date=?",
                (account, date)).fetchone()
            decisions, last_at, balance, costs = day["decisions"], day["last_at"], day["balance"], day["costs"]
            if stored is not None:
                decisions += stored[0]
                if _timestamp(stored[1]) > _timestamp(last_at):
                    last_at, balance, costs = stored[1], stored[2], stored[3]
            summary_db.execute(
                """INSERT INTO daily_summaries(account,date,decisions,last_at,balance,costs)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(account,date) DO UPDATE SET decisions=excluded.decisions,
                   last_at=excluded.last_at, balance=excluded.balance, costs=excluded.costs""",
                (account, date, decisions, last_at, balance, costs))
        if through is None or cutoff > through:
            summary_db.execute(
                """INSERT INTO summary_watermarks(account,through) VALUES(?,?)
                   ON CONFLICT(account) DO UPDATE SET through=excluded.through""",
                (account, cutoff.isoformat()))
        summary_db.commit()
    except BaseException:
        summary_db.rollback()
        raise


def _decoded_observations(db, rows):
    observations = []
    for stamp, payload in rows:
        observation = load_packet(db, payload)
        observation["at"] = stamp
        observations.append(observation)
    return observations


def _retain_wallet_observations(account_dir, cutoff, archive_dir, summary_db, fault, now):
    """Archive, summarize then remove wallet observations older than ``cutoff``."""
    db_path = _safe_root(account_dir / "wallet_equity.sqlite")
    if not db_path.is_file():
        return {"kept": 0, "archived": 0}
    db = sqlite3.connect(db_path, timeout=5, isolation_level=None)
    try:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='observations'"
        ).fetchone() is None:
            db.commit()
            return {"kept": 0, "archived": 0}
        rows = db.execute("SELECT at, payload FROM observations ORDER BY at").fetchall()
        old_rows = [row for row in rows if _timestamp(row[0]) < cutoff]
        if old_rows:
            old_observations = _decoded_observations(db, old_rows)
            archive_path, receipt_path = _archive_paths(
                archive_dir, f"{account_dir.name}_wallet_equity", cutoff, old_observations)
            _archive_content(old_observations, archive_path, receipt_path,
                             f"wallet_equity:{account_dir.name}", fault, now)
            # The summary commits before the deletion; its watermark makes a
            # crash between both commits harmless (no double count).
            _fold_summaries(summary_db, account_dir.name, old_observations, cutoff)
            if fault:
                fault("before_detail_delete")
            db.executemany("DELETE FROM observations WHERE at = ?", ((row[0],) for row in old_rows))
        db.commit()
        return {"kept": len(rows) - len(old_rows), "archived": len(old_rows)}
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def _ensure_retired_table(db):
    db.execute("""CREATE TABLE IF NOT EXISTS paper_history_retired (
        id INTEGER PRIMARY KEY CHECK(id=1),
        complete INTEGER NOT NULL CHECK(complete>=0))""")


def retired_complete(db):
    """Complete batches removed by retention, read inside the caller's snapshot."""
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_history_retired'").fetchone() is None:
        return 0
    row = db.execute("SELECT complete FROM paper_history_retired WHERE id=1").fetchone()
    return 0 if row is None else int(row[0])


def _retain_publication_batches(primary_dir, cutoff, archive_dir, fault, now):
    """Archive complete paired batches older than ``cutoff``, keeping the latest one."""
    db_path = _safe_root(primary_dir / "paper_publications.sqlite")
    if not db_path.is_file():
        return {"kept": 0, "archived": 0}
    db = sqlite3.connect(db_path, timeout=5, isolation_level=None)
    try:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "paired_batches" not in tables:
            db.commit()
            return {"kept": 0, "archived": 0}
        if "paired_archive_checkpoint" in tables:
            # The archive journal owns its own lifetime count: never mix both.
            db.commit()
            return {"kept": None, "archived": 0, "skipped": "archive_journal"}
        rows = db.execute(
            "SELECT batch_id, status, payload, input_hash, completed, rowid FROM paired_batches ORDER BY rowid"
        ).fetchall()
        latest_complete = max((row[5] for row in rows if row[1] == "complete"), default=None)
        old_rows = []
        for batch_id, status, payload, input_hash, completed, rowid in rows:
            if status != "complete" or rowid == latest_complete:
                continue
            packet = load_packet(db, payload)
            created_at = packet.get("created_at") if isinstance(packet, dict) else None
            if not isinstance(created_at, str) or _timestamp(created_at) >= cutoff:
                continue
            old_rows.append({
                "batch_id": batch_id,
                "status": status,
                "packet": packet,
                "input_hash": input_hash,
                "completed_packet": load_packet(db, completed) if completed else None,
                "rowid": rowid,
            })
        if old_rows:
            archive_path, receipt_path = _archive_paths(archive_dir, "paper_publications", cutoff, old_rows)
            _archive_content(old_rows, archive_path, receipt_path, "paper_publications", fault, now)
            if fault:
                fault("before_detail_delete")
            _ensure_retired_table(db)
            retired = retired_complete(db) + len(old_rows)
            db.executemany("DELETE FROM paired_batches WHERE rowid = ?", ((row["rowid"],) for row in old_rows))
            db.execute(
                """INSERT INTO paper_history_retired(id,complete) VALUES(1,?)
                   ON CONFLICT(id) DO UPDATE SET complete=excluded.complete""", (retired,))
        db.commit()
        return {"kept": len(rows) - len(old_rows), "archived": len(old_rows)}
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def _manifest(root):
    return json.loads((root / "manifest.json").read_text(encoding="utf-8"))


def apply_retention(root, now=None, *, backup_verified=True, fault=None):
    """Archive, verify and only then remove detail older than 30 days.

    ``backup_verified`` must be true only after the daily backup of ``now`` has
    been created and verified.  Otherwise nothing is touched.
    """
    root = _safe_root(Path(root).absolute())
    if backup_verified is not True:
        raise ValueError("retention refused: daily backup of the day is not verified")
    current = _now(now).astimezone(timezone.utc)
    cutoff = _cutoff(current)
    manifest = _manifest(root)
    selection = resolve_primary(manifest)
    primary_dir = root / selection.profile_directory(manifest, "adaptive")
    archive_dir = _archive_dir(root)
    summary_db = _connect_summary_db(_safe_root(root / SUMMARY_DB))
    try:
        result = {
            "schema_version": RECEIPT_SCHEMA,
            "purpose": PURPOSE,
            "cutoff": cutoff.isoformat(),
            "accounts": {},
            "publications": {"kept": 0, "archived": 0},
        }
        for profile in manifest.get("profiles", []):
            account_dir = root / profile["directory"]
            result["accounts"][profile["directory"]] = _retain_wallet_observations(
                account_dir, cutoff, archive_dir, summary_db, fault, current)
        result["publications"] = _retain_publication_batches(primary_dir, cutoff, archive_dir, fault, current)
        _sync_dir(archive_dir)
        return result
    finally:
        summary_db.close()


def _read_only(path):
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)


def _detail_observations(root, account):
    """Wallet observations still in detail, decoded inside one read snapshot."""
    wallet = _safe_root(root / account / "wallet_equity.sqlite")
    if not wallet.is_file():
        return []
    with closing(_read_only(wallet)) as db:
        db.execute("BEGIN")
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='observations'").fetchone() is None:
            return []
        rows = db.execute("SELECT at, payload FROM observations ORDER BY at").fetchall()
        return _decoded_observations(db, rows)


def _archived_summaries(summary_path):
    archived, watermarks = {}, {}
    if not summary_path.is_file():
        return archived, watermarks
    with closing(_read_only(summary_path)) as db:
        db.execute("BEGIN")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "daily_summaries" in tables:
            for account, date, decisions, last_at, balance, costs in db.execute(
                    "SELECT account,date,decisions,last_at,balance,costs FROM daily_summaries"):
                archived.setdefault(account, {})[date] = {
                    "decisions": decisions, "last_at": last_at, "balance": balance, "costs": costs}
        if "summary_watermarks" in tables:
            watermarks = {account: _timestamp(through) for account, through in
                          db.execute("SELECT account,through FROM summary_watermarks")}
    return archived, watermarks


def daily_summaries(root):
    """Permanent daily summaries for every account, read-only.

    ``{account_directory: [{'date', 'balance', 'decisions', 'costs'}, ...]}``,
    one row per UTC day of activity (archived days included), amounts as
    decimal strings, ``decisions`` the number of wallet observations (cycles).

    Read order matters: every detail snapshot is taken before the summary
    snapshot.  Retention commits the summary and its watermark before deleting
    the detail, so a summary read afterwards always covers any observation the
    detail snapshots no longer hold, and the watermark drops the detail rows it
    already counts.  A retention running between the reads, including the one
    that creates the summary file, therefore neither loses nor doubles a day.
    """
    root = _safe_root(Path(root).absolute())
    manifest = _manifest(root)
    accounts = [profile["directory"] for profile in manifest.get("profiles", [])]
    detail = {account: _detail_observations(root, account) for account in accounts}
    archived, watermarks = _archived_summaries(_safe_root(root / SUMMARY_DB))
    summaries = {}
    for account in accounts:
        days = {date: dict(row) for date, row in archived.get(account, {}).items()}
        through = watermarks.get(account)
        fresh = [obs for obs in detail[account] if through is None or _timestamp(obs["at"]) >= through]
        for date, day in _day_totals(fresh).items():
            stored = days.get(date)
            if stored is None:
                days[date] = day
                continue
            stored["decisions"] += day["decisions"]
            if _timestamp(day["last_at"]) >= _timestamp(stored["last_at"]):
                stored.update(balance=day["balance"], costs=day["costs"], last_at=day["last_at"])
        summaries[account] = [
            {"date": date, "balance": row["balance"], "decisions": int(row["decisions"]), "costs": row["costs"]}
            for date, row in sorted(days.items())
        ]
    return summaries


def restore_wallet_archive(archive_path):
    """Return the archived bytes after checking them against the receipt."""
    archive_path = _safe_root(Path(archive_path))
    receipt_path = archive_path.with_suffix(".receipt.json")
    if not receipt_path.is_file():
        raise ValueError("retention archive receipt is missing")
    record = json.loads(receipt_path.read_text(encoding="utf-8"))
    if _sha(archive_path.read_bytes()) != record.get("archive_sha256"):
        raise ValueError("retention archive differs from its receipt")
    with gzip.open(archive_path, "rb") as source:
        original_bytes = source.read(MAX_ARCHIVE_BYTES + 1)
    if len(original_bytes) > MAX_ARCHIVE_BYTES:
        raise ValueError("retention archive decodes beyond bound")
    if len(original_bytes) != record.get("original_bytes") or _sha(original_bytes) != record.get("original_sha256"):
        raise ValueError("restored archive content differs from receipt")
    return original_bytes


# --- storage projection ------------------------------------------------------

def _projection(status, bytes_per_day=None, days_before_reserve=None, **extra):
    return dict({"schema_version": PROJECTION_SCHEMA, "status": status,
                 "bytes_per_day": bytes_per_day, "days_before_reserve": days_before_reserve}, **extra)


def _count(value):
    return value if type(value) is int and value >= 0 else None


def project_storage(samples, *, now, state_bytes, free_bytes, reserve_bytes, max_state_bytes):
    """Pure projection from daily ``(at, state_bytes)`` samples and one measurement.

    ``bytes_per_day`` is the integer growth between the oldest sample of the
    window and the current measurement; at least one day of history is
    required.  ``days_before_reserve`` is ``headroom // bytes_per_day`` with
    ``headroom = max(0, min(free - reserve, max_state - reserve - state))``.
    """
    values = (state_bytes, free_bytes, reserve_bytes, max_state_bytes)
    if any(_count(value) is None for value in values):
        raise ValueError("storage projection requires non-negative integer inputs")
    current = _timestamp(now)
    headroom = max(0, min(free_bytes - reserve_bytes, max_state_bytes - reserve_bytes - state_bytes))
    window = [(at, size) for at, size in samples
              if timedelta(0) <= current - at <= timedelta(days=SAMPLE_DAYS)]
    bytes_per_day = None
    sample_days = 0
    if window:
        oldest_at, oldest_size = min(window)
        elapsed = int((current - oldest_at).total_seconds())
        sample_days = elapsed // DAY_SECONDS
        if elapsed >= DAY_SECONDS:
            bytes_per_day = (state_bytes - oldest_size) * DAY_SECONDS // elapsed
    extra = {"headroom_bytes": headroom, "sample_days": sample_days}
    if headroom == 0:
        return _projection("reserve_reached", bytes_per_day, 0, **extra)
    if bytes_per_day is None:
        return _projection("insufficient_data", **extra)
    if bytes_per_day <= 0:
        return _projection("non_positive_growth", bytes_per_day, None, **extra)
    return _projection("projected", bytes_per_day, headroom // bytes_per_day, **extra)


def storage_projection(root, now=None, *, state_bytes, free_bytes, reserve_bytes, max_state_bytes):
    """Record today's storage sample under ``root`` and return the projection.

    One sample per UTC day (the latest of the day) is kept for 31 days in
    ``.history-retention/daily_summaries.sqlite``.
    """
    root = _safe_root(Path(root).absolute())
    current = _now(now).astimezone(timezone.utc)
    if _count(state_bytes) is None:
        raise ValueError("storage projection requires non-negative integer inputs")
    db = _connect_summary_db(_safe_root(root / SUMMARY_DB))
    try:
        db.execute("BEGIN IMMEDIATE")
        try:
            # Samples recorded before today, then today's measurement.
            samples = [(_timestamp(at), size) for at, size in db.execute(
                "SELECT at, state_bytes FROM storage_samples WHERE date < ? ORDER BY date",
                (current.date().isoformat(),))]
            db.execute(
                """INSERT INTO storage_samples(date,at,state_bytes) VALUES(?,?,?)
                   ON CONFLICT(date) DO UPDATE SET at=excluded.at, state_bytes=excluded.state_bytes""",
                (current.date().isoformat(), current.isoformat(), state_bytes))
            db.execute("DELETE FROM storage_samples WHERE date < ?",
                       ((current - timedelta(days=SAMPLE_DAYS)).date().isoformat(),))
            db.commit()
        except BaseException:
            db.rollback()
            raise
    finally:
        db.close()
    return project_storage(samples, now=current, state_bytes=state_bytes, free_bytes=free_bytes,
                           reserve_bytes=reserve_bytes, max_state_bytes=max_state_bytes)


def heartbeat_projection(root, storage_report, *, now):
    """``heartbeat['storage_projection']`` from the same heartbeat's storage report.

    Never raises: a missing measurement or a sample store failure yields
    ``status: 'unavailable'`` with a reason code.
    """
    report = storage_report if isinstance(storage_report, dict) else {}
    limits = report.get("limits") if isinstance(report.get("limits"), dict) else {}
    values = {"state_bytes": _count(report.get("state_bytes")), "free_bytes": _count(report.get("free_bytes")),
              "reserve_bytes": _count(limits.get("reserve_bytes")),
              "max_state_bytes": _count(limits.get("max_state_bytes"))}
    if None in values.values():
        return _projection("unavailable", reason="storage_measurement_unavailable")
    try:
        return storage_projection(root, now, **values)
    except (OSError, ValueError, sqlite3.Error) as error:
        return _projection("unavailable", reason="projection_failed:" + type(error).__name__)
