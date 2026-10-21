"""Version-checked, retry-safe publication on the paper worker filesystem.

The transport invokes this helper on the worker; it never submits orders.
Filesystem source checks require a separately verified release/restart.
"""
import argparse
import base64
import hashlib
import json
import os
import sqlite3
from pathlib import Path
import sys
import tempfile
import uuid

import yaml

from hermes_trading.reflect import (
    _get_nested, _next_version, _variable_path,
    STRATEGY_VARIABLE_BOUNDS,
)
from hermes_trading.paper_strategy_proposal import apply_paper_strategy_proposal
from hermes_trading.reflection_supervision import (
    ReviewLease, _connect, _hash, _json, _now, _time, assert_writer_owner,
)

GUARD_FILES = ("loop.py", "reflect.py", "reflection_supervision.py", "paper_publication.py", "supervisor_sync.py")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _release_hashes():
    package = Path(__file__).resolve().parent
    # Bind the whole Python runtime, including accounting/position contracts,
    # rather than only the entrypoints that own strategy writes.
    return {path.relative_to(package).as_posix(): _sha(path.read_bytes())
            for path in sorted(package.rglob("*.py"))}


def build_publication_request(review, policy, *, writer_release=None):
    decision, request = review.get("decision", {}), review.get("request", {})
    if review.get("outcome") != "pending" or decision.get("status") != "awaiting_publication":
        raise ValueError("review has no publishable pending candidate")
    return json.loads(_json({
        "schema_version": 1, "review_id": review["review_id"], "experiment_id": policy["experiment_id"],
        "policy_hash": _hash(policy), "goal_sha256": request["goal_sha256"],
        "base_sha256": request["strategy_sha256"], "base_strategy": request["strategy"],
        "candidate": decision["candidate"], "variable": decision["variable"], "asset": decision["asset"],
        "review": review, "writer_release": _release_hashes() if writer_release is None else writer_release,
    }))


def _validate_request(state_dir, request, env, *, writer_release=None):
    if env.get("HERMES_TRADING_MODE", "").strip().lower() not in {"paper", "training", "train", "demo"}:
        raise ValueError("publication requires an explicit paper runtime")
    policy = assert_writer_owner(state_dir, "external_llm")
    if policy is None or request.get("schema_version") != 1:
        raise ValueError("publication requires an explicit experiment and request schema")
    if request.get("experiment_id") != policy["experiment_id"] or request.get("policy_hash") != _hash(policy):
        raise ValueError("publication owner policy changed")
    expected_release=_release_hashes() if writer_release is None else writer_release
    if request.get("writer_release") != expected_release:
        raise ValueError("worker writer-guard release differs from the reviewed release")
    if _sha((state_dir / "goal.yaml").read_bytes()) != request.get("goal_sha256"):
        raise ValueError("publication goal changed")
    uuid.UUID(request["review_id"])
    base, candidate = request["base_strategy"], request["candidate"]
    review = request["review"]
    if _json(build_publication_request(review, policy,writer_release=expected_release)) != _json(request):
        raise ValueError("publication request differs from its immutable review")
    response = review.get("response", {})
    if (response.get("provider"), response.get("model")) != (policy["model"]["provider"], policy["model"]["name"]):
        raise ValueError("publication review did not use the pinned model")
    if review["decision"].get("based_on_sha256") != request["base_sha256"]:
        raise ValueError("publication decision differs from its reviewed base")
    variable, asset = request["variable"], request["asset"]
    if variable not in STRATEGY_VARIABLE_BOUNDS or variable == "position_size_r":
        raise ValueError("publication variable is unsupported in paper mode")
    target = candidate if asset is None else candidate["symbols"][asset]
    value = _get_nested(target, _variable_path(variable))
    goal = yaml.safe_load((state_dir / 'goal.yaml').read_bytes())
    assets = goal.get('assets', [goal['asset']] if goal.get('asset') else None)
    expected, _, _ = apply_paper_strategy_proposal(
        base, {"variable": variable, "asset": asset, "value": value}, assets=assets)
    expected["version"] = _next_version(str(base.get("version", "01")))
    if _json(expected) != _json(candidate):
        raise ValueError("candidate changes more than its declared variable and version")
    if _json(dict(candidate, version=base.get("version"))) == _json(base):
        raise ValueError("publication candidate has no effective change")
    return policy


