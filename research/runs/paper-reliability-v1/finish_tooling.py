"""Prepare operator evidence/scripts; each stage has explicit bounded inputs."""
from pathlib import Path
from datetime import datetime, timezone
import base64, hashlib, io, json, re, shutil, sys, tarfile
RUN=Path(__file__).resolve().parent;LIVE=RUN/'live-cutover';ROOT=RUN.parents[2]
old=ROOT/'research/runs/paper-supervisor-retry-v1/live-cutover'
c=json.loads((LIVE/'config.json').read_text());meta=json.loads((RUN/'package.json').read_text())
ref=Path(c['reference']);package=Path(c['package']);stage=sys.argv[1]
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(name,value):
    with (LIVE/name).open('x',encoding='utf-8') as f:json.dump(value,f,indent=2);f.write('\n')
def adapt(source):
    return source.replace('hermes-snapshot-20eb6af','hermes-'+c['label']).replace('paper-snapshot-20eb6af','paper-'+c['label']).replace('.paper-ready-snapshot-20eb6af','.paper-ready-'+c['label']).replace("/'s20/w'","/"+repr(ref.name+'/w'))
if stage=='scripts':
    text=adapt((old/'verify_actual_pc.py').read_text())
    text=re.sub(r'^SOURCE = .*$', 'SOURCE = '+repr(c['candidate']), text,flags=re.M)
    text=re.sub(r'^EXPECTED_RELEASE = .*$', 'EXPECTED_RELEASE = '+repr(meta['release_hash']),text,flags=re.M)
    text=re.sub(r'^EXPECTED_TRANSITION = .*$', "EXPECTED_TRANSITION = read_plan_placeholder",text,flags=re.M)
    text=text.replace('EXPECTED_TRANSITION = read_plan_placeholder',"EXPECTED_TRANSITION = json.loads((LIVE/'frozen-live-transition-plan.json').read_text())['transition_id']")
    text=text.replace("read(LIVE.parent/'candidate-package-v4.raw-sha256.json')",'read(Path('+repr(str(package.with_name(package.name+'.raw-sha256.json')))+'))')
    (LIVE/'verify_actual_pc.py').write_text(text,encoding='utf-8')
elif stage=='bind_instance':
    instance=sys.argv[2];assert re.fullmatch('[0-9a-f-]{36}',instance)
    for name in ('transfer_worker_reference.py','transfer_worker_audit.py'):
        text=(LIVE/name).read_text();assert '577064e4-f63d-4ff5-af18-a87ff4e97765' in text
        (LIVE/name).write_text(text.replace('577064e4-f63d-4ff5-af18-a87ff4e97765',instance))
    save('successor-instance.json',dict(instance=instance,at=datetime.now(timezone.utc).isoformat()))
elif stage=='upload':
    data={'launch-original.json':(ref/'l.json').read_bytes()}
    data.update({'pc-original/'+p.relative_to(ref/'p').as_posix():p.read_bytes() for p in (ref/'p').rglob('*') if p.is_file()})
    assert sum(map(len,data.values()))<=16*1024**2
    expected={k:hashlib.sha256(v).hexdigest() for k,v in data.items()}
    out=io.BytesIO()
    with tarfile.open(fileobj=out,mode='w') as archive:
        for name,raw in sorted(data.items()):
            assert len(raw)<=8*1024**2
            item=tarfile.TarInfo(name);item.size=len(raw);archive.addfile(item,io.BytesIO(raw))
    text=adapt((old/'upload_pc_reference.py').read_text()).replace('member.size<=4*1024**2','member.size<=8*1024**2').replace('seen=set()', 'seen=set();assert len(raw)<=17*1024**2')
    text=re.sub(r'^PAYLOAD=.*$','PAYLOAD='+repr(base64.b64encode(out.getvalue()).decode()),text,flags=re.M)
    text=re.sub(r'^EXPECTED=.*$','EXPECTED='+repr(expected),text,flags=re.M)
    (LIVE/'upload_pc_reference.py').write_text(text,encoding='utf-8')
    save('pc-reference-upload-pins.json',dict(files=expected,wire_sha256=hashlib.sha256(out.getvalue()).hexdigest()))
elif stage=='freeze_plan':
    native=json.loads((LIVE/'prepare_native_plan.native-result.json').read_text());assert native['native_exit_code']==0
    plan=native['result']['plan'];assert plan['release_plan']==json.loads((LIVE/'release-plan.json').read_text())
    save('frozen-live-transition-plan.json',plan)
elif stage=='pc_intent_copy':
    sys.path.insert(0,str(package))
    from scripts.paper_release_transition import _bounded,_verify_rollback,_hash
    prepared=json.loads((LIVE/'pc-intent-prepared.json').read_text());audit=Path(prepared['audit_dir']);target=ref/'pc-audit'
    assert not target.exists();shutil.copytree(audit,target)
    actual={p.relative_to(audit).as_posix():sha(p) for p in audit.rglob('*') if p.is_file()}
    assert actual=={p.relative_to(target).as_posix():sha(p) for p in target.rglob('*') if p.is_file()}
    plan=json.loads((LIVE/'frozen-live-transition-plan.json').read_text());intent=json.loads((target/'intent.json').read_text())
    assert intent['intent_hash']==prepared['intent_hash']==_hash({k:v for k,v in intent.items() if k!='intent_hash'})
    _bounded(_verify_rollback)(plan,dict(audit=target))
    save('pc-intent-offhost-verified.json',dict(status='pc_intent_and_rollback_verified_on_E',intent_hash=intent['intent_hash'],root_binding=intent['root_binding'],files=actual,copy=str(target)))
elif stage=='bind_intent':
    value=json.loads((LIVE/'prepare_worker_intent.native-result.json').read_text());assert value['native_exit_code']==0
    text=(LIVE/'apply_worker_transition.py').read_text()
    text=re.sub(r'^EXPECTED_INTENT=.*$','EXPECTED_INTENT='+repr(value['result']['intent_hash']),text,flags=re.M)
    (LIVE/'apply_worker_transition.py').write_text(text)
elif stage=='bind_validation':
    p=LIVE/'pc-native-launch-validated.json';value=json.loads(p.read_text());assert value['status']=='native_launch_validated_on_actual_pc'
    text=(LIVE/'release_successor_readiness.py').read_text()
    text=re.sub(r"pc_native_validation_sha256='[0-9a-f]{64}'","pc_native_validation_sha256="+repr(sha(p)),text)
    (LIVE/'release_successor_readiness.py').write_text(text)
else:raise ValueError('unknown stage')
print(json.dumps(dict(stage=stage,status='prepared',at=datetime.now(timezone.utc).isoformat())))
