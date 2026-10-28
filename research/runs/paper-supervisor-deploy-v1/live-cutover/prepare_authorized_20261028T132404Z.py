import sys;sys.dont_write_bytecode=True
import json
from hermes_trading.paper_supervisor_health import build_recovery_request
try:
    request=build_recovery_request('/app/state',approval_ref='owner_20261028_resume_and_make_worker_not_halted',pc_evidence={'schema_version': 1, 'observed_at': '2026-10-28T13:24:04.005308+00:00', 'experiment_id': 'paper-beta-v1-20261021-6c9d520', 'policy_hash': 'db684ab9780a550de2cbaa22d586dfe706154cea47dfd6bef8ed247ff56d1577', 'account_hash': '8537e0f7f17abc8a092f2ca8e39ad8b1d290f8ee222296fbdc834fe00bb488f3', 'primary_wallet_eur': 200, 'primary_selection_sha256': '34cb2c43f0e5805e1fbb07f731da8028e258b4b74250e8e54d3b2ceb557f4c90', 'release_hash': 'd8289ff44d543bebca1df87a566335f05366607ffc767d05eb00b75c6eddcf04', 'runtime_hash': 'ffe95446f146d79441862a3eae14228865fd7bf460fc7c7311f5ead3510834eb', 'launch_hash': '8c69f59e1b1dd937e40142a4d4db063c49841110e44cd2667c8ea08c83a2dbd7', 'limits_hash': '82e7730a295e7d5924109ec6e267a307431a80edaf7df888a35cd64a8d00dec6', 'wrapper_hash': 'd1426320536bd8c369163d8fd6221f151fcb4f01e76ceb3033d945b8f96bd073', 'lifecycle_hash': 'bda2a5c6132c1f400e55c07ebfa54df1c80a68ab57bd4e6481084282559bf47f'})
    result=dict(status='authorized_recovery_request_prepared',request=request,recovery_applied=False)
except Exception as error:
    frames=[];tb=error.__traceback__
    while tb:
        file=tb.tb_frame.f_code.co_filename
        if file.startswith('/app/hermes_trading/'):frames.append(dict(file=file,line=tb.tb_lineno))
        tb=tb.tb_next
    result=dict(status='authorized_recovery_request_rejected',error_type=type(error).__name__,frames=frames[-8:],recovery_applied=False)
print(json.dumps(result))
