"""Create one dedicated Nous device session; output only its verification link."""
from contextlib import redirect_stdout
import io
import json
import logging
import os
from pathlib import Path
import stat
import sys
from urllib.parse import urlsplit

PORTAL='https://portal.nousresearch.com'
INFERENCE='https://inference-api.nousresearch.com/v1'


def _safe(path):
    for member in (path,*path.parents):
        if member.exists():
            info=member.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
                raise ValueError('linked cloud authentication path')
    return path


def connect(project, *, auth=None, stream=None):
    project=Path(project).absolute();home=_safe(project/'state/cloud-auth')
    store=_safe(home/'auth.json')
    if store.exists():
        if store.stat().st_size>1024*1024:raise ValueError('cloud authentication store exceeds bound')
        body=json.loads(store.read_bytes())
        if isinstance(body,dict) and isinstance(body.get('providers',{}).get('nous'),dict) and body['providers']['nous'].get('refresh_token'):
            return 'already_connected'
        raise ValueError('existing cloud authentication requires operator inspection')
    stream=stream if stream is not None else sys.stdout
    home.mkdir(parents=True,exist_ok=True,mode=0o700)
    values={'HERMES_HOME':str(home),'HERMES_SHARED_AUTH_DIR':str(home/'shared')}
    previous={name:os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        if auth is None:
            from hermes_cli import auth
        verified=[]
        def verification(url,code):
            parsed=urlsplit(url)
            if parsed.scheme!='https' or parsed.netloc!='portal.nousresearch.com' or not isinstance(code,str) or not 1<=len(code)<=64:
                raise ValueError('Nous verification origin differs')
            print(json.dumps({'kind':'paper_cloud_auth','status':'verification_required','url':url,'code':code}),file=stream,flush=True)
            verified.append(True)
        with redirect_stdout(io.StringIO()):
            credentials=auth._nous_device_code_login(portal_base_url=PORTAL,inference_base_url=INFERENCE,
                client_id='hermes-cli',scope=auth.NOUS_INFERENCE_INVOKE_SCOPE,open_browser=False,
                insecure=False,on_verification=verification)
        if (not verified or not isinstance(credentials,dict) or credentials.get('portal_base_url')!=PORTAL or
                credentials.get('inference_base_url')!=INFERENCE or credentials.get('client_id')!='hermes-cli' or
                not isinstance(credentials.get('scope'),str) or 'inference:invoke' not in credentials['scope'].split() or
                'billing:manage' in credentials['scope'].split() or credentials.get('tls',{}).get('insecure') is not False or
                any(not isinstance(credentials.get(key),str) or not credentials[key] for key in ('access_token','refresh_token'))):
            raise ValueError('dedicated Nous session controls differ')
        # This application only uses the singleton Nous resolver, never the
        # agent credential pool. Use its official locked store writer directly.
        auth._persist_provider_state_to_store('nous',credentials,store,set_active=True)
        return 'connected'
    finally:
        for name,value in previous.items():
            if value is None:os.environ.pop(name,None)
            else:os.environ[name]=value


def main():
    logging.disable(logging.CRITICAL)
    try:status=connect(Path(__file__).resolve().parents[1])
    except (Exception,SystemExit):
        print(json.dumps({'kind':'paper_cloud_auth','status':'connection_failed'}),flush=True)
        return 1
    print(json.dumps({'kind':'paper_cloud_auth','status':status}),flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
