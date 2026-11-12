"""Read-only comparison of the original frozen package and installed PC source."""
from pathlib import Path
import hashlib,json

root=Path(__file__).resolve().parents[3]
parent=root.parent
frozen=parent/'hermes-snapshot-20eb6af'
expected=json.loads((parent/'hermes-snapshot-20eb6af.raw-sha256.json').read_text(encoding='utf-8'))
actual={path.relative_to(frozen).as_posix():hashlib.sha256(path.read_bytes()).hexdigest()
        for path in frozen.rglob('*') if path.is_file()}
source=[name for name in expected if name.startswith('hermes_trading/') and name.endswith('.py')]
source+=['scripts/hermes_watch.py','scripts/native_hermes_watch.py']
pc=parent/'hermes-trading'
pc_mismatch=[name for name in source if hashlib.sha256((pc/name).read_bytes()).hexdigest()!=expected[name]]
unrelated=hashlib.sha256((root/'docs/PAPER_OPERATIONAL_CORRECTION_CUTOVER.md').read_bytes()).hexdigest()
result=dict(frozen_count=len(actual),expected_count=len(expected),frozen_unchanged=actual==expected,
    installed_sources_checked=len(source),installed_source_mismatches=pc_mismatch,
    unrelated_dirty_document_unchanged=unrelated=='1b7aa94b07b5307e2bd9636abb6e614c6dc9924fbcdbb79a48930498f8fb0f70')
Path(__file__).with_name('untouched-verification.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result))
raise SystemExit(0 if result['frozen_unchanged'] and not pc_mismatch and result['unrelated_dirty_document_unchanged'] else 1)