def _sync_dir(path):
    if os.name == "posix":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)


def _temporary(path, data, *, stat=None):
    fd, name = tempfile.mkstemp(prefix=".publication-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            if stat is not None:
                os.chmod(temporary, stat.st_mode & 0o777)
                if os.name == "posix": os.chown(temporary, stat.st_uid, stat.st_gid)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _archive_once(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("prior-version archive conflicts with publication")
        return
    temporary = _temporary(path, data)
    try:
        try: os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ValueError("prior-version archive conflicts with publication")
        _sync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _replace_strategy(path, data, expected_hash):
    temporary = _temporary(path, data, stat=path.stat())
    try:
        if _sha(path.read_bytes()) != expected_hash:
            raise ValueError("strategy changed before publication replace")
        os.replace(temporary, path)
        _sync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _commit_receipt(db, review_id, receipt):
    if not db.in_transaction: db.execute("BEGIN EXCLUSIVE")
    db.execute("UPDATE publications SET receipt=? WHERE review_id=?", (_json(receipt), review_id))
    db.commit()


def assert_no_pending_paired_batch(state_dir, *, db=None):
    """A crashed common-input batch retains its publication boundary."""
    from hermes_trading.paired_primary import read_primary_context
    context=read_primary_context(state_dir)
    if (Path(state_dir)/'experiment_halt.json').exists():
        raise ValueError('durable experiment storage halt blocks publication and steering')
    owned=db is None
    if owned:
        path=Path(state_dir)/'paper_publications.sqlite'
        if not path.exists():
            if context is not None:raise ValueError('selected primary journal is missing')
            return
        db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=0)
    try:
        from hermes_trading.archive_quote_state import assert_quote_retention_complete,assert_quote_cohort
        assert_quote_retention_complete(db)
        if context is not None:assert_quote_cohort(Path(state_dir).parent,db=db,manifest=context[0])
        if context is not None:
            manifest,primary=context
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='paired_identity'").fetchone():
                raise ValueError('selected primary manifest identity is missing')
            identity=db.execute('SELECT manifest_hash FROM paired_identity WHERE id=1').fetchone()
            if identity is None or identity[0]!=_hash(manifest):raise ValueError('selected primary manifest changed')
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='paired_batches'").fetchone()
        if exists and db.execute("SELECT 1 FROM paired_batches WHERE status='pending' LIMIT 1").fetchone():
            raise ValueError('unfinished paired batch requires recovery before publication or review')
        risk_table=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='paired_risk_state'").fetchone()
        if context is not None and not risk_table:raise ValueError('selected primary risk state is missing')
        if risk_table:
            row=db.execute('SELECT state FROM paired_risk_state WHERE id=1').fetchone()
            risk=None if row is None else json.loads(row[0])
            if context is not None:
                from hermes_trading.paired_risk import validate_risk_state
                validate_risk_state(risk,primary)
            if risk is None or risk.get('global_halted') is not False:
                raise ValueError('primary paper risk halt blocks publication and steering')
    except sqlite3.OperationalError as error:
        raise ValueError('paired batch/publication lease is busy') from error
    finally:
        if owned:db.close()


def publish_paper_request(state_dir, request, *, env=None, now=None):
    state_dir = Path(state_dir)
    env = os.environ if env is None else env
    _validate_request(state_dir, request, env)
    candidate_bytes = yaml.safe_dump(request["candidate"], sort_keys=True).encode("utf-8")
    candidate_hash, request_hash = _sha(candidate_bytes), _hash(request)
    review_id, strategy_path = request["review_id"], state_dir / "strategy.yaml"
    db = _connect(state_dir / "paper_publications.sqlite")
    try:
        assert_no_pending_paired_batch(state_dir,db=db)
        db.execute("CREATE TABLE IF NOT EXISTS publications (review_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, request TEXT NOT NULL, base_bytes TEXT NOT NULL, receipt TEXT)")
        stored = db.execute("SELECT request_hash, base_bytes, receipt FROM publications WHERE review_id=?", (review_id,)).fetchone()
        active = strategy_path.read_bytes()
        if stored is not None and stored[0] != request_hash:
            raise ValueError("publication review identity cannot be rewritten")
        if stored is None:
            if _sha(active) != request["base_sha256"] or _json(yaml.safe_load(active)) != _json(request["base_strategy"]):
                raise ValueError("publication base strategy changed")
            original = active
            db.execute("INSERT INTO publications VALUES (?,?,?,?,NULL)",
                       (review_id, request_hash, _json(request), base64.b64encode(original).decode("ascii")))
            db.commit()  # Durable intent before any active strategy change.
        else:
            original = base64.b64decode(stored[1], validate=True)
            if _sha(original) != request["base_sha256"]:
                raise ValueError("publication intent's prior bytes are inconsistent")
            if _sha(active) not in {request["base_sha256"], candidate_hash}:
                raise ValueError("active strategy diverged from the pending publication")
            if stored[2] is not None:
                if _sha(active) != candidate_hash:
                    raise ValueError("completed publication no longer matches active strategy")
        version = int(str(request["base_strategy"].get("version", "01")))
        _archive_once(state_dir / "history" / f"v{version:04d}.yaml", original)
        # Recheck policy/goal/release under the publication lease.
        _validate_request(state_dir, request, env)
        if stored is not None and stored[2] is not None:
            return json.loads(stored[2])
        if _sha(strategy_path.read_bytes()) != candidate_hash:
            _replace_strategy(strategy_path, candidate_bytes, request["base_sha256"])
        if _sha(strategy_path.read_bytes()) != candidate_hash:
            raise ValueError("published strategy hash could not be verified")
        receipt = {"schema_version": 1, "status": "published", "review_id": review_id,
                   "experiment_id": request["experiment_id"], "request_hash": request_hash,
                   "base_sha256": request["base_sha256"], "candidate_sha256": candidate_hash,
                   "goal_sha256": request["goal_sha256"], "policy_hash": request["policy_hash"],
                   "published_at": _now(now).isoformat()}
        _commit_receipt(db, review_id, receipt)
        return receipt
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def publish_pending_review(state_dir, transport, *, now=None):
    with ReviewLease(state_dir) as lease:
        review = lease.pending_review()
        if review is None:
            return {"status": "not_due", "reason": "no_pending_review"}
        request = review.get("publication_intent")
        if request is None:
            request = build_publication_request(review, lease.policy)
            lease.record_material(review["review_id"], publication_intent=request, now=_now(now))
        receipt = transport(request)  # Any unknown outcome leaves review pending.
        expected = {"schema_version": 1, "status": "published", "review_id": request["review_id"],
                    "experiment_id": request["experiment_id"], "request_hash": _hash(request),
                    "base_sha256": request["base_sha256"],
                    "candidate_sha256": _sha(yaml.safe_dump(request["candidate"], sort_keys=True).encode("utf-8")),
                    "goal_sha256": request["goal_sha256"], "policy_hash": request["policy_hash"]}
        if not isinstance(receipt, dict) or any(receipt.get(key) != value for key, value in expected.items()):
            raise ValueError("publication receipt does not confirm this candidate")
        if _time(receipt.get("published_at")) < _time(review["started_at"]):
            raise ValueError("publication receipt precedes its review")
        lease.complete(review["review_id"], outcome="changed", provider=review["response"]["provider"],
                       model=review["response"]["model"], publication=receipt, now=_now(now))
        return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True)
    args = parser.parse_args()
    print(_json(publish_paper_request(Path(args.state_dir), json.load(sys.stdin))))


if __name__ == "__main__": main()
