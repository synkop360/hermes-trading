"""Release-bound cost-source transfer over the existing authenticated channel.

This importer records evidence only. It cannot activate an experiment, change a
strategy, infer billing prices, extend source coverage or submit an order.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import uuid

from hermes_trading.paper_publication import _release_hashes, _temporary, _sync_dir
from hermes_trading.reflection_supervision import _connect, _hash, _json, _now, assert_writer_owner
from hermes_trading.supervisor_usage_source import _stamp
from hermes_trading.wallet_metrics import _decimal

MAX_SOURCE_BYTES = 16 * 1024 * 1024


def _digest(value):
    if not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('usage source digest is invalid')


def validate_usage_snapshot(snapshot, account, policy_hash, *, now=None):
    at=_now(now)
    if not isinstance(account,dict) or account.get('currency')!='EUR':raise ValueError('usage wallet is invalid')
    if not isinstance(snapshot,dict) or type(snapshot.get('schema_version')) is not int or snapshot['schema_version']!=1:
        raise ValueError('usage source schema is invalid')
    if len(_json(snapshot).encode())>MAX_SOURCE_BYTES:raise ValueError('usage source exceeds size limit')
    if (snapshot.get('experiment_id'),snapshot.get('currency'),snapshot.get('policy_hash'))!=(account.get('experiment_id'),'EUR',policy_hash):
        raise ValueError('usage source scope/policy differs from wallet')
    _digest(policy_hash)
    start=_stamp(account.get('activated_at'));end=_stamp(snapshot.get('covered_through'))
    if _stamp(snapshot.get('scope_start'))!=start or not start<=end<=at:
        raise ValueError('usage source coverage is invalid')
    if snapshot.get('scope')!='supervised_inference_only' or snapshot.get('fixed_subscription_included') is not False or snapshot.get('invoice_reconciled') is not False:
        raise ValueError('usage source cannot assert subscription or invoice coverage')
    hashes=snapshot.get('source_hashes')
    if not isinstance(hashes,dict) or set(hashes)!={'ledger','reviews'}:raise ValueError('usage source evidence hashes are missing')
    for digest in hashes.values():_digest(digest)
    stream=snapshot.get('model_usage')
    if not isinstance(stream,dict) or stream.get('mode')!='events' or stream.get('source')!='single_writer_supervisor_usage_v1':
        raise ValueError('usage source adapter is invalid')
    if _stamp(stream.get('coverage_start'))!=start or _stamp(stream.get('coverage_end'))!=end:
        raise ValueError('usage event coverage differs from source coverage')
    events=stream.get('events')
    if not isinstance(events,list) or len(events)>100000:raise ValueError('usage events are invalid')
    seen=set()
    for event in events:
        if not isinstance(event,dict) or not isinstance(event.get('id'),str) or not event['id'].startswith('review:'):
            raise ValueError('usage event identity is invalid')
        uuid.UUID(event['id'].removeprefix('review:'))
        if event['id'] in seen:raise ValueError('duplicate usage event')
        seen.add(event['id'])
        if event.get('currency')!='EUR' or not start<=_stamp(event.get('occurred_at'))<=end:
            raise ValueError('usage event currency/coverage is invalid')
        quality=event.get('quality');amount=event.get('amount')
        if not isinstance(quality,str) or quality not in {'actual','estimated','missing'} or (amount is None)!=(quality=='missing'):
            raise ValueError('usage event charge quality is invalid')
        if amount is not None and _decimal(amount)<0:raise ValueError('usage charge cannot be negative')
        _digest(event.get('source_event_hash'))
        if not isinstance(event.get('original_currency'),str) or not event['original_currency']:
            raise ValueError('usage original currency is missing')
        if event.get('original_amount') is not None and _decimal(event['original_amount'])<0:
            raise ValueError('usage original charge cannot be negative')
    return stream


def build_usage_request(snapshot, policy, account):
    return json.loads(_json({'schema_version':1,'policy_hash':_hash(policy),'account_hash':_hash(account),
        'source_sha256':_hash(snapshot),'snapshot':snapshot,'worker_release':_release_hashes()}))


def install_supervisor_usage(state_dir, request, *, env, now=None):
    state_dir=Path(state_dir);at=_now(now)
    if env.get('HERMES_TRADING_MODE','').strip().lower() not in {'paper','training','train','demo'}:
        raise ValueError('usage import requires explicit paper mode')
    policy=assert_writer_owner(state_dir,'external_llm')
    if policy is None:raise ValueError('usage import requires an explicit experiment owner')
    account=json.loads((state_dir/'wallet_account.json').read_text(encoding='utf-8'))
    if not isinstance(account,dict) or type(account.get('schema_version')) is not int or account['schema_version']!=1 or account.get('currency')!='EUR' or account.get('experiment_id')!=policy['experiment_id']:
        raise ValueError('usage import wallet account is invalid')
    sources=json.loads((state_dir/'wallet_cost_sources.json').read_text(encoding='utf-8'))
    if not isinstance(sources,dict) or not isinstance(sources.get('categories'),dict):raise ValueError('usage import source plan is invalid')
    configured=sources['categories'].get('ai',{})
    if not isinstance(configured,dict):raise ValueError('usage import AI source plan is invalid')
    if configured.get('mode')!='supervised' or configured.get('policy_hash')!=_hash(policy):
        raise ValueError('usage import is not configured for this policy')
    if not isinstance(request,dict) or type(request.get('schema_version')) is not int or request['schema_version']!=1:
        raise ValueError('usage import request schema is invalid')
    if (request.get('policy_hash'),request.get('account_hash'),request.get('worker_release'))!=(_hash(policy),_hash(account),_release_hashes()):
        raise ValueError('usage import owner/account/release differs')
    source=request.get('snapshot');stream=validate_usage_snapshot(source,account,_hash(policy),now=at)
    digest=_hash(source)
    if request.get('source_sha256')!=digest:raise ValueError('usage import source hash differs')
    stamp=_stamp(source['covered_through']).isoformat()
    db=_connect(state_dir/'supervisor_usage_feed.sqlite')
    try:
        db.execute('CREATE TABLE IF NOT EXISTS binding (id INTEGER PRIMARY KEY,payload TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY,payload TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS snapshots (at TEXT PRIMARY KEY,source_hash TEXT,receipt TEXT)')
        bound=_json({'account':_hash(account),'policy':_hash(policy),'release':_hash(request['worker_release'])})
        previous=db.execute('SELECT payload FROM binding WHERE id=1').fetchone()
        if previous is None:db.execute('INSERT INTO binding VALUES (1,?)',(bound,))
        elif previous[0]!=bound:raise ValueError('usage import binding changed; reconciliation required')
        previous=db.execute('SELECT at,source_hash,receipt FROM snapshots ORDER BY at DESC LIMIT 1').fetchone()
        if previous is not None:
            if _stamp(stamp)<_stamp(previous[0]):raise ValueError('usage coverage regressed')
            if stamp==previous[0] and digest!=previous[1]:raise ValueError('conflicting usage at frozen coverage')
        old=dict(db.execute('SELECT event_id,payload FROM events'))
        latest={row['id']:_json(row) for row in stream['events']}
        if any(latest.get(key)!=payload for key,payload in old.items()):
            raise ValueError('usage event changed or disappeared')
        for event in stream['events']:
            if event['id'] not in old and previous is not None and _stamp(event['occurred_at'])<=_stamp(previous[0]):
                raise ValueError('backdated usage event contradicts complete coverage')
            db.execute('INSERT OR IGNORE INTO events VALUES (?,?)',(event['id'],_json(event)))
        frozen=db.execute('SELECT receipt FROM snapshots WHERE at=?',(stamp,)).fetchone()
        receipt=json.loads(frozen[0]) if frozen is not None else {'status':'imported','experiment_id':policy['experiment_id'],
            'policy_hash':_hash(policy),'account_hash':_hash(account),'source_sha256':digest,'covered_through':stamp,'imported_at':at.isoformat()}
        db.execute('INSERT OR IGNORE INTO snapshots VALUES (?,?,?)',(stamp,digest,_json(receipt)))
        db.commit()
        # Keep the exclusive lease until projection is durable; stale importers
        # cannot replace a newer source after its audit commit.
        target=state_dir/'supervisor_usage_snapshot.json';temporary=_temporary(target,(_json(source)+'\n').encode())
        try:os.replace(temporary,target)
        finally:temporary.unlink(missing_ok=True)
        _sync_dir(state_dir)
        return receipt
    except BaseException:
        db.rollback();raise
    finally:db.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--state-dir',required=True);args=parser.parse_args()
    raw=sys.stdin.buffer.read(MAX_SOURCE_BYTES*2+1)
    if len(raw)>MAX_SOURCE_BYTES*2:raise ValueError('usage import request exceeds size limit')
    receipt=install_supervisor_usage(args.state_dir,json.loads(raw),env=os.environ)
    print(_json(receipt))


if __name__=='__main__':main()
