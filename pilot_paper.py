"""Deterministic bootstrap and bounded paper session for the eight pilot accounts.

``--init-only`` creates the adaptive/control x 50/100/150/200 EUR account
cohort under a fresh ``.test-tmp*`` root. Each account is owned explicitly by
the deterministic worker and the four control strategies are frozen.

``--run`` activates that bootstrap for paper execution and runs a bounded
session: every cycle takes one shared sample from the public collector
(``hermes_trading.pilot_market``) and hands it to the canonical paired engine
(``paired_paper.run_paired_cycle``), which processes the eight accounts. After
a cycle the local fallback reflection (``reflect.run_fallback_reflection``)
proposes at most one variable for the adaptive primary; the change is
published causally (durable intent, archived base, receipt) so that only
candles closing after the publication use it. Controls are never reflected.

No live mode, no credentials, no model access and no Discord/Railway call:
the deterministic worker owns the strategy, so no ``model_access`` record is
created or required.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ARMS = ("adaptive", "control")
WALLETS_EUR = (50, 100, 150, 200)
ASSETS = ["ETH/EUR"]
TIMEFRAME = "1h"
DEFAULT_EXPERIMENT_ID = "pilot-paper"

BASE_STRATEGY = {
    "version": "1",
    "entry": {"indicator": "rsi", "threshold": "30", "direction": "long"},
    "stop_loss_pct": "2",
    "position_size_r": ".5",
    "simulation": {
        "maker_fee_rate": "0",
        "taker_fee_rate": "0",
        "slippage_rate": "0",
        "minimum_expected_edge_pct": "0",
        "max_order_quote": "5",
    },
}


def _validate_root(root: Path) -> Path:
    """A pilot root must be new and live under a .test-tmp* tree."""
    root = Path(root).resolve()
    if root.exists() or root.is_symlink():
        raise ValueError(f"root already exists: {root}")
    if not any(part.startswith(".test-tmp") for part in root.parts):
        raise ValueError("root must be inside a .test-tmp* directory")
    return root


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _strategy_for(arm: str) -> dict:
    strategy = copy.deepcopy(BASE_STRATEGY)
    if arm == "control":
        strategy["research"] = {"frozen": True}
    return strategy


def _reflection_owner(experiment_id: str) -> dict:
    return {
        "schema_version": 1,
        "owner": "deterministic_worker",
        "experiment_id": experiment_id,
        "cadence_closed_trades": 5,
        "one_variable_only": True,
    }


def _wallet_account(experiment_id: str, wallet: int, at: datetime) -> dict:
    return {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "currency": "EUR",
        "starting_balance": str(wallet),
        "activated_at": at.isoformat(),
    }


def _wallet_costs(experiment_id: str, at: datetime) -> dict:
    return {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "currency": "EUR",
        "scope_start": at.isoformat(),
        "as_of": at.isoformat(),
        "totals": {
            name: {"amount": "0", "source": "explicit_bootstrap_zero", "quality": "actual"}
            for name in ("hosting", "ai", "tools", "funding")
        },
    }


def init_pilot_accounts(
    root: Path,
    *,
    now: datetime | None = None,
    experiment_id: str = DEFAULT_EXPERIMENT_ID,
) -> dict:
    """Create the eight fresh EUR accounts and return the bootstrap receipt.

    The manifest records the cohort binding but stays non-activated
    (``execution_enabled`` is false and the status is not ``paper_ready``),
    so this bootstrap alone can never feed the collector or the worker.
    """
    root = _validate_root(root)
    if not isinstance(experiment_id, str) or not experiment_id.strip():
        raise ValueError("an explicit experiment identity is required")
    at = now or datetime.now(timezone.utc)
    if not isinstance(at, datetime) or at.tzinfo is None:
        raise ValueError("an aware activation time is required")
    at = at.astimezone(timezone.utc)
    root.mkdir(parents=True)
    profiles = []
    accounts = {}
    for arm in ARMS:
        for wallet in WALLETS_EUR:
            name = f"{arm}_{wallet}"
            account_dir = root / name
            account_dir.mkdir()
            _write_yaml(account_dir / "strategy.yaml", _strategy_for(arm))
            _write_yaml(account_dir / "goal.yaml", {"assets": ASSETS})
            _write_json(account_dir / "wallet_account.json", _wallet_account(experiment_id, wallet, at))
            _write_json(account_dir / "wallet_costs.json", _wallet_costs(experiment_id, at))
            _write_json(account_dir / "reflection_owner.json", _reflection_owner(experiment_id))
            digest = hashlib.sha256((account_dir / "strategy.yaml").read_bytes()).hexdigest()
            profiles.append(
                {"arm": arm, "wallet_eur": wallet, "directory": name, "strategy_sha256": digest}
            )
            accounts[name] = {
                "arm": arm,
                "wallet_eur": wallet,
                "directory": name,
                "currency": "EUR",
                "starting_balance": str(wallet),
                "activated_at": at.isoformat(),
                "owner": "deterministic_worker",
                "strategy_sha256": digest,
            }
    manifest = {
        "schema_version": 1,
        "status": "pilot_bootstrap",
        "execution_enabled": False,
        "assets": ASSETS,
        "timeframe": TIMEFRAME,
        "experiment_id": experiment_id,
        "profiles": profiles,
    }
    _write_json(root / "manifest.json", manifest)
    return {
        "schema_version": 1,
        "status": "initialized",
        "experiment_id": experiment_id,
        "activated_at": at.isoformat(),
        "root": str(root),
        "accounts": accounts,
        "collection": "not_started",
        "model_access": "not_configured",
    }


# ---------------------------------------------------------------------------
# Bounded paper session (LAT-32)
# ---------------------------------------------------------------------------

MAX_SESSION_CYCLES = 500
MAX_SESSION_SECONDS = 7 * 24 * 3600
MAX_INTERVAL_SECONDS = 24 * 3600
SESSION_REPORT = "pilot_session.json"
STOP_FILE = "STOP"
REFLECTED_VARIABLES = ("entry.threshold", "stop_loss_pct")
FALLBACK_WRITER = "deterministic_worker"


class PilotSessionError(RuntimeError):
    """The bounded session cannot start or continue safely."""


class PilotCollectionUnavailable(RuntimeError):
    """The public collector exhausted its finite attempts without a sample."""


def _validate_session_root(root: Path) -> Path:
    root = Path(root).resolve()
    if not any(part.startswith(".test-tmp") for part in root.parts):
        raise ValueError("root must be inside a .test-tmp* directory")
    if not (root / "manifest.json").is_file():
        raise ValueError(f"no pilot bootstrap in {root}; run --init-only first")
    return root


def _refuse_non_paper_mode(environ=None) -> None:
    environ = os.environ if environ is None else environ
    mode = environ.get("HERMES_TRADING_MODE", "").strip().lower()
    if mode and mode != "paper":
        raise PilotSessionError(f"pilot session is paper only (HERMES_TRADING_MODE={mode!r})")


def activate_pilot_manifest(root: Path) -> dict:
    """Turn the eight-account bootstrap into a paper-ready manifest, once.

    Only a ``pilot_bootstrap`` manifest written by :func:`init_pilot_accounts`
    is activated. The engine pins the manifest hash at the first cycle, so an
    activated manifest is never rewritten.
    """
    root = Path(root)
    path = root / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("status") == "paper_ready" and manifest.get("execution_enabled") is True:
        return manifest
    if manifest.get("status") != "pilot_bootstrap" or manifest.get("execution_enabled") is not False:
        raise PilotSessionError("manifest is neither a pilot bootstrap nor an activated pilot")
    expected = {(arm, wallet) for arm in ARMS for wallet in WALLETS_EUR}
    found = {(row.get("arm"), row.get("wallet_eur")) for row in manifest.get("profiles", [])}
    if found != expected or len(manifest.get("profiles", [])) != len(expected):
        raise PilotSessionError("pilot bootstrap must hold exactly the eight accounts")
    for row in manifest["profiles"]:
        strategy = root / row["directory"] / "strategy.yaml"
        if hashlib.sha256(strategy.read_bytes()).hexdigest() != row["strategy_sha256"]:
            raise PilotSessionError(f"{row['directory']} strategy differs from its bootstrap")
    for row in manifest["profiles"]:
        account_dir = root / row["directory"]
        sources = account_dir / "wallet_cost_sources.json"
        if not sources.exists():
            account = json.loads((account_dir / "wallet_account.json").read_text(encoding="utf-8"))
            _write_json(sources, _pilot_cost_sources(account))
    manifest = dict(manifest, status="paper_ready", execution_enabled=True, primary_wallet_eur=100)
    _write_json(path, manifest)
    return manifest


def _pilot_cost_sources(account: dict) -> dict:
    """Declared cost plan of a local paper pilot, read by the canonical accrual.

    The pilot runs locally with the deterministic fallback: no hosting bill,
    no model call, no paid tool and unleveraged spot. The zero daily rates are
    declared as ``estimated`` (never ``actual``) so the valuation keeps a
    current cost coverage without claiming a measured cost.
    """
    declared = {"mode": "recurring", "daily_rate": "0", "quality": "estimated"}
    return {
        "schema_version": 1,
        "experiment_id": account["experiment_id"],
        "currency": "EUR",
        "scope_start": account["activated_at"],
        "categories": {
            "hosting": dict(declared, source="pilot_local_paper_declared_zero"),
            "tools": dict(declared, source="pilot_local_paper_declared_zero"),
            "ai": dict(declared, source="pilot_deterministic_fallback_no_model"),
            "funding": {"mode": "spot_zero", "quality": "actual", "source": "unleveraged_spot_no_borrowing"},
        },
    }


class _CapturingQuotes:
    """Keep the snapshots the public collector returned for the engine."""

    def __init__(self, inner):
        self.inner = inner
        self.last = None

    async def collect(self, assets):
        result = await self.inner.collect(assets)
        self.last = result
        return result

    async def aclose(self):
        close = getattr(self.inner, "aclose", None)
        if close is not None:
            await close()


class PilotCycleMarket:
    """``collect(assets)`` adapter from the LAT-31 collector to the paired engine.

    Each engine collection is exactly one ``PilotMarketCollector.collect_once``
    sample (finite retries, hard per-attempt timeout, credentials refused);
    the snapshots of that sample are given to the eight accounts and the
    sample itself is kept as the cycle's provenance.
    """

    def __init__(self, pilot):
        self.pilot = pilot
        self._quotes = _CapturingQuotes(pilot._quote_collector())
        pilot._collector = self._quotes  # source stays as decided by the collector
        self.samples: list[dict] = []

    @property
    def source(self) -> str:
        return self.pilot.source

    async def collect(self, assets):
        if tuple(assets) != tuple(self.pilot.assets):
            raise PilotSessionError("engine assets differ from the pilot collector assets")
        self._quotes.last = None
        sample = await self.pilot.collect_once()
        self.samples.append(sample)
        snapshots = self._quotes.last
        if sample["status"] == "unavailable" or snapshots is None:
            raise PilotCollectionUnavailable(
                "public collection failed after %d attempt(s)" % len(sample["attempts"]))
        return snapshots

    async def aclose(self):
        await self.pilot.aclose()


def _strategy_bytes(strategy: dict) -> bytes:
    # Same serialization as paper_publication: the engine compares the active
    # strategy bytes with the receipt's candidate_sha256.
    return yaml.safe_dump(strategy, sort_keys=True).encode("utf-8")


def _leaf_changes(before, after, prefix=()):
    if isinstance(before, dict) and isinstance(after, dict):
        changes = []
        for key in sorted(set(before) | set(after), key=str):
            changes += _leaf_changes(before.get(key), after.get(key), prefix + (str(key),))
        return changes
    return [] if before == after else [".".join(prefix)]


def _complete_publication(state_dir: Path, db, review_id: str, request: dict, original: bytes, *, now) -> dict:
    """Archive the base, replace the strategy and commit the receipt (idempotent)."""
    from hermes_trading.paper_publication import _archive_once, _commit_receipt, _replace_strategy

    candidate_bytes = _strategy_bytes(request["candidate"])
    candidate_sha = hashlib.sha256(candidate_bytes).hexdigest()
    strategy_path = state_dir / "strategy.yaml"
    version = int(str(request["base_strategy"].get("version", "1")))
    _archive_once(state_dir / "history" / f"v{version:04d}.yaml", original)
    active = hashlib.sha256(strategy_path.read_bytes()).hexdigest()
    if active not in {request["base_sha256"], candidate_sha}:
        raise PilotSessionError("adaptive strategy diverged from the pending fallback publication")
    if active != candidate_sha:
        _replace_strategy(strategy_path, candidate_bytes, request["base_sha256"])
    if hashlib.sha256(strategy_path.read_bytes()).hexdigest() != candidate_sha:
        raise PilotSessionError("published fallback strategy could not be verified")
    receipt = {
        "schema_version": 1, "status": "published", "review_id": review_id,
        "experiment_id": request["experiment_id"], "request_hash": _hash(request),
        "base_sha256": request["base_sha256"], "candidate_sha256": candidate_sha,
        "goal_sha256": request["goal_sha256"], "policy_hash": request["policy_hash"],
        "writer": FALLBACK_WRITER, "published_at": now.isoformat(),
    }
    _commit_receipt(db, review_id, receipt)
    return receipt


def recover_fallback_publication(state_dir: Path, *, now) -> dict | None:
    """Finish a fallback publication whose durable intent has no receipt yet."""
    from hermes_trading.reflection_supervision import _connect

    path = Path(state_dir) / "paper_publications.sqlite"
    if not path.exists():
        return None
    db = _connect(path)
    try:
        table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='publications'").fetchone()
        rows = [] if table is None else db.execute(
            "SELECT review_id, request, base_bytes FROM publications WHERE receipt IS NULL").fetchall()
        if not rows:
            db.rollback()
            return None
        if len(rows) > 1:
            raise PilotSessionError("several unfinished fallback publications")
        review_id, raw, base_bytes = rows[0]
        request = json.loads(raw)
        if request.get("writer") != FALLBACK_WRITER:
            raise PilotSessionError("unfinished publication was not written by the fallback")
        original = base64.b64decode(base_bytes, validate=True)
        if hashlib.sha256(original).hexdigest() != request["base_sha256"]:
            raise PilotSessionError("unfinished publication base bytes are inconsistent")
        return _complete_publication(Path(state_dir), db, review_id, request, original, now=now)
    except BaseException:
        if db.in_transaction:
            db.rollback()
        raise
    finally:
        db.close()


def reflect_adaptive_primary(root: Path, *, now) -> dict:
    """Run the local fallback on the adaptive primary and publish it causally.

    ``run_fallback_reflection`` is executed on a scratch copy so the active
    strategy is only replaced after a durable intent exists. At most one
    variable may change (plus the version); controls are never touched.
    """
    from hermes_trading.paired_primary import resolve_primary
    from hermes_trading.paper_publication import assert_no_pending_paired_batch
    from hermes_trading.reflect import run_fallback_reflection
    from hermes_trading.reflection_supervision import _connect, assert_writer_owner

    root = Path(root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    primary = root / resolve_primary(manifest).profile_directory(manifest, "adaptive")
    policy = assert_writer_owner(primary, FALLBACK_WRITER)
    if policy is None:
        raise PilotSessionError("adaptive primary has no explicit deterministic owner")
    recovered = recover_fallback_publication(primary, now=now)
    if recovered is not None:
        return {"status": "recovered", "receipt": recovered}

    strategy_path = primary / "strategy.yaml"
    original = strategy_path.read_bytes()
    base = yaml.safe_load(original)
    with tempfile.TemporaryDirectory(prefix=".fallback-", dir=root) as scratch_name:
        scratch = Path(scratch_name)
        for name in ("strategy.yaml", "goal.yaml", "reflection_owner.json", "trades.jsonl"):
            if (primary / name).exists():
                shutil.copyfile(primary / name, scratch / name)
        outcome = run_fallback_reflection(scratch)
        if isinstance(outcome, dict) and outcome.get("status") == "frozen":
            return {"status": "frozen"}
        candidate = yaml.safe_load((scratch / "strategy.yaml").read_bytes())
        hypotheses = (scratch / "hypotheses.jsonl").read_text(encoding="utf-8").splitlines()
    hypothesis = json.loads(hypotheses[-1]) if hypotheses else {}
    changes = [path for path in _leaf_changes(base, candidate) if path != "version"]
    if not changes:
        return {"status": "no_change", "hypothesis": hypothesis}
    if len(changes) != 1 or changes[0] not in REFLECTED_VARIABLES:
        raise PilotSessionError(f"fallback must change exactly one supported variable, got {changes}")
    variable = changes[0]
    if str(candidate.get("version")) == str(base.get("version")):
        raise PilotSessionError("fallback candidate did not advance the strategy version")

    request = json.loads(_json_text({
        "schema_version": 1, "kind": "pilot_fallback_publication", "writer": FALLBACK_WRITER,
        "experiment_id": policy["experiment_id"], "policy_hash": _hash(policy),
        "goal_sha256": hashlib.sha256((primary / "goal.yaml").read_bytes()).hexdigest(),
        "base_sha256": hashlib.sha256(original).hexdigest(), "base_strategy": base,
        "candidate": candidate, "variable": variable, "asset": None, "hypothesis": hypothesis,
    }))
    review_id = str(uuid.uuid4())
    db = _connect(primary / "paper_publications.sqlite")
    try:
        assert_no_pending_paired_batch(primary, db=db)
        db.execute("CREATE TABLE IF NOT EXISTS publications (review_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, "
                   "request TEXT NOT NULL, base_bytes TEXT NOT NULL, receipt TEXT)")
        if strategy_path.read_bytes() != original:
            raise PilotSessionError("adaptive strategy changed during fallback reflection")
        db.execute("INSERT INTO publications VALUES (?,?,?,?,NULL)",
                   (review_id, _hash(request), _json_text(request), base64.b64encode(original).decode("ascii")))
        db.commit()  # Durable intent before any active strategy change.
        receipt = _complete_publication(primary, db, review_id, request, original, now=now)
    except BaseException:
        if db.in_transaction:
            db.rollback()
        raise
    finally:
        db.close()
    with (primary / "hypotheses.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(_json_text(dict(hypothesis, review_id=review_id, published_at=receipt["published_at"])) + "\n")
    return {"status": "published", "variable": variable, "before": hypothesis.get("before"),
            "after": hypothesis.get("after"), "from_version": str(base.get("version")),
            "to_version": str(candidate.get("version")), "receipt": receipt}


def _hash(value) -> str:
    from hermes_trading.reflection_supervision import _hash as canonical_hash
    return canonical_hash(value)


def _json_text(value) -> str:
    from hermes_trading.reflection_supervision import _json
    return _json(value)


def _trade_counts(root: Path, manifest: dict) -> dict:
    from hermes_trading.persistence.repository import StateRepository

    counts = {}
    for row in manifest["profiles"]:
        db_path = root / row["directory"] / "trading.db"
        if not db_path.exists():
            counts[row["directory"]] = {"entries": 0, "exits": 0}
            continue
        repo = StateRepository.open_read_only(db_path)
        try:
            counts[row["directory"]] = {"entries": len(repo.portfolio_positions()),
                                        "exits": len(repo.closed_positions())}
        finally:
            repo.close()
    return counts


def _check_session_bounds(max_cycles, max_runtime_seconds, interval_seconds, reflect_every) -> None:
    if type(max_cycles) is not int or not 1 <= max_cycles <= MAX_SESSION_CYCLES:
        raise ValueError(f"max_cycles must be an integer from 1 through {MAX_SESSION_CYCLES}")
    if type(max_runtime_seconds) not in (int, float) or not 0 < max_runtime_seconds <= MAX_SESSION_SECONDS:
        raise ValueError(f"max_runtime_seconds must be within (0, {MAX_SESSION_SECONDS}]")
    if type(interval_seconds) not in (int, float) or not 1 <= interval_seconds <= MAX_INTERVAL_SECONDS:
        raise ValueError(f"interval_seconds must be within [1, {MAX_INTERVAL_SECONDS}]")
    if type(reflect_every) is not int or reflect_every < 1:
        raise ValueError("reflect_every must be a positive integer")


async def run_pilot_session(
    root: Path,
    *,
    max_cycles: int,
    max_runtime_seconds: float,
    interval_seconds: float = 3600,
    reflect_every: int = 1,
    collector=None,
    clock=None,
    sleep=None,
    environ=None,
    collector_options: dict | None = None,
) -> dict:
    """Run at most ``max_cycles`` shared paper cycles within ``max_runtime_seconds``.

    Stops on the cycle limit, the runtime limit, a ``STOP`` file in the root,
    an engine halt, a collection failure or an error; cancellation (Ctrl-C) is
    recorded and re-raised. The collector is always closed and
    ``pilot_session.json`` is always written. ``collector``, ``clock`` and
    ``sleep`` are injection points for test doubles only.
    """
    from hermes_trading.paired_paper import PairedDeadlineReached, run_paired_cycle
    from hermes_trading.pilot_market import PilotMarketCollector, refuse_credentials

    _check_session_bounds(max_cycles, max_runtime_seconds, interval_seconds, reflect_every)
    _refuse_non_paper_mode(environ)
    refuse_credentials(environ)
    root = _validate_session_root(root)
    clock = clock or (lambda: datetime.now(timezone.utc))
    sleep = sleep or asyncio.sleep
    if (root / STOP_FILE).exists():
        raise PilotSessionError(f"remove {root / STOP_FILE} before starting a session")
    manifest = activate_pilot_manifest(root)

    pilot = PilotMarketCollector(collector=collector, assets=tuple(manifest["assets"]),
                                 timeframe=manifest["timeframe"], clock=clock, sleep=sleep,
                                 environ=environ, **(collector_options or {}))
    market = PilotCycleMarket(pilot)
    started = clock()
    deadline = started + timedelta(seconds=max_runtime_seconds)
    cycles: list[dict] = []
    stop = {"reason": "max_cycles_reached", "error": None}

    def cycle_sample(samples_before: int):
        return market.samples[-1] if len(market.samples) > samples_before else None

    def finish_cycle(record: dict, sample) -> None:
        if sample is not None:
            record["sample"] = {"fingerprint": sample["fingerprint"], "status": sample["status"],
                                "source": sample["source"],
                                "public_proof_eligible": sample["public_proof_eligible"],
                                "attempts": len(sample["attempts"])}
        cycles.append(record)

    try:
        async with asyncio.timeout(max_runtime_seconds):
            for index in range(max_cycles):
                if (root / STOP_FILE).exists():
                    stop["reason"] = "stop_requested"
                    break
                at = clock()
                if at >= deadline:
                    stop["reason"] = "max_runtime_reached"
                    break
                record = {"cycle": index + 1, "started_at": at.isoformat()}
                samples_before = len(market.samples)
                try:
                    result = await run_paired_cycle(root, market, now=clock, not_after=deadline)
                except PairedDeadlineReached:
                    record["status"] = "deadline_before_packet"
                    finish_cycle(record, cycle_sample(samples_before))
                    stop["reason"] = "max_runtime_reached"
                    break
                except PilotCollectionUnavailable as error:
                    record.update(status="collection_unavailable", error=str(error))
                    finish_cycle(record, cycle_sample(samples_before))
                    stop.update(reason="collection_unavailable", error=str(error))
                    break
                record.update(status=result["status"], batch_id=result["batch_id"],
                              input_hash=result["input_hash"],
                              accounts=sorted(f"{row['arm']}_{row['wallet_eur']}" for row in result["accounts"]))
                sample = cycle_sample(samples_before)
                if result["status"] == "complete" and (index + 1) % reflect_every == 0:
                    if sample is not None and sample["status"] == "available":
                        record["reflection"] = reflect_adaptive_primary(root, now=clock())
                    else:
                        record["reflection"] = {"status": "skipped", "reason": "market_sample_not_available"}
                finish_cycle(record, sample)
                if result["status"] != "complete":
                    stop["reason"] = "engine_halted"
                    break
                if index + 1 >= max_cycles:
                    break
                remaining = (deadline - clock()).total_seconds()
                if remaining <= 0:
                    stop["reason"] = "max_runtime_reached"
                    break
                await sleep(min(interval_seconds, remaining))
    except TimeoutError:
        stop["reason"] = "hard_runtime_limit"
    except asyncio.CancelledError:
        stop["reason"] = "interrupted"
        raise
    except Exception as error:  # noqa: BLE001 - recorded in the report, then re-raised
        stop.update(reason="error", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        try:
            await market.aclose()
        finally:
            report = _session_report(root, manifest, market, cycles, stop, started, clock(),
                                     max_cycles=max_cycles, max_runtime_seconds=max_runtime_seconds,
                                     interval_seconds=interval_seconds, reflect_every=reflect_every)
    return report


def _session_report(root, manifest, market, cycles, stop, started, finished, **bounds) -> dict:
    counts = _trade_counts(root, manifest)
    entries = sum(row["entries"] for row in counts.values())
    exits = sum(row["exits"] for row in counts.values())
    public = market.source == "kraken_public_unauthenticated"
    eligible_samples = sum(1 for sample in market.samples if sample["public_proof_eligible"])
    if not public:
        proof = "not_eligible_injected_collector"
    elif eligible_samples == 0:
        proof = "not_eligible_no_public_sample"
    elif entries == 0 or exits == 0:
        proof = "not_eligible_zero_trade"
    else:
        proof = "candidate_requires_review"
    report = {
        "schema_version": 1,
        "kind": "pilot_paper_session",
        "mode": "paper",
        "authenticated": False,
        "model_access": "not_used",
        "reflection_writer": FALLBACK_WRITER,
        "experiment_id": manifest.get("experiment_id"),
        "manifest_sha256": hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest(),
        "source": market.source,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "bounds": bounds,
        "stop_reason": stop["reason"],
        "error": stop["error"],
        "collector_closed": market.pilot._closed,
        "completed_cycles": sum(1 for row in cycles if row.get("status") == "complete"),
        "cycles": cycles,
        "samples": len(market.samples),
        "public_proof_eligible_samples": eligible_samples,
        "trade_counts": counts,
        "entries": entries,
        "exits": exits,
        "public_proof": proof,
    }
    _write_json(root / SESSION_REPORT, json.loads(_json_text(report)))
    return report


def main(argv=None, *, collector=None, clock=None, sleep=None, environ=None) -> None:
    """CLI. ``collector``/``clock``/``sleep``/``environ`` are test-double hooks only."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", required=True, help="pilot root under a .test-tmp* directory")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--init-only", action="store_true",
                      help="only initialize the eight accounts in a new root")
    mode.add_argument("--run", action="store_true",
                      help="activate an initialized root and run a bounded paper session")
    parser.add_argument("--experiment-id", default=DEFAULT_EXPERIMENT_ID)
    parser.add_argument("--max-cycles", type=int, help=f"required with --run, at most {MAX_SESSION_CYCLES}")
    parser.add_argument("--max-runtime-seconds", type=float,
                        help=f"required with --run, at most {MAX_SESSION_SECONDS}")
    parser.add_argument("--interval-seconds", type=float, default=3600.0)
    parser.add_argument("--reflect-every", type=int, default=1, help="fallback reflection every N cycles")
    parser.add_argument("--max-attempts", type=int, default=3, help="public collection attempts per cycle")
    args = parser.parse_args(argv)
    if not (args.init_only or args.run):
        print("error: choose --init-only or --run", file=sys.stderr, flush=True)
        raise SystemExit(1)
    if args.init_only:
        try:
            receipt = init_pilot_accounts(Path(args.root), experiment_id=args.experiment_id)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr, flush=True)
            raise SystemExit(1)
        print(json.dumps(receipt, indent=2, sort_keys=True))
        return
    if args.max_cycles is None or args.max_runtime_seconds is None:
        print("error: --run requires --max-cycles and --max-runtime-seconds", file=sys.stderr, flush=True)
        raise SystemExit(2)
    session = run_pilot_session(
        Path(args.root), max_cycles=args.max_cycles, max_runtime_seconds=args.max_runtime_seconds,
        interval_seconds=args.interval_seconds, reflect_every=args.reflect_every,
        collector=collector, clock=clock, sleep=sleep, environ=environ,
        collector_options={"max_attempts": args.max_attempts})
    try:
        report = asyncio.run(session)
    except (ValueError, PermissionError, PilotSessionError) as exc:
        print(f"refused: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(2)
    except KeyboardInterrupt:
        print("interrupted: session report written", file=sys.stderr, flush=True)
        raise SystemExit(130)
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["stop_reason"] not in {"max_cycles_reached", "max_runtime_reached", "stop_requested"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
