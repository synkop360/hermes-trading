"""Prepare concrete operator scripts from the previously executed checked cutover."""
from pathlib import Path
import json,re,subprocess
ROOT=Path(__file__).resolve().parents[3];RUN=Path(__file__).resolve().parent;LIVE=RUN/'live-cutover';LIVE.mkdir(exist_ok=True)
old=ROOT/'research/runs/paper-supervisor-retry-v1/live-cutover'
meta=json.loads((RUN/'package.json').read_text());plan=json.loads((RUN/'release-plan.json').read_text())
identity=json.loads((ROOT/'research/runs/paper-discord-reporting-v1/proposed-original-identity.json').read_text())
label='recovery-'+meta['commit'][:7];ref=ROOT.parent/('r27-'+meta['commit'][:7])
config=dict(label=label,reference=str(ref),package=meta['package'],approval='owner_20261027_always_deploy_paper_only',
    candidate=meta['commit'],remote_base='/tmp/hermes-'+label,marker='/app/state/.paper-ready-'+label,
    pc_audit='C:/Users/thoma/AppData/Local/hermes/cutovers/paper-'+label+'/pc-audit',
    pc_project=str(ROOT.parent/'hermes-trading'),actual_launch='C:/Users/thoma/AppData/Local/hermes/scripts/hermes_trading_paper_6c9d520_native.json')
(LIVE/'config.json').write_text(json.dumps(config,indent=2)+'\n')
(LIVE/'release-plan.json').write_text(json.dumps(plan,indent=2)+'\n')
def adapt(source):
    source=source.replace('hermes-snapshot-20eb6af','hermes-'+label).replace('paper-snapshot-20eb6af','paper-'+label)
    source=source.replace('.paper-ready-snapshot-20eb6af','.paper-ready-'+label)
    source=source.replace("/'s20'","/"+repr(ref.name))
    source=source.replace('owner_20261026_snapshot_20eb6af_paper_cutover',config['approval'])
    return source
for name in ('run_remote.py','capture_original_worker.py','reconcile_cold_journal.py','prepare_native_plan.py',
             'prepare_worker_intent.py','prepare_pc_intent.py','apply_pc_transition.py','apply_worker_transition.py',
             'transfer_worker_reference.py','transfer_worker_audit.py','release_successor_readiness.py'):
    source=adapt((old/name).read_text())
    if name in ('prepare_pc_intent.py','transfer_worker_audit.py'):
        source=source.replace("base.parent/'hermes-"+label+"'",'Path('+repr(meta['package'])+')')
    if name=='transfer_worker_audit.py':source=source.replace('prepare_worker_intent_reconciled.native-result.json','prepare_worker_intent.native-result.json')
    for key,value in [('EXPECTED_RUNTIME',plan['new']['runtime']),('IDENTITY',identity),('RELEASE',plan)]:
        source=re.sub(r'^'+key+r'=.*$',key+'='+repr(value),source,flags=re.M)
    (LIVE/name).write_text(source,encoding='utf-8')
source=(old/'preflight_worker.py').read_text()
source=re.sub(r'^EXPECTED_RUNTIME=.*$','EXPECTED_RUNTIME='+repr(plan['old']['runtime']),source,flags=re.M)
source=adapt(source)
(LIVE/'preflight_worker.py').write_text(source,encoding='utf-8')
gate="/bin/sh -c 'set -eu\ndeadline=$(($(date +%s) + 1800))\nuntil [ -f "+config['marker']+" ]; do\n    if [ \"$(date +%s)\" -ge \"$deadline\" ]; then\n        echo paper_preparation_expired >&2\n        exit 78\n    fi\n    sleep 2\ndone\nexec /app/.venv/bin/python -m hermes_trading.paper_boot --root /app/state\n'"
(LIVE/'set-gate.graphql').write_bytes((old/'set-gate.graphql').read_bytes())
(LIVE/'set-gate.variables.json').write_text(json.dumps(dict(serviceId=plan['target']['service'],environmentId=plan['target']['environment'],input=dict(startCommand=gate)),indent=2)+'\n')
(LIVE/'stop.graphql').write_bytes((old/'stop.graphql').read_bytes())
print(json.dumps(config))
