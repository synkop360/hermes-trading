import sys;sys.dont_write_bytecode=True
from pathlib import Path
import json,os
root=Path('/app/state');bundle=Path('/tmp/hermes-snapshot-20eb6af/worker-original-bundle')
value=dict(status='read_only_capture_reconciliation',marker_absent=not(root/'.paper-ready-snapshot-20eb6af').exists(),paper_boot_is_pid1=b'hermes_trading.paper_boot' in Path('/proc/1/cmdline').read_bytes(),bundle_exists=bundle.exists(),bundle_members=sorted(p.relative_to(bundle).as_posix() for p in bundle.iterdir()) if bundle.exists() else [],sidecars=[dict(path=p.relative_to(root).as_posix(),bytes=p.stat().st_size,cold_journal_header=p.read_bytes()[:8].hex() if p.name.endswith('-journal') else None) for p in root.rglob('*') if p.is_file() and p.name.endswith(('-wal','-shm','-journal'))])
if bundle.exists():
 value['published_inventory_exists']=(bundle/'worker-native-snapshot/snapshot_inventory.json').exists()
 value['native_files']=sum(p.is_file() for p in (bundle/'worker-native-snapshot').rglob('*'))
print(json.dumps(value))
