"""Explicit reflection ownership and durable LLM review accounting.

No network/model/strategy mutation is performed here. The SQLite lease covers
cooperating processes on one state directory; remote publication needs CAS.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid
from hermes_trading.operational_exits import RECOVERY_EXIT_REASONS

UTC = timezone.utc
OWNERS = {"external_llm", "deterministic_worker"}


class WriterNotAuthorized(RuntimeError):
    pass


class ReviewBusy(RuntimeError):
    pass


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _time(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _now(value=None):
    current = value or datetime.now(UTC)
    if not isinstance(current, datetime) or current.tzinfo is None:
        raise ValueError("an aware observation time is required")
    return current.astimezone(UTC)


def load_owner_policy(state_dir):
    path = Path(state_dir) / "reflection_owner.json"
    if not path.exists():
        return None  # Legacy behavior until the controlled paper release.
    policy = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(policy, dict):
        raise ValueError("reflection policy must be an object")
    if type(policy.get("schema_version")) is not int or policy["schema_version"] != 1:
        raise ValueError("unsupported reflection policy schema")
    if policy.get("owner") not in OWNERS:
        raise ValueError("unknown reflection owner")
    if not isinstance(policy.get("experiment_id"), str) or not policy["experiment_id"].strip():
        raise ValueError("an explicit experiment identity is required")
    if policy.get("cadence_closed_trades") != 5 or policy.get("one_variable_only") is not True:
        raise ValueError("recovery policy requires five trades and one variable")
    if 'model_access_renewal_required' in policy and type(policy['model_access_renewal_required']) is not bool:
        raise ValueError('model access renewal requirement must be a boolean')
    if 'supervisor_lifecycle_required' in policy and type(policy['supervisor_lifecycle_required']) is not bool:
        raise ValueError('supervisor lifecycle requirement must be a boolean')
    if policy["owner"] == "external_llm":
        model = policy.get("model")
        if not isinstance(model, dict) or any(not isinstance(model.get(k), str) or not model[k].strip() for k in ("provider", "name")):
            raise ValueError("the LLM provider and model must be pinned")
        if model["provider"] in {"deterministic", "systemic_accessibility_guard"}:
            raise ValueError("deterministic activity is not an LLM review")
    return policy


def writer_allowed(state_dir, writer):
    if writer not in OWNERS:
        raise ValueError("unknown reflection actor")
    policy = load_owner_policy(state_dir)
    return policy is None or policy["owner"] == writer


def assert_writer_owner(state_dir, writer):
    if not writer_allowed(state_dir, writer):
        raise WriterNotAuthorized(f"reflection actor {writer} does not own strategy changes")
    return load_owner_policy(state_dir)


def _connect(path):
    db = sqlite3.connect(path, timeout=0, isolation_level=None)
    try:
        db.execute("PRAGMA locking_mode=EXCLUSIVE")
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN EXCLUSIVE")
    except BaseException:
        db.close()
        raise
    return db


def initialize_supervisor(state_dir, *, now=None):
    state_dir = Path(state_dir)
    policy = assert_writer_owner(state_dir, "external_llm")
    if policy is None:
        raise ValueError("supervisor initialization requires an explicit owner policy")
    current = _now(now)
    db = _connect(state_dir / "llm_review_state.sqlite")
    try:
        db.execute("CREATE TABLE IF NOT EXISTS supervisor (id INTEGER PRIMARY KEY CHECK(id=1), state TEXT NOT NULL)")
        if db.execute("SELECT state FROM supervisor WHERE id=1").fetchone() is not None:
            raise ValueError("existing experiment cannot be reinitialized")
        db.execute("CREATE TABLE IF NOT EXISTS reviews (review_id TEXT PRIMARY KEY, record TEXT NOT NULL)")
        state = {"schema_version": 1, "experiment_id": policy["experiment_id"], "policy_hash": _hash(policy),
                 "activated_at": current.isoformat(), "completed_trade_ids": [],
                 "last_completed_review_at": None, "pending_review": None, "retry_not_before": None}
        db.execute("INSERT INTO supervisor VALUES (1,?)", (_json(state),))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


class ReviewLease:
    def __init__(self, state_dir):
        self.state_dir = Path(state_dir)
        self.db = None

    def __enter__(self):
        self.policy = assert_writer_owner(self.state_dir, "external_llm")
        path = self.state_dir / "llm_review_state.sqlite"
        if self.policy is None or not path.is_file():
            raise ValueError("supervisor has not been explicitly initialized")
        try:
            self.db = _connect(path)
            row = self.db.execute("SELECT state FROM supervisor WHERE id=1").fetchone()
            if row is None:
                raise ValueError("supervisor state is missing")
            self._state = json.loads(row[0])
            self._check_policy()
        except BaseException as exc:
            if self.db is not None:
                self.db.close()
                self.db = None
            if isinstance(exc, sqlite3.OperationalError) and ("locked" in str(exc) or "busy" in str(exc)):
                raise ReviewBusy("another supervisor owns the review lease") from exc
            raise
        return self

    def _check_policy(self):
        current = assert_writer_owner(self.state_dir, "external_llm")
        if current is None or self._state.get("schema_version") != 1 or self._state.get("policy_hash") != _hash(current):
            raise ValueError("experiment policy changed; explicit reconciliation is required")
        self.policy = current

    def _save(self, review, state, *, insert=False):
        # Every transition commits its audit record and trade window together.
        # locking_mode=EXCLUSIVE retains the process lease between transactions.
        try:
            if not self.db.in_transaction:
                self.db.execute("BEGIN EXCLUSIVE")
            if insert:
                self.db.execute("INSERT INTO reviews VALUES (?,?)", (review["review_id"], _json(review)))
            else:
                updated = self.db.execute("UPDATE reviews SET record=? WHERE review_id=?",
                                          (_json(review), review["review_id"]))
                if updated.rowcount != 1:
                    raise ValueError("pending review audit record is missing")
            self.db.execute("UPDATE supervisor SET state=? WHERE id=1", (_json(state),))
            self.db.commit()  # Pending state must survive a model-call crash.
        except BaseException:
            self.db.rollback()
            # A commit error can leave its outcome uncertain. Re-read durable
            # state instead of continuing with a speculative in-memory counter.
            self._state = json.loads(self.db.execute("SELECT state FROM supervisor WHERE id=1").fetchone()[0])
            raise
        self._state = state

    def due(self, trades, *, now=None):
        self._check_policy()
        current = _now(now)
        if self._state["pending_review"] is not None:
            return {"eligible": False, "new_trade_ids": [], "reason": "pending_review_requires_reconciliation"}
        retry = self._state["retry_not_before"]
        if retry and current < _time(retry):
            return {"eligible": False, "new_trade_ids": [], "reason": "attempt_backoff"}
        activated = _time(self._state["activated_at"])
        reviewed = set(self._state["completed_trade_ids"])
        found, missing_identity, recovery_trade = {}, False, False
        for row in trades:
            if not isinstance(row, dict):
                raise ValueError("closed trade records must be objects")
            if row.get("status", "closed") != "closed":
                continue
            try:
                closed = _time(row.get("closed_at") or row.get("timestamp"))
            except (TypeError, ValueError):
                continue
            if not activated < closed <= current:
                continue
            identity = row.get("position_id") or row.get("trade_id") or row.get("id")
            asset = row.get("asset")
            if not isinstance(identity, str) or not identity or not isinstance(asset, str) or not asset:
                missing_identity = True
                continue
            key = _json([asset, identity])
            if key in reviewed:
                continue
            if row.get('evidence_classification') in {'operational_recovery','operational_risk_stop'} or row.get('exit_reason') in RECOVERY_EXIT_REASONS:
                recovery_trade = True
                continue
            if key in found and found[key][0] != closed:
                raise ValueError("conflicting close times for one position identity")
            if key in found and _json(found[key][1]) != _json(row):
                raise ValueError("conflicting trade contents for one position identity")
            found[key] = (closed, row)
        ordered = sorted(found, key=lambda key: (found[key][0], key))
        eligible = not recovery_trade and not missing_identity and len(ordered) >= self.policy["cadence_closed_trades"]
        snapshot = json.loads(_json([found[key][1] for key in ordered]))
        return {"eligible": eligible, "new_trade_ids": ordered,
                "trade_snapshot": snapshot, "snapshot_hash": _hash(snapshot),
                "reason": "recovery_trade_requires_reconciliation" if recovery_trade else ("missing_trade_identity" if missing_identity else ("five_new_closed_trades" if eligible else "insufficient_new_trades"))}

    def prepare(self, trades, *, now=None):
        current = _now(now)
        due = self.due(trades, now=current)
        if not due["eligible"]:
            raise ValueError("review is not eligible: " + due["reason"])
        review = {"review_id": str(uuid.uuid4()), "started_at": current.isoformat(),
                  "trade_ids": due["new_trade_ids"], "trade_snapshot": due["trade_snapshot"],
                  "snapshot_hash": due["snapshot_hash"], "outcome": "pending"}
        state = dict(self._state, pending_review=review)
        self._save(review, state, insert=True)
        return json.loads(_json(review))

    def _pending(self, review_id, now):
        self._check_policy()
        pending = self._state["pending_review"]
        if pending is None or pending["review_id"] != review_id:
            raise ValueError("review identity does not match the pending request")
        if now < _time(pending["started_at"]):
            raise ValueError("review completion precedes its request")
        return dict(pending)

    def record_material(self, review_id, *, request=None, response=None, decision=None, publication_intent=None, now=None):
        """Retain immutable model inputs/output and staged publication intent."""
        review = self._pending(review_id, _now(now))
        materials = {key: value for key, value in (("request", request), ("response", response),
                                                  ("decision", decision), ("publication_intent", publication_intent)) if value is not None}
        if not materials:
            raise ValueError("review material is required")
        for key, value in materials.items():
            if key in review:
                raise ValueError(f"review {key} is already recorded")
            if not isinstance(value, dict):
                raise ValueError("review material must be an object")
        review.update(json.loads(_json(materials)))
        self._save(review, dict(self._state, pending_review=review))

    def pending_review(self):
        self._check_policy()
        return json.loads(_json(self._state["pending_review"]))

    def complete(self, review_id, *, outcome, provider, model, publication=None, now=None):
        current = _now(now)
        review = self._pending(review_id, current)
        if outcome not in {"changed", "no_change", "rejected"}:
            raise ValueError("unsupported completed-review outcome")
        if provider != self.policy["model"]["provider"] or model != self.policy["model"]["name"]:
            raise ValueError("review did not use the pinned LLM")
        if outcome == "changed":
            if not isinstance(publication, dict) or publication.get("review_id") != review_id or publication.get("status") != "published":
                raise ValueError("changed review requires its verified publication receipt")
            review["publication"] = json.loads(_json(publication))
        review.update(outcome=outcome, provider=provider, model=model, completed_at=current.isoformat())
        state = dict(self._state, pending_review=None, retry_not_before=None,
                     last_completed_review_at=current.isoformat(),
                     completed_trade_ids=sorted(set(self._state["completed_trade_ids"]) | set(review["trade_ids"])))
        self._save(review, state)

    def defer_before_dispatch(self, review_id, *, detail, now=None):
        current=_now(now);review=self._pending(review_id,current)
        if not isinstance(detail,str) or not detail.strip():raise ValueError('deferral requires a recorded reason')
        if 'request' not in review or any(key in review for key in ('response','decision','publication_intent','publication')):
            raise ValueError('only an undispatched prepared request can be deferred')
        review.update(outcome='deferred',completed_at=current.isoformat(),deferred_detail=detail,
            decision={'status':'deferred_before_dispatch','reason':'invocation_budget','model_dispatched':False})
        self._save(review,dict(self._state,pending_review=None,retry_not_before=None))

    def fail(self, review_id, *, reason, now=None):
        current = _now(now)
        review = self._pending(review_id, current)
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("failure requires a recorded reason")
        review.update(outcome="failed", error=reason, completed_at=current.isoformat())
        state = dict(self._state, pending_review=None,
                     retry_not_before=(current + timedelta(minutes=30)).isoformat())
        self._save(review, state)

    def __exit__(self, *_):
        if self.db is not None:
            self.db.close()
            self.db = None
