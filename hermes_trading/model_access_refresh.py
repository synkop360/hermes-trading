"""Read-only renewal of an already reviewed free Nous model pin.

No inference, tools, fallback, billing change or strategy write. A live catalog
and session check is availability metadata, not an inference connectivity test.
"""
import argparse,hashlib,json,os,sys
from datetime import timedelta
from decimal import DecimalException
from pathlib import Path
from urllib.request import Request,build_opener

from hermes_trading.proposal_transport import BASE,NoRedirect,_credentials,_exchange,_pricing
from hermes_trading.paper_publication import _release_hashes,_temporary,_sync_dir
from hermes_trading.reflection_supervision import _connect,_hash,_json,_now,assert_writer_owner
from hermes_trading.supervisor_usage_source import _stamp

SUBSCRIPTION_URL='https://portal.nousresearch.com/api/billing/subscription'


def _baseline(policy,access):
    if not isinstance(policy,dict) or not isinstance(access,dict):raise ValueError('model access requires reviewed objects')
    pin=policy.get('model',{})
    if not isinstance(pin,dict) or pin.get('provider')!='nous' or not isinstance(pin.get('name'),str) or not pin['name'].strip() or pin['name'].lower()=='auto':
        raise ValueError('free renewal requires an exact Nous pin')
    if type(access.get('schema_version')) is not int or access['schema_version']!=1:
        raise ValueError('model access schema is invalid')
    if (access.get('provider'),access.get('model'),access.get('policy_hash'))!=('nous',pin['name'],_hash(policy)):
        raise ValueError('model access differs from reviewed pin/owner')
    if access.get('payment')!='verified_free' or access.get('paid_calls_authorized',False) is not False or access.get('tools_allowed',False) is not False:
        raise ValueError('renewal cannot add paid calls or tools')
    if not isinstance(access.get('provenance_ref'),str) or not access['provenance_ref'].strip():raise ValueError('original reviewed provenance is required')


def _subscription():
    # Use only the read-only billing route, never a privileged spending method.
    from hermes_trading.proposal_transport import _environment_credentials
    dedicated=_environment_credentials()
    if dedicated is None:
        from hermes_cli.nous_billing import _resolve_token_and_base
        token,base=_resolve_token_and_base(use_cache=False)
    else:
        token,base=dedicated['HERMES_NOUS_PORTAL_TOKEN'],'https://portal.nousresearch.com'
    if base.rstrip('/')!='https://portal.nousresearch.com':raise ValueError('unexpected account endpoint')
    request=Request(SUBSCRIPTION_URL,method='GET',headers={'Authorization':'Bearer '+token,'Accept':'application/json'})
    with build_opener(NoRedirect()).open(request,timeout=20) as response:
        raw=response.read(1024*1024+1)
    if len(raw)>1024*1024:raise ValueError('account metadata exceeds bound')
    return json.loads(raw)


def probe_free_access(policy,existing,*,now=None):
    _baseline(policy,existing)
    credentials=_credentials()
    if credentials.get('base_url','').rstrip('/')!=BASE or not isinstance(credentials.get('api_key'),str) or not credentials['api_key']:
        raise ValueError('unexpected inference endpoint or missing credential')
    captured=[]
    catalog=_exchange('GET','/models',token=credentials['api_key'],raw_capture=captured.append)
    try:pricing=_pricing(catalog,existing['model'],True)
    except DecimalException as error:raise ValueError('catalog pricing is invalid') from error
    subscription=_subscription()
    if not isinstance(subscription,dict) or subscription.get('context')!='personal' or 'current' not in subscription or subscription['current'] is not None:
        raise ValueError('free personal subscription status is unverified')
    if len(captured)!=1 or not isinstance(captured[0],bytes):raise ValueError('catalog evidence is unavailable')
    at=_now(now)
    return {'schema_version':1,'provider':'nous','model':existing['model'],'policy_hash':_hash(policy),
        'payment':'verified_free','provenance_ref':existing['provenance_ref'],'tools_allowed':False,'paid_calls_authorized':False,
        'verified_at':at.isoformat(),'valid_until':(at+timedelta(minutes=5)).isoformat(),
        'renewal':{'kind':'read_only_catalog_and_session','catalog_source':BASE+'/models','catalog_sha256':hashlib.sha256(captured[0]).hexdigest(),
                   'pricing':{key:str(value) for key,value in pricing['pricing'].items()},'subscription_source':SUBSCRIPTION_URL,
                   'subscription_context':'personal','subscription_current_is_null':True,'inference_test_performed':False}}


