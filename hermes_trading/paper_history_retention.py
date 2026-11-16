"""Thirty-day detail retention with verified gzip archives and permanent daily summaries.

The worker calls this after a successful daily backup.  Detail older than
``now - 30 days`` is archived, verified, and only then removed.  A row exactly
at the cutoff is kept.  Daily summaries are computed before deletion and kept
indefinitely so archived days remain visible.

No source state is read outside the supplied root; no live mode, secret or
network call is used.
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

from hermes_trading.archive_snapshot import _safe_root
from hermes_trading.paper_publication import _sync_dir, _temporary
from hermes_trading.paired_packet_storage import load_packet, store_record
from hermes_trading.paired_primary import resolve_primary
from hermes_trading.reflection_supervision import _hash, _json, _now, _time

RETENTION_DAYS = 30
PURPOSE = "paper_history_retention_v1"
RECEIPT_SCHEMA = 1
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
SUMMARY_DB = ".history-retention" / Path("daily_summaries.sqlite")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _timestamp(value):
    if isinstance(value, datetime):
        return value
    return _time(value)


def _cutoff(now):
    current = _now(now).astimezone(timezone.utc)
    return current - timedelta(days=RETENTION_DAYS)


def _archive_dir(root):
    directory = _safe_root(root / ".history-retention")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _daily(date):
    return date.astimezone(timezone.utc).date().isoformat()


def _ensure_summary_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=0, isolation_level=None)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            """CREATE TABLE IF NOT EXISTS daily_summaries (
                account TEXT NOT NULL,
                date TEXT NOT NULL,
                balance TEXT,
                decisions INTEGER,
                costs TEXT,
                payload TEXT NOT NULL,
                PRIMARY KEY (account, date)
            )"""
        )
        db.commit()
    except BaseException:
        db.rollback()
        db.close()
        raise
    return db


def _summarize_observation(observation):
    """Extract the daily summary fields from one wallet observation."""
    net_equity = observation.get("net_equity")
    known_cost_equity = observation.get("known_cost_equity")
    # Prefer the cost-bearing equity figure when available.
    if known_cost_equity is not None:
        balance = str(known_cost_equity)
    elif net_equity is not None:
        balance = str(net_equity)
    else:
        balance = None
    costs = observation.get("known_accrued_costs")
    return {"balance": balance, "costs": str(costs) if costs is not None else None}


def _update_summaries(db, account, observations):
    """Merge observations into the permanent daily summary table."""
    by_date = {}
    for observation in observations:
        stamp = _timestamp(observation.get("at"))
        date = _daily(stamp)
        if date not in by_date:
            by_date[date] = []
        by_date[date].append(observation)
    for date, rows in by_date.items():
        # Last observation of the day carries the end-of-day balance.
        last = rows[-1]
        summary = _summarize_observation(last)
        summary["decisions"] = len(rows)
        payload = _json({"date": date, **summary})
        db.execute(
            """INSERT INTO daily_summaries(account,date,balance,decisions,costs,payload)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(account,date) DO UPDATE SET
               balance=excluded.balance, decisions=excluded.decisions,
               costs=excluded.costs, payload=excluded.payload""",
            (account, date, summary["balance"], summary["decisions"], summary["costs"], payload),
        )


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


def _archive_content(rows, archive_path, receipt_path, kind, fault):
    """Create a verified gzip archive and its receipt for the supplied rows."""
    if not rows:
        return None
    original_bytes = _encode_rows(rows)
    original_sha256 = _sha(original_bytes)
    _gzip_bytes(original_bytes, archive_path, fault)
    _verify_gzip_archive(archive_path, original_bytes, original_sha256)
    record = {
        "schema_version": RECEIPT_SCHEMA,
        "purpose": PURPOSE,
        "kind": kind,
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "row_count": len(rows),
        "original_bytes": len(original_bytes),
        "original_sha256": original_sha256,
        "archive_sha256": _sha(archive_path.read_bytes()),
    }
    _write_receipt(receipt_path, record)
    return record


def _retain_wallet_observations(account_dir, cutoff, archive_dir, summary_db, fault):
    """Archive wallet observations older than ``cutoff`` and update daily summaries."""
    db_path = _safe_root(account_dir / "wallet_equity.sqlite")
    if not db_path.is_file():
        return {"kept": 0, "archived": 0}
    archive_name = f"{account_dir.name}_wallet_equity_{cutoff.date().isoformat()}.jsonl.gz"
    archive_path = archive_dir / archive_name
    receipt_path = archive_path.with_suffix(".receipt.json")

    db = sqlite3.connect(db_path, timeout=0, isolation_level=None)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='observations'"
        ).fetchone() is None:
            db.commit()
            return {"kept": 0, "archived": 0}

        cutoff_text = cutoff.isoformat()
        all_rows = db.execute(
            "SELECT at, payload FROM observations ORDER BY at"
        ).fetchall()
        all_observations = []
        for stamp, payload in all_rows:
            observation = load_packet(db, payload)
            observation["at"] = stamp
            all_observations.append(observation)
        _update_summaries(summary_db, account_dir.name, all_observations)

        old_rows = [row for row in all_rows if row[0] < cutoff_text]
        old_observations = [obs for obs in all_observations if _timestamp(obs["at"]) < cutoff]

        if old_observations:
            if archive_path.exists() or receipt_path.exists():
                raise ValueError("retention archive for this cutoff already exists")
            record = _archive_content(
                old_observations, archive_path, receipt_path,
                f"wallet_equity:{account_dir.name}", fault
            )
            if record is None:
                raise ValueError("retention archive creation failed")
            db.executemany("DELETE FROM observations WHERE at = ?", ((row[0],) for row in old_rows))
        db.commit()
        kept = db.execute(
            "SELECT COUNT(*) FROM observations WHERE at >= ?", (cutoff_text,)
        ).fetchone()[0]
        return {"kept": kept, "archived": len(old_observations)}
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def _packet_created_at(db, payload):
    """Return the created_at timestamp of a packet payload, or None."""
    try:
        packet = load_packet(db, payload)
    except (ValueError, TypeError, KeyError):
        return None
    return packet.get("created_at")


def _retain_publication_batches(primary_dir, cutoff, archive_dir, fault):
    """Archive paired batches older than ``cutoff``."""
    db_path = _safe_root(primary_dir / "paper_publications.sqlite")
    if not db_path.is_file():
        return {"kept": 0, "archived": 0}
    archive_name = f"paper_publications_{cutoff.date().isoformat()}.jsonl.gz"
    archive_path = archive_dir / archive_name
    receipt_path = archive_path.with_suffix(".receipt.json")

    db = sqlite3.connect(db_path, timeout=0, isolation_level=None)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='paired_batches'"
        ).fetchone() is None:
            db.commit()
            return {"kept": 0, "archived": 0}

        cutoff_text = cutoff.isoformat()
        rows = db.execute(
            "SELECT batch_id, status, payload, input_hash, completed, rowid FROM paired_batches ORDER BY rowid"
        ).fetchall()
        old_rows = []
        kept = 0
        for batch_id, status, payload, input_hash, completed, rowid in rows:
            created_at = _packet_created_at(db, payload)
            if created_at is None or _time(created_at) >= cutoff:
                kept += 1
                continue
            packet = load_packet(db, payload)
            completed_packet = load_packet(db, completed) if completed else None
            old_rows.append(
                {
                    "batch_id": batch_id,
                    "status": status,
                    "packet": packet,
                    "input_hash": input_hash,
                    "completed_packet": completed_packet,
                    "rowid": rowid,
                }
            )

        if old_rows:
            if archive_path.exists() or receipt_path.exists():
                raise ValueError("retention archive for this cutoff already exists")
            record = _archive_content(
                old_rows, archive_path, receipt_path, "paper_publications", fault
            )
            if record is None:
                raise ValueError("retention archive creation failed")
            # Remove archived rows in reverse rowid order to avoid shifting.
            for row in sorted(old_rows, key=lambda r: r["rowid"], reverse=True):
                db.execute("DELETE FROM paired_batches WHERE rowid = ?", (row["rowid"],))
        db.commit()
        return {"kept": kept, "archived": len(old_rows)}
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def apply_retention(root, now=None, *, backup_verified=True, fault=None):
    """Archive and remove detail older than 30 days.

    ``backup_verified`` must be true only after the daily backup of ``now`` has
    been verified.  If it is false, the function refuses to delete anything.
    """
    root = _safe_root(Path(root).absolute())
    if backup_verified is not True:
        raise ValueError("retention refused: daily backup of the day is not verified")
    current = _now(now).astimezone(timezone.utc)
    cutoff = _cutoff(current)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    selection = resolve_primary(manifest)
    primary_dir = root / selection.profile_directory(manifest, "adaptive")
    archive_dir = _archive_dir(root)
    summary_db_path = _safe_root(root / SUMMARY_DB)
    summary_db = _ensure_summary_db(summary_db_path)
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
                account_dir, cutoff, archive_dir, summary_db, fault
            )
        result["publications"] = _retain_publication_batches(
            primary_dir, cutoff, archive_dir, fault
        )
        _sync_dir(archive_dir)
        return result
    finally:
        summary_db.close()


def daily_summaries(root):
    """Return permanent daily summaries for every account.

    The result is ``{account_directory: [{date, balance, decisions, costs}, ...]}``
    with one row per UTC day of activity, archived days included.
    """
    root = _safe_root(Path(root).absolute())
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    summary_db_path = _safe_root(root / SUMMARY_DB)
    summaries = {}
    for profile in manifest.get("profiles", []):
        account = profile["directory"]
        summaries[account] = []
        if summary_db_path.is_file():
            db = sqlite3.connect(summary_db_path, timeout=0)
            try:
                for date, balance, decisions, costs in db.execute(
                    "SELECT date, balance, decisions, costs FROM daily_summaries WHERE account = ? ORDER BY date",
                    (account,),
                ):
                    summaries[account].append(
                        {"date": date, "balance": balance, "decisions": decisions, "costs": costs}
                    )
            finally:
                db.close()
    return summaries


def restore_wallet_archive(archive_path):
    """Return the byte-identical archived observations for inspection or replay."""
    archive_path = _safe_root(Path(archive_path))
    receipt_path = archive_path.with_suffix(".receipt.json")
    if not receipt_path.is_file():
        raise ValueError("retention archive receipt is missing")
    record = json.loads(receipt_path.read_text(encoding="utf-8"))
    original_bytes = _encode_rows(_decode_archive(archive_path))
    if len(original_bytes) != record.get("original_bytes") or _sha(original_bytes) != record.get("original_sha256"):
        raise ValueError("restored archive content differs from receipt")
    return original_bytes


def _decode_archive(archive_path):
    """Decode a gzip archive of JSON-lines rows."""
    rows = []
    with gzip.open(archive_path, "rb") as source:
        for line in source:
            if not line.strip():
                continue
            rows.append(json.loads(line.decode("utf-8")))
    return rows
