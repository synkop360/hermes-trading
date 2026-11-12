from pathlib import Path
from datetime import datetime,timezone
import json
processes=[]
for p in Path('/proc').iterdir():
 if p.name.isdigit():
  try:a=(p/'cmdline').read_bytes().split(b'\0')
  except OSError:continue
  names=[x.decode() for x in a if x in (b'hermes_trading.paper_boot',b'hermes_trading.colocated',b'hermes_trading.paired_worker')]
  if names:processes.append(dict(pid=int(p.name),modules=names))
h=json.loads(Path('/app/state/adaptive_200/heartbeat.json').read_bytes())
print(json.dumps(dict(observed_at=datetime.now(timezone.utc).isoformat(),known_processes=processes,
heartbeat_at=h.get('updated_at'),status=h.get('status'),entry_admission=h.get('entry_admission'),
old_readiness=Path('/app/state/.paper-ready-operational-49c8513').exists(),
new_readiness=Path('/app/state/.paper-ready-discord-ed439e8').exists())))