def validate_renewed_access(policy,access,*,now=None):
    _baseline(policy,access);at=_now(now)
    verified=_stamp(access.get('verified_at'));expiry=_stamp(access.get('valid_until'))
    if access['verified_at']!=verified.isoformat() or access['valid_until']!=expiry.isoformat():raise ValueError('renewal timestamps require canonical UTC')
    if not verified<=at<expiry or expiry-verified!=timedelta(minutes=5):raise ValueError('model renewal timing is invalid')
    proof=access.get('renewal')
    if not isinstance(proof,dict) or proof.get('kind')!='read_only_catalog_and_session' or proof.get('catalog_source')!=BASE+'/models':
        raise ValueError('model renewal source is invalid')
    digest=proof.get('catalog_sha256')
    if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise ValueError('catalog hash is invalid')
    if (proof.get('subscription_source'),proof.get('subscription_context'),proof.get('subscription_current_is_null'),proof.get('inference_test_performed'))!=(SUBSCRIPTION_URL,'personal',True,False):
        raise ValueError('model renewal session proof is invalid')
    # Literal flags matter: integers must not masquerade as evidence booleans.
    if proof.get('subscription_current_is_null') is not True or proof.get('inference_test_performed') is not False:
        raise ValueError('model renewal session flags are invalid')
    try:_pricing({'data':[{'id':access['model'],'pricing':proof.get('pricing')}]},access['model'],True)
    except DecimalException as error:raise ValueError('renewed pricing is invalid') from error


def build_access_request(access,policy,account):
    return json.loads(_json({'schema_version':1,'access':access,'access_hash':_hash(access),'policy_hash':_hash(policy),
                            'account_hash':_hash(account),'worker_release':_release_hashes()}))


def install_free_access(state_dir,request,*,env,now=None):
    state_dir=Path(state_dir);at=_now(now)
    if env.get('HERMES_TRADING_MODE','').strip().lower() not in {'paper','training','train','demo'}:raise ValueError('renewal requires explicit paper mode')
    policy=assert_writer_owner(state_dir,'external_llm')
    account=json.loads((state_dir/'wallet_account.json').read_text(encoding='utf-8'))
    old=json.loads((state_dir/'model_access.json').read_text(encoding='utf-8'))
    _baseline(policy,old)
    if not isinstance(account,dict) or account.get('experiment_id')!=policy['experiment_id'] or account.get('currency')!='EUR':raise ValueError('renewal account differs')
    if not isinstance(request,dict) or type(request.get('schema_version')) is not int or request['schema_version']!=1:raise ValueError('renewal request schema is invalid')
    if (request.get('policy_hash'),request.get('account_hash'),request.get('worker_release'))!=(_hash(policy),_hash(account),_release_hashes()):
        raise ValueError('renewal owner/account/release differs')
    access=request.get('access');validate_renewed_access(policy,access,now=at)
    if access['provenance_ref']!=old['provenance_ref'] or request.get('access_hash')!=_hash(access):raise ValueError('renewal changes original provenance or hash')
    stamp=access['verified_at'];db=_connect(state_dir/'model_access_refresh.sqlite')
    try:
        db.execute('CREATE TABLE IF NOT EXISTS binding (id INTEGER PRIMARY KEY,payload TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS renewals (at TEXT PRIMARY KEY,access_hash TEXT,receipt TEXT)')
        bound=_json({'policy':_hash(policy),'account':_hash(account),'release':_hash(request['worker_release']),'provenance':old['provenance_ref']})
        previous=db.execute('SELECT payload FROM binding WHERE id=1').fetchone()
        if previous is None:db.execute('INSERT INTO binding VALUES (1,?)',(bound,))
        elif previous[0]!=bound:raise ValueError('renewal binding changed; reconciliation required')
        previous=db.execute('SELECT at,access_hash FROM renewals ORDER BY at DESC LIMIT 1').fetchone()
        if _stamp(stamp)<_stamp(old['verified_at']) or (previous is not None and _stamp(stamp)<_stamp(previous[0])):
            raise ValueError('renewal observation regressed')
        if previous is not None and _stamp(stamp)==_stamp(previous[0]) and _hash(access)!=previous[1]:raise ValueError('conflicting renewal at frozen timestamp')
        frozen=db.execute('SELECT receipt FROM renewals WHERE at=?',(stamp,)).fetchone()
        receipt=json.loads(frozen[0]) if frozen is not None else {'status':'renewed','access_hash':_hash(access),
            'policy_hash':_hash(policy),'account_hash':_hash(account),'verified_at':stamp,'valid_until':access['valid_until'],'imported_at':at.isoformat()}
        db.execute('INSERT OR IGNORE INTO renewals VALUES (?,?,?)',(stamp,_hash(access),_json(receipt)));db.commit()
        target=state_dir/'model_access.json';temporary=_temporary(target,(_json(access)+'\n').encode())
        try:os.replace(temporary,target)
        finally:temporary.unlink(missing_ok=True)
        _sync_dir(state_dir);return receipt
    except BaseException:db.rollback();raise
    finally:db.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['probe','install']);parser.add_argument('--state-dir');args=parser.parse_args()
    try:
        raw=sys.stdin.buffer.read(65537)
        if len(raw)>65536:raise ValueError('renewal request exceeds bound')
        request=json.loads(raw)
        result=probe_free_access(request['policy'],request['existing']) if args.action=='probe' else install_free_access(args.state_dir,request,env=os.environ)
        print(_json(result));return 0
    except Exception as error:
        # Never echo provider/auth exception payloads or credentials.
        print(_json({'status':'unverified','error_type':type(error).__name__}));return 1


if __name__=='__main__':raise SystemExit(main())
