"""Freeze the exact native-prepared request into an explicit enabled invocation."""
from pathlib import Path
import json,sys
LIVE=Path(__file__).resolve().parent/'live-cutover';source=Path(sys.argv[1]).resolve()
assert source.parent==LIVE.resolve()
native=json.loads(source.with_suffix('.native-result.json').read_text());assert native['native_exit_code']==0
assert native['result']['status']=='authorized_recovery_request_prepared'
request=native['result']['request'];request_id=request['request_id']
with (LIVE/('request-'+request_id+'.json')).open('x') as f:json.dump(request,f,sort_keys=True,indent=2)
script='''import sys;sys.dont_write_bytecode=True
from pathlib import Path
from datetime import datetime,timezone
import json
from hermes_trading.paper_supervisor_health import receive_recovery
from hermes_trading.paper_safety import entry_status,load_policy
request=REQUEST
try:
    receipt=receive_recovery('/app/state',request,enabled=True)
    result=dict(status='checked_recovery_returned',receipt=receipt)
except Exception as error:
    frames=[];tb=error.__traceback__
    while tb:
        file=tb.tb_frame.f_code.co_filename
        if file.startswith('/app/hermes_trading/'):frames.append(dict(file=file,line=tb.tb_lineno))
        tb=tb.tb_next
    result=dict(status='checked_recovery_rejected_or_incomplete',error_type=type(error).__name__,frames=frames[-8:])
root=Path('/app/state');audit=root/'paper_supervisor_recovery'
result.update(observed_at=datetime.now(timezone.utc).isoformat(),request_id=request['request_id'])
try:
    result.update(entry_admission=entry_status(root,policy=load_policy(root,request['experiment_id'])),
        safety=json.loads((root/'paper_safety_state.json').read_text()),pending=(root/'paper_supervisor_recovery_pending.json').exists(),
        intent_exists=(audit/(request['request_id']+'.intent.json')).exists(),receipt_exists=(audit/(request['request_id']+'.receipt.json')).exists())
except Exception as error:result['readback_error_type']=type(error).__name__
print(json.dumps(result))
'''.replace('request=REQUEST','request='+repr(request))
path=LIVE/('apply_recovery_'+request_id+'.py')
with path.open('x') as f:f.write(script)
print(str(path))
