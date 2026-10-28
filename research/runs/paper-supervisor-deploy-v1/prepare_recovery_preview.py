"""Prepare a strictly read-only remote preflight preview; never apply recovery."""
from pathlib import Path
from datetime import datetime,timezone
import json,sys
RUN=Path(__file__).resolve().parent;LIVE=RUN/'live-cutover';c=json.loads((LIVE/'config.json').read_text())
sys.path.insert(0,c['pc_project'])
from hermes_trading.paper_supervisor_health import prepare_recovery_pc_evidence
evidence=prepare_recovery_pc_evidence(Path(c['pc_project']),Path(c['actual_launch']))
source='''import sys;sys.dont_write_bytecode=True
import json
from datetime import datetime,timezone
from hermes_trading.paper_supervisor_health import build_recovery_request
try:
    request=build_recovery_request('/app/state',approval_ref='read_only_preview_not_an_approval',pc_evidence=PC)
    result=dict(status='read_only_recovery_preflight_passed',prepared_at=request['prepared_at'],valid_until=request['valid_until'],bindings=request['bindings'],recovery_applied=False)
except Exception as error:
    frames=[];tb=error.__traceback__
    while tb:
        file=tb.tb_frame.f_code.co_filename
        if file.startswith('/app/hermes_trading/'):frames.append(dict(file=file,line=tb.tb_lineno))
        tb=tb.tb_next
    result=dict(status='read_only_recovery_preflight_rejected',error_type=type(error).__name__,frames=frames[-8:],recovery_applied=False)
print(json.dumps(dict(observed_at=datetime.now(timezone.utc).isoformat(),**result)))
'''.replace('pc_evidence=PC','pc_evidence='+repr(evidence))
name='preview_recovery_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.py'
with (LIVE/name).open('x') as f:f.write(source)
print(str(LIVE/name))
