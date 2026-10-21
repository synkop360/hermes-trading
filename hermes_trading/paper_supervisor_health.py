"""Small identity-bound paper supervisor health receiver; no model dispatch.

SSH carries one base64 JSON argument, never stdin or exception text. The worker
root owns the safety state; its manifest selects the adaptive reflection owner.
The existing safety recorder enforces latest-event retry and failure latches.
"""
import argparse
import base64
import json
from pathlib import Path
import re

from hermes_trading import paper_safety
from hermes_trading.paired_primary import resolve_primary
from hermes_trading.reflection_supervision import _hash, assert_writer_owner


PURPOSE = 'paper_supervisor_health_v1'
MAX_REQUEST_BYTES = 2048
FIELDS = {'schema_version', 'purpose', 'experiment_id', 'policy_hash', 'event_id',
          'observed_at', 'success', 'error_kind'}


def validate_request(request):
    if (not isinstance(request, dict) or set(request) != FIELDS or
            type(request['schema_version']) is not int or request['schema_version'] != 1 or
            request['purpose'] != PURPOSE):
        raise ValueError('paper supervisor health request differs')
    experiment = request['experiment_id']
    digest = request['policy_hash']
    if (not isinstance(experiment, str) or not 1 <= len(experiment) <= 128 or not experiment.strip() or
            not isinstance(digest, str) or re.fullmatch('[0-9a-f]{64}', digest) is None):
        raise ValueError('paper supervisor health identity differs')
    paper_safety._event_id(request['event_id'])
    paper_safety._result(request['success'], request['error_kind'])
    observed = request['observed_at']
    if not isinstance(observed, str) or len(observed) > 64:
        raise ValueError('paper supervisor health time differs')
    paper_safety._time(observed)
    if len(json.dumps(request, allow_nan=False).encode()) > MAX_REQUEST_BYTES:
        raise ValueError('paper supervisor health request exceeds bound')
    return dict(request)


def build_request(experiment_id, policy_hash, *, event_id, now, success, error_kind=None):
    return validate_request(dict(schema_version=1, purpose=PURPOSE, experiment_id=experiment_id,
        policy_hash=policy_hash, event_id=event_id, observed_at=paper_safety._time(now).isoformat(),
        success=success, error_kind=error_kind))


def receive_health(root, request):
    """Validate all bindings before the first safety-state/lock mutation."""
    request = validate_request(request)
    root = paper_safety._directory(Path(root))
    safety = paper_safety.load_policy(root, request['experiment_id'])
    if safety is None:
        raise ValueError('paper supervisor health requires explicit safety policy')
    manifest = paper_safety._read(root/'manifest.json')
    primary = resolve_primary(manifest)
    state = paper_safety._directory(root/primary.profile_directory(manifest, 'adaptive'))
    # Bound and reject linked metadata before the existing owner reader opens it.
    original = paper_safety._read(state/'reflection_owner.json')
    owner = assert_writer_owner(state, 'external_llm')
    if (owner is None or owner != original or owner['experiment_id'] != request['experiment_id'] or
            _hash(owner) != request['policy_hash']):
        raise ValueError('paper supervisor health owner differs')
    paper_safety.record_supervisor_result(root, success=request['success'], event_id=request['event_id'],
        now=paper_safety._time(request['observed_at']), error_kind=request['error_kind'])
    return dict(request, status='recorded')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Receive one code-only paper supervisor health observation')
    parser.add_argument('--root', required=True)
    parser.add_argument('--request-base64', required=True)
    args = parser.parse_args(argv)
    try:
        if len(args.request_base64) > 4096:
            raise ValueError('paper supervisor health argument exceeds bound')
        raw = base64.b64decode(args.request_base64, validate=True)
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError('paper supervisor health request exceeds bound')
        receipt = receive_health(args.root, json.loads(raw))
    except Exception:
        print(json.dumps(dict(status='rejected', error_kind='supervisor_health_rejected')))
        return 2
    print(json.dumps(receipt, allow_nan=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
