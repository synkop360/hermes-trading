from __future__ import annotations

import base64
from contextlib import contextmanager, nullcontext
import json
import os
import re
import shutil
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
STATE_DIR = PROJECT_DIR / "state"
MIRROR_DIR = Path(r"C:\Users\thoma\hermes-trading-config")
REMOTE_STATE_DIR = "/app/state"
LAUNCH_CONFIG = None
STATE_FILES = [
    "goal.yaml",
    "strategy.yaml",
    "heartbeat.json",
    "decisions.jsonl",
    "trades.jsonl",
    "orders.jsonl",
    "hypotheses.jsonl",
    "reflection_state.json",
    "positions.json",
    "wallet_metrics.json",
]
DEFAULT_NO_TRADE_DECISIONS = 30


@contextmanager
def supervised_launch(path):
    global STATE_DIR,MIRROR_DIR,REMOTE_STATE_DIR,LAUNCH_CONFIG
    from hermes_trading.supervisor_launch import validate_launch
    config=validate_launch(path,PROJECT_DIR,_supervisor_release())
    previous=STATE_DIR,MIRROR_DIR,REMOTE_STATE_DIR,LAUNCH_CONFIG
    STATE_DIR,MIRROR_DIR,REMOTE_STATE_DIR,LAUNCH_CONFIG=config['local_state_dir'],config['mirror_dir'],config['remote_state_dir'],config
    try:yield config
    finally:STATE_DIR,MIRROR_DIR,REMOTE_STATE_DIR,LAUNCH_CONFIG=previous


def _maintenance_active():
    return (STATE_DIR/'watcher_maintenance.json').exists() or (LAUNCH_CONFIG is not None and (PROJECT_DIR/'state/watcher_maintenance.json').exists())


def _assert_launched_owner(owner):
    if LAUNCH_CONFIG is None:return
    from hermes_trading.reflection_supervision import _hash
    if owner is None or owner['owner']!='external_llm' or _hash(owner)!=LAUNCH_CONFIG['policy_hash']:
        raise ValueError('activated launch owner disappeared or changed')
    from hermes_trading.supervisor_launch import launch_primary
    from hermes_trading.paired_primary import validate_primary_account,validate_primary_limits
    primary=launch_primary(LAUNCH_CONFIG)
    if primary.versioned:
        account=json.loads((STATE_DIR/'wallet_account.json').read_text(encoding='utf-8'))
        digest=validate_primary_account(account,primary,expected_hash=LAUNCH_CONFIG['account_hash'],experiment_id=owner['experiment_id'])
        limits=json.loads((STATE_DIR/'supervisor_limits.json').read_text(encoding='utf-8'))
        validate_primary_limits(limits,primary,digest)


def _snapshot_expectations():
    if LAUNCH_CONFIG is None:return {}
    from hermes_trading.supervisor_launch import launch_primary
    primary=launch_primary(LAUNCH_CONFIG)
    return {'expected_primary':primary,'expected_account_hash':LAUNCH_CONFIG['account_hash']} if primary.versioned else {}


def _run(command: list[str], *, input_text: str | None = None, timeout_seconds: int | None = None) -> subprocess.CompletedProcess[str]:
    from hermes_trading.supervisor_budget import active_budget
    budget=active_budget()
    if budget is not None:timeout_seconds=budget.timeout(timeout_seconds or 30)
    return subprocess.run(
        command,
        cwd=PROJECT_DIR,
        input=input_text,
        text=True,
        capture_output=True,
        check=True,
        **({'timeout':timeout_seconds} if timeout_seconds is not None else {}),
    )


def _clean_railway_stdout(output: str) -> str:
    lines = output.splitlines()
    if lines and lines[0].startswith("Using SSH key from file "):
        return "\n".join(lines[1:])
    return output


def _looks_like_ssh_gateway_payload(content: str) -> bool:
    """True for Railway's own SSH-gateway discovery/handshake JSON. Seen in
    production: `railway ssh -- "<command>"` started returning this instead
    of executing the command (while Railway itself was down), and this
    cron -- run unattended every ~15-20 min -- silently overwrote every
    local state file with it on each cycle, since only an empty response was
    previously rejected. No real state file is ever a JSON object carrying
    both of these top-level keys together."""
    stripped = content.lstrip()
    return stripped.startswith('{"status":"ready"') and '"account"' in content[:200]


