from pathlib import Path
from datetime import datetime,timezone
import json
root=Path('/tmp/hermes-discord-ed439e8')
p=root/'worker-original-bundle'
print(json.dumps(dict(observed_at=datetime.now(timezone.utc).isoformat(),base_exists=root.exists(),bundle_exists=p.exists(),
members=[x.name for x in p.iterdir()] if p.exists() else [],receipt=json.loads((p/'capture-receipt.json').read_text()) if (p/'capture-receipt.json').exists() else None,
ready_exists=Path('/app/state/.paper-ready-discord-ed439e8').exists())))
