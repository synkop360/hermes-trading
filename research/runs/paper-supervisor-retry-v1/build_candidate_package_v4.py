"""Freeze the approved source delta locally; never installs or migrates state."""
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess

ROOT = Path(__file__).resolve().parents[3]
RUN = Path(__file__).resolve().parent
COMMIT = '20eb6af2a441c934618e124af30d72f411a3938a'


def extended(path):
    path = path.resolve()
    return Path('\\\\?\\'+str(path)) if os.name=='nt' and not str(path).startswith('\\\\?\\') else path


def sha(data):
    return hashlib.sha256(data).hexdigest()


def normalized(data):
    return data.replace(b'\r\n',b'\n')


if __name__=='__main__':
    old_name = ROOT/'research/runs/paper-discord-reporting-v1/release-upload-ed439e8'
    oldroot = extended(old_name)
    oldpins = json.loads(old_name.with_name(old_name.name+'.raw-sha256.json').read_text())
    original = {}
    for name,digest in oldpins.items():
        parts = PurePosixPath(name)
        assert not parts.is_absolute() and '..' not in parts.parts
        data = (oldroot/name).read_bytes()
        assert sha(data)==digest,name
        original[name] = data
    oldplan = json.loads((ROOT/'research/runs/paper-discord-reporting-v1/live-cutover/frozen-live-transition-plan.json').read_text())['release_plan']
    oldmap = oldplan['new']
    for name,digest in oldmap['runtime'].items():assert sha(original['hermes_trading/'+name])==digest,name
    assert sha(original['scripts/hermes_watch.py'])==oldmap['watcher']
    assert sha(original['scripts/native_hermes_watch.py'])==oldmap['native_launcher']
    additions = ['tests/test_supervisor_snapshot_retry.py','tests/test_paper_snapshot_release_transition.py',
        'research/runs/paper-discord-reporting-v1/live-cutover/frozen-live-transition-plan.json',
        'docs/superpowers/specs/2026-10-25-supervisor-snapshot-contention-design.md',
        'docs/superpowers/plans/2026-10-25-supervisor-snapshot-contention.md']
    inventory = json.loads((RUN/'historical-fixture-inventory.json').read_text())
    historical = inventory['files']
    assert len(historical)==57 and sum(x['bytes'] for x in historical.values())==67167
    additions += sorted(historical) + ['.gitattributes','research/runs/paper-supervisor-retry-v1/historical-fixture-inventory.json']
    names = sorted(set(oldpins)|set(additions))
    assert all('\n' not in name for name in names)
    request = ''.join(f'{COMMIT}:{name}\n' for name in names).encode()
    output = subprocess.run(['git','cat-file','--batch'],cwd=ROOT,input=request,capture_output=True,check=True)
    stream = io.BytesIO(output.stdout)
    staged = {}; changed = []
    allowed = {'hermes_trading/supervisor_sync.py','scripts/paper_release_transition.py'}
    for name in names:
        header = stream.readline().decode().strip().split()
        assert len(header)==3 and header[1]=='blob',(name,header)
        blob = stream.read(int(header[2]));assert stream.read(1)==b'\n'
        prior = original.get(name)
        if prior is not None and normalized(prior)==normalized(blob):
            data = prior
        else:
            if name.endswith('.py') and name not in allowed and name not in additions:
                raise AssertionError(('unexpected Python source delta',name))
            data = blob;changed.append(name)
        assert normalized(data)==normalized(blob),name
        staged[name] = data
    assert stream.read()==b''
    for relative,info in historical.items():
        assert len(staged[relative])==info['bytes'] and sha(staged[relative])==info['sha256'],relative
    newmap = deepcopy(oldmap)
    newmap['runtime'] = {name[len('hermes_trading/'):]:sha(data) for name,data in staged.items()
                        if name.startswith('hermes_trading/') and name.endswith('.py')}
    assert set(newmap['runtime'])==set(oldmap['runtime'])
    delta = [name for name in newmap['runtime'] if newmap['runtime'][name]!=oldmap['runtime'][name]]
    assert delta==['supervisor_sync.py'],delta
    assert sha(staged['scripts/hermes_watch.py'])==oldmap['watcher']
    assert sha(staged['scripts/native_hermes_watch.py'])==oldmap['native_launcher']
    plan = dict(old_commit='ed439e897a4dd388fdcf6425e7e8b6171d5a88a9',new_commit=COMMIT,
                target=oldplan['target'],old=oldmap,new=newmap)
    import sys
    sys.path.insert(0,str(ROOT))
    from scripts.paper_release_transition import validate_release_plan,_purpose
    from hermes_trading.reflection_supervision import _hash
    validate_release_plan(plan)
    # Keep the packaged test fixture's ordinary path below Windows MAX_PATH.
    name = ROOT.parent/('hermes-snapshot-'+COMMIT[:7])
    assert name.resolve().is_relative_to(ROOT.parent.resolve())
    destination = extended(name)
    assert not destination.exists(),'preserve any existing export; reconcile before retry'
    for relative,data in staged.items():
        path = destination/relative
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(data)
    raw = {relative:sha((destination/relative).read_bytes()) for relative in staged}
    assert all(raw[relative]==sha(data) for relative,data in staged.items())
    name.with_name(name.name+'.raw-sha256.json').write_text(json.dumps(raw,indent=2)+'\n',encoding='utf-8')
    (RUN/'candidate-release-plan-v4.json').write_text(json.dumps(plan,indent=2)+'\n',encoding='utf-8')
    value = dict(candidate_commit=COMMIT,export=str(name),export_files=len(raw),
        predecessor_export_files_verified=len(oldpins),runtime_files=len(newmap['runtime']),runtime_delta=delta,
        watcher_and_native_unchanged=True,source_matches_commit_after_newline_normalization=True,
        other_updated_paths=changed,new_release_hash=_hash(newmap),new_runtime_map_hash=_hash(newmap['runtime']),
        transition_purpose=_purpose(plan),windows_extended_paths_used=os.name=='nt',
        deployed=False,actual_guards_changed=False)
    value['historical_fixture_files_verified'] = 57
    value['historical_fixture_bytes_verified'] = 67167
    value['prior_program_candidate'] = 'a79fb936fd970eb9b25a23edf6522a2e34f6e17f'
    value['supersedes'] = 'candidate-package-verification-v3.json: runtime/test programs unchanged; all 57 omitted historical inputs now tracked and included with original raw hashes'
    (RUN/'candidate-package-verification-v4.json').write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(value,indent=2))