def _download_remote_file(name: str) -> bool:
    result = _run(["railway.cmd", "ssh", "--", f"cat {REMOTE_STATE_DIR}/{name} 2>/dev/null || true"])
    content = _clean_railway_stdout(result.stdout)
    if not content or _looks_like_ssh_gateway_payload(content):
        return False
    target = STATE_DIR / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return True


def _upload_remote_file(local_path: Path, remote_path: str) -> None:
    encoded = base64.b64encode(local_path.read_bytes()).decode("ascii")
    _run(["railway.cmd", "ssh", "--", f"mkdir -p $(dirname {remote_path}) && base64 -d > {remote_path}"], input_text=encoded)


def _sync_to_mirror() -> None:
    MIRROR_DIR.mkdir(parents=True, exist_ok=True)
    for name in STATE_FILES + ["supervisor_status.json"]:
        source = STATE_DIR / name
        if source.exists():
            shutil.copy2(source, MIRROR_DIR / name)


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _parse_timestamp(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _record_timestamp(row: dict) -> datetime | None:
    for field in ("timestamp", "decided_at", "closed_at", "created_at", "updated_at"):
        parsed = _parse_timestamp(row.get(field))
        if parsed is not None:
            return parsed
    return None


def _last_hypothesis_timestamp() -> datetime | None:
    hypotheses = _load_jsonl(STATE_DIR / "hypotheses.jsonl")
    for row in reversed(hypotheses):
        parsed = _parse_timestamp(row.get("timestamp"))
        if parsed:
            return parsed
    return None


def _count_after(path: Path, since: datetime | None, predicate=None) -> int:
    count = 0
    for row in _load_jsonl(path):
        timestamp = _record_timestamp(row)
        if timestamp is None or (since is not None and timestamp <= since):
            continue
        if predicate and not predicate(row):
            continue
        count += 1
    return count


def _read_scalar_yaml_int(path: Path, key: str, default: int) -> int:
    if not path.exists():
        return default
    match = re.search(rf"(?m)^\s*{re.escape(key)}\s*:\s*(\d+)\s*$", path.read_text(encoding="utf-8"))
    return int(match.group(1)) if match else default


def _strategy_version() -> str:
    path = STATE_DIR / "strategy.yaml"
    if not path.exists():
        return "unknown"
    match = re.search(r"(?m)^\s*version\s*:\s*['\"]?([^'\"\r\n]+)", path.read_text(encoding="utf-8"))
    return match.group(1).strip() if match else "unknown"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _reflection_is_due() -> tuple[bool, str]:
    since = _last_hypothesis_timestamp()
    reflection_every = _read_scalar_yaml_int(STATE_DIR / "goal.yaml", "reflection_every", 5)
    no_trade_limit = int(
        os.environ.get(
            "HERMES_NO_TRADE_REFLECTION_DECISIONS",
            os.environ.get("HERMES_REFLECT_MIN_NEW_DECISIONS", str(DEFAULT_NO_TRADE_DECISIONS)),
        )
    )
    closed_trades = _count_after(STATE_DIR / "trades.jsonl", since)
    decisions = _count_after(STATE_DIR / "decisions.jsonl", since)
    closed_decisions = _count_after(STATE_DIR / "decisions.jsonl", since, lambda row: bool(row.get("trade_closed")))

    if closed_trades >= reflection_every:
        return True, f"{closed_trades} closed trades since last reflection"
    if decisions >= no_trade_limit and closed_decisions == 0:
        return True, f"{decisions} no-trade decisions since last reflection"
    return False, f"{closed_trades} closed trades, {decisions} decisions since last reflection"


def _run_reflection() -> None:
    _run(
        [
            "uv",
            "run",
            "python",
            "-m",
            "hermes_trading.reflect",
            "--hermes",
            "--state-dir",
            str(STATE_DIR),
        ]
    )


def _remote_json(result):
    content = _clean_railway_stdout(result.stdout)
    if not content or _looks_like_ssh_gateway_payload(content):
        raise ValueError("Railway did not return worker data")
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError("worker response must be an object")
    return value


def _remote_snapshot():
    command = f"{_remote_python()} -m hermes_trading.supervisor_sync --state-dir {shlex.quote(REMOTE_STATE_DIR)}"
    return _remote_json(_run(["railway.cmd", "ssh", "--", command],timeout_seconds=30))


def _publish_remote(request):
    command = f"{_remote_python()} -m hermes_trading.paper_publication --state-dir {shlex.quote(REMOTE_STATE_DIR)}"
    return _remote_json(_run(["railway.cmd", "ssh", "--", command], input_text=json.dumps(request, allow_nan=False),timeout_seconds=30))


def _install_remote_usage(request):
    command = f"{_remote_python()} -m hermes_trading.supervisor_usage_feed --state-dir {shlex.quote(REMOTE_STATE_DIR)}"
    return _remote_json(_run(["railway.cmd", "ssh", "--", command], input_text=json.dumps(request, allow_nan=False),timeout_seconds=30))


def _probe_model_access(policy, existing):
    python=os.environ.get('HERMES_PROPOSAL_PYTHON')
    if not python:
        hermes=shutil.which('hermes')
        adjacent=Path(hermes).parent/('python.exe' if os.name=='nt' else 'python') if hermes else None
        python=str(adjacent) if adjacent is not None and adjacent.is_file() else sys.executable
    return _remote_json(_run([python,'-m','hermes_trading.model_access_refresh','probe'],
        input_text=json.dumps({'policy':policy,'existing':existing},allow_nan=False),timeout_seconds=60))


def _install_remote_access(request):
    command=f"{_remote_python()} -m hermes_trading.model_access_refresh install --state-dir {shlex.quote(REMOTE_STATE_DIR)}"
    return _remote_json(_run(['railway.cmd','ssh','--',command],input_text=json.dumps(request,allow_nan=False),timeout_seconds=30))


def _remote_python():
    return '/app/.venv/bin/python' if LAUNCH_CONFIG is not None else 'python'


def _refresh_supervised_access(policy, *, now=None):
    if policy.get('model_access_renewal_required') is not True:return None
    from datetime import timedelta
    from hermes_trading.reflection_supervision import _now, _hash
    from hermes_trading.model_access_refresh import validate_renewed_access,build_access_request
    from hermes_trading.paper_publication import _temporary,_sync_dir
    from hermes_trading.supervisor_usage_source import _stamp
    existing=json.loads((STATE_DIR/'model_access.json').read_text(encoding='utf-8'))
    at=_now(now);access=None
    try:
        validate_renewed_access(policy,existing,now=at)
        if _stamp(existing['valid_until'])-at>timedelta(minutes=2):access=existing
    except ValueError:pass
    if access is None:access=_probe_model_access(policy,existing)
    validate_renewed_access(policy,access,now=_now(now))
    if access.get('provenance_ref')!=existing.get('provenance_ref'):raise ValueError('access refresh changes reviewed provenance')
    account=json.loads((STATE_DIR/'wallet_account.json').read_text(encoding='utf-8'))
    request=build_access_request(access,policy,account);receipt=_install_remote_access(request)
    expected={'status':'renewed','access_hash':_hash(access),'policy_hash':_hash(policy),'account_hash':_hash(account),
              'verified_at':access['verified_at'],'valid_until':access['valid_until']}
    if not isinstance(receipt,dict) or any(receipt.get(key)!=value for key,value in expected.items()):raise ValueError('worker access receipt differs from renewed proof')
    for name,body in (('model_access.json',access),('model_access_import_receipt.json',receipt)):
        target=STATE_DIR/name;temporary=_temporary(target,json.dumps(body,allow_nan=False).encode())
        try:os.replace(temporary,target)
        finally:temporary.unlink(missing_ok=True)
    _sync_dir(STATE_DIR);return receipt


def _sync_supervised_usage(policy, *, now=None):
    from hermes_trading.reflection_supervision import _hash, _now
    from hermes_trading.supervisor_usage_source import export_supervisor_usage
    from hermes_trading.supervisor_usage_feed import build_usage_request
    from hermes_trading.paper_publication import _temporary, _sync_dir
    path=STATE_DIR/'wallet_cost_sources.json'
    inputs=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if not isinstance(inputs,dict):raise ValueError('supervisor cost source plan must be an object')
    categories=inputs.get('categories',{})
    if not isinstance(categories,dict):raise ValueError('supervisor cost categories must be an object')
    row=categories.get('ai',{})
    if not isinstance(row,dict):raise ValueError('supervisor AI source plan must be an object')
    if row.get('mode')!='supervised':
        if policy.get('wallet_required') is True:raise ValueError('configured wallet requires the supervised usage source')
        return None
    if row.get('policy_hash')!=_hash(policy):raise ValueError('supervisor cost source policy differs')
    account=json.loads((STATE_DIR/'wallet_account.json').read_text(encoding='utf-8'))
    snapshot=export_supervisor_usage(STATE_DIR,account,now=_now(now))
    request=build_usage_request(snapshot,policy,account);receipt=_install_remote_usage(request)
    expected={'status':'imported','experiment_id':policy['experiment_id'],'policy_hash':request['policy_hash'],
              'account_hash':request['account_hash'],'source_sha256':request['source_sha256'],'covered_through':snapshot['covered_through']}
    if not isinstance(receipt,dict) or any(receipt.get(key)!=value for key,value in expected.items()):
        raise ValueError('worker usage receipt differs from exported source')
    target=STATE_DIR/'supervisor_usage_import_receipt.json';temporary=_temporary(target,json.dumps(receipt).encode())
    try:os.replace(temporary,target)
    finally:temporary.unlink(missing_ok=True)
    _sync_dir(STATE_DIR)
    return receipt


@contextmanager
def _strict_runner(policy, *, now=None):
    from hermes_trading.costs import CostLedger
    from hermes_trading.model_runner import ReflectionModelRunner
    from hermes_trading.persistence.repository import StateRepository
    from hermes_trading.providers import HermesCatalog, ModelCandidate, ProviderRouter
    from hermes_trading.reflection_supervision import _hash, _now, _time
    path = STATE_DIR / "model_access.json"
    if not path.exists():
        raise ValueError("reviewed model-access configuration is required")
    access = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(access, dict) or type(access.get("schema_version")) is not int or access["schema_version"] != 1:
        raise ValueError("invalid model-access schema")
    if access.get("policy_hash") != _hash(policy) or (access.get("provider"), access.get("model")) != (policy["model"]["provider"], policy["model"]["name"]):
        raise ValueError("model access differs from the pinned experiment")
    if _time(access.get("valid_until")) <= _now(now):
        raise ValueError("model-access verification expired")
    if policy.get('model_access_renewal_required') is True:
        from hermes_trading.model_access_refresh import validate_renewed_access
        validate_renewed_access(policy,access,now=_now(now))
    payment = access.get("payment")
    reference = access.get("provenance_ref") if payment == "verified_free" else access.get("approval_ref")
    if payment not in {"verified_free", "paid_authorized"} or not isinstance(reference, str) or not reference.strip():
        raise ValueError("explicit verified-free provenance or paid approval is required")
    repository = StateRepository.open(STATE_DIR / "llm_providers.sqlite")
    try:
        repository.migrate()
        yield ReflectionModelRunner(ProviderRouter(repository, HermesCatalog(())),
            pinned_candidate=ModelCandidate(access["provider"], access["model"], payment == "verified_free", "reviewed_model_access"),
            strict_pinned=True, paid_calls_authorized=payment == "paid_authorized", cost_ledger=CostLedger(STATE_DIR),timeout_seconds=60)
    finally:
        repository.close()


def _supervisor_release():
    import hashlib
    from hermes_trading.paper_publication import _release_hashes
    return {'runtime':_release_hashes(),'watcher':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'native_launcher':hashlib.sha256(Path(__file__).with_name('native_hermes_watch.py').read_bytes()).hexdigest()}


def _supervised_watch_result(*, now=None):
    from hermes_trading.supervisor_budget import InvocationBudgetExpired
    try:return _run_supervised_cycle(now=now)
    except InvocationBudgetExpired:return {'status':'deferred','reason':'invocation_budget'}


def _deliver_supervisor_health(path):
    """Retry the exact pending observation, never substitute a fresh timestamp."""
    from hermes_trading import paper_safety
    from hermes_trading.paper_supervisor_health import validate_request
    from hermes_trading.persistence.artifacts import _fsync_directory
    request=validate_request(paper_safety._read(path))
    if (request['experiment_id']!=LAUNCH_CONFIG['experiment_id'] or
            request['policy_hash']!=LAUNCH_CONFIG['policy_hash']):
        raise ValueError('pending supervisor health launch binding differs')
    encoded=base64.b64encode(json.dumps(request,allow_nan=False,separators=(',',':')).encode()).decode('ascii')
    command=(f'{_remote_python()} -m hermes_trading.paper_supervisor_health '
             f'--root /app/state --request-base64 {shlex.quote(encoded)}')
    receipt=_remote_json(_run(['railway.cmd','ssh','--',command],timeout_seconds=10))
    if receipt!=dict(request,status='recorded'):
        raise ValueError('supervisor health acknowledgment differs')
    path.unlink()
    _fsync_directory(path.parent)


def _run_supervised_watch(*, now=None):
    flag=LAUNCH_CONFIG.get('paper_safety_enabled',False) if LAUNCH_CONFIG is not None else False
    if type(flag) is not bool:raise ValueError('paper safety launch flag must be boolean')
    if not flag:return _supervised_watch_result(now=now)
    import uuid
    from hermes_trading import paper_safety
    from hermes_trading.paper_publication import _temporary
    from hermes_trading.paper_supervisor_health import build_request
    from hermes_trading.persistence.artifacts import _artifact_lock,_fsync_directory
    directory=paper_safety._directory(STATE_DIR)
    pending=directory/'paper_supervisor_health_pending.json'
    paper_safety._regular(pending.with_suffix('.json.lock'),missing=True)
    def observe(success,error_kind):
        at=paper_safety._utc_now() if now is None else now
        request=build_request(LAUNCH_CONFIG['experiment_id'],LAUNCH_CONFIG['policy_hash'],
            event_id=uuid.uuid4().hex,now=at,success=success,error_kind=error_kind)
        if paper_safety._regular(pending,missing=True) is not None:
            raise ValueError('previous supervisor health observation remains pending')
        temporary=_temporary(pending,json.dumps(request,allow_nan=False).encode())
        try:
            os.replace(temporary,pending)
            _fsync_directory(directory)
        finally:temporary.unlink(missing_ok=True)
        _deliver_supervisor_health(pending)
    with _artifact_lock(pending):
        if paper_safety._regular(pending,missing=True) is not None:
            _deliver_supervisor_health(pending)
        try:result=_supervised_watch_result(now=now)
        except Exception:
            observe(False,'supervisor_exception')
            raise
        status=result.get('status') if isinstance(result,dict) else None
        success=status in {'not_due','no_change','published','rejected'}
        observe(success,None if success else 'supervisor_unhealthy_result')
        return result


def _run_supervised_cycle(*, now=None):
    from hermes_trading.global_reflection import run_global_review
    from hermes_trading.paper_publication import _temporary, _sync_dir, publish_pending_review
    from hermes_trading.reflection_supervision import ReviewLease, _connect, _now, load_owner_policy
    from hermes_trading.supervisor_sync import validate_snapshot
    from hermes_trading.supervisor_lifecycle import admit_cycle
    from hermes_trading.supervisor_budget import active_budget
    budget=active_budget()
    def phase(reserve):return budget.phase(reserve=reserve) if budget is not None else nullcontext()
    def fits(seconds,reserve=5):return budget is None or budget.can_fit(seconds,reserve=reserve)
    if _maintenance_active():return {'status':'maintenance'}
    cycle_lease = _connect(STATE_DIR / "watcher_cycle.sqlite")
    try:
        policy = load_owner_policy(STATE_DIR)
        _assert_launched_owner(policy)
        lifecycle=admit_cycle(STATE_DIR,policy,_supervisor_release(),now=_now(now))
        if lifecycle is not None and lifecycle.status!='admitted':
            return {'status':'supervisor_stopped' if lifecycle.status=='stopped' else 'not_due',
                    'reason':lifecycle.state['stop_reason'] if lifecycle.status=='stopped' else 'supervisor_interval'}
        with phase(40):_refresh_supervised_access(policy,now=now)
        # Cost bootstrap must precede wallet-health validation. Transporting
        # coverage does not grant permission to dispatch a model or trade.
        with phase(5):_sync_supervised_usage(policy,now=now)
        with phase(40):files = validate_snapshot(_remote_snapshot(), policy, now=_now(now),**_snapshot_expectations())
        def action_admitted():
            if _maintenance_active():return False
            if lifecycle is not None:return lifecycle.check_actions(now=_now(now))
            return not (STATE_DIR/'watcher_maintenance.json').exists() and not (STATE_DIR/'supervisor_operator_stop.json').exists()
        def stopped_result():
            return {'status':'supervisor_stopped','reason':lifecycle.state['stop_reason'] if lifecycle is not None else 'operator_stop'}
        if not action_admitted():return stopped_result()
        # All inputs validate before the first local file is replaced. The cycle
        # lease prevents competing watchers from syncing during another review.
        for name, data in files.items():
            target = STATE_DIR / name
            temporary = _temporary(target, data)
            try: os.replace(temporary, target)
            finally: temporary.unlink(missing_ok=True)
        _sync_dir(STATE_DIR)
        with ReviewLease(STATE_DIR) as review_lease:
            pending = review_lease.pending_review()
            due = review_lease.due(_load_jsonl(STATE_DIR / "trades.jsonl"), now=_now(now))
        if pending is not None and pending.get("decision", {}).get("status") == "awaiting_publication":
            result = {"status": "awaiting_publication"}
        elif not due["eligible"]:
            result = {"status": "not_due", "reason": due["reason"]}
        elif not fits(60,reserve=40):
            result = {"status": "deferred", "reason": "invocation_budget"}
        else:
            if not action_admitted():return stopped_result()
            if lifecycle is not None and not lifecycle.reserve_model(now=_now(now)):
                result={'status':'supervisor_stopped','reason':lifecycle.state['stop_reason']}
            else:
                with _strict_runner(policy, now=now) as runner:
                    if not action_admitted():return stopped_result()
                    if not fits(60,reserve=40):
                        result = {"status": "deferred", "reason": "invocation_budget"}
                    else:
                        if budget is not None:runner.timeout_seconds=min(runner.timeout_seconds,budget.timeout(60,reserve=40))
                        if lifecycle is not None:
                            from hermes_trading.reflection_supervision import _time
                            runner.timeout_seconds=min(runner.timeout_seconds,(_time(lifecycle.state['deadline'])-_now(now)).total_seconds())
                        result = run_global_review(STATE_DIR, runner, now=now)
        # Reconcile the completed model work before optional publication. A
        # rejected receipt leaves the same candidate pending without changing
        # the strategy; a slow publication cannot delay this cost transfer.
        with phase(5):usage_receipt=_sync_supervised_usage(policy,now=now)
        if result["status"] == "awaiting_publication":
            if not action_admitted():return stopped_result()
            if fits(30):
                with phase(5):result = publish_pending_review(STATE_DIR, _publish_remote, now=now)
            else:result=dict(result,reason='invocation_budget')
        result = dict(result)
        if usage_receipt is not None:result['usage_source_sha256']=usage_receipt['source_sha256']
        result["mirror_state"] = "current"
        if result["status"] == "published":
            # The receipt confirms publication, but the next worker heartbeat
            # may not yet observe its new version. Do not mislabel old mirrors.
            try:
                if not fits(30):
                    from hermes_trading.supervisor_budget import InvocationBudgetExpired
                    raise InvocationBudgetExpired('mirror refresh deferred')
                with phase(5):refreshed = validate_snapshot(_remote_snapshot(), policy, now=_now(now),**_snapshot_expectations())
                import hashlib
                if hashlib.sha256(refreshed["strategy.yaml"]).hexdigest() != result["candidate_sha256"]:
                    raise ValueError("worker has not confirmed the published candidate")
                for name, data in refreshed.items():
                    target = STATE_DIR / name
                    temporary = _temporary(target, data)
                    try: os.replace(temporary, target)
                    finally: temporary.unlink(missing_ok=True)
                _sync_dir(STATE_DIR)
            except Exception:
                result["mirror_state"] = "awaiting_worker_refresh"
        status = {"observed_at": _now(now).isoformat(), "status": result["status"],
                  "mirror_state": result["mirror_state"],
                  "refresh_required": result["mirror_state"] != "current"}
        if result["status"] == "published":
            status["published_strategy_sha256"] = result["candidate_sha256"]
        target = STATE_DIR / "supervisor_status.json"
        temporary = _temporary(target, json.dumps(status).encode("utf-8"))
        try: os.replace(temporary, target)
        finally: temporary.unlink(missing_ok=True)
        _sync_dir(STATE_DIR)
        if fits(5):_sync_to_mirror()
        else:result['mirror_copy_deferred']=True
        return result
    finally:
        cycle_lease.close()


def _main() -> int:
    if _maintenance_active():
        print("Hermes watch paused: maintenance marker is active; no sync or model review started")
        return 0
    _load_dotenv(PROJECT_DIR / ".env")
    _load_dotenv(Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / ".env")
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    from hermes_trading.reflection_supervision import load_owner_policy
    owner = load_owner_policy(STATE_DIR)
    _assert_launched_owner(owner)
    if owner is not None:
        if owner["owner"] != "external_llm":
            print("Hermes watch idle: strategy changes belong to the deterministic worker")
            return 0
        result = _run_supervised_watch()
        print(f"Hermes supervised watch: {result['status']}; reason={result.get('reason', '')}")
        return 0

    downloaded = [name for name in STATE_FILES if _download_remote_file(name)]
    _sync_to_mirror()

    before_version = _strategy_version()
    due, reason = _reflection_is_due()
    if due:
        _run_reflection()
        after_version = _strategy_version()
        _sync_to_mirror()
        _upload_remote_file(STATE_DIR / "strategy.yaml", f"{REMOTE_STATE_DIR}/strategy.yaml")
        _upload_remote_file(STATE_DIR / "hypotheses.jsonl", f"{REMOTE_STATE_DIR}/hypotheses.jsonl")
        archived = STATE_DIR / "history" / f"v{int(before_version):04d}.yaml"
        if archived.exists():
            _upload_remote_file(archived, f"{REMOTE_STATE_DIR}/history/{archived.name}")
        print(
            f"Hermes watch ok: reflection applied {before_version}->{after_version}; "
            f"reason={reason}; downloaded={len(downloaded)} files"
        )
    else:
        print(f"Hermes watch ok: no reflection due; strategy={before_version}; reason={reason}")
    return 0


def main(argv=()) -> int:
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--launch-file')
    args=parser.parse_args(argv)
    if args.launch_file:
        from hermes_trading.supervisor_budget import InvocationBudget
        import math,time
        start=time.monotonic()
        inherited=os.environ.get('HERMES_TRADING_INVOCATION_DEADLINE')
        inherited=float(inherited) if inherited is not None else start+235
        if not math.isfinite(inherited):raise ValueError('finite inherited invocation deadline required')
        with supervised_launch(args.launch_file):
            maximum=LAUNCH_CONFIG.get('max_script_seconds')
            if type(maximum) is not int or not 1<=maximum<=240:raise ValueError('finite launcher wait required')
            from hermes_trading.reflection_supervision import _now,_time
            remaining=(_time(LAUNCH_CONFIG['expires_at'])-_now()).total_seconds()
            deadline=min(start+maximum-5,inherited,time.monotonic()+remaining-5)
            budget=InvocationBudget(deadline)
            with budget.activate():return _main()
    return _main()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
