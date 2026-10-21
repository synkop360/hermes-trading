"""Review-locked prospective model-cost source; no inference or billing API."""
import argparse,base64,hashlib,json,os
from datetime import timedelta,datetime
from pathlib import Path

from hermes_trading.reflection_supervision import ReviewLease,_hash,_json,_now,_time
from hermes_trading.wallet_metrics import _decimal
from hermes_trading.paper_publication import _temporary,_sync_dir


def _stamp(value):
    if not isinstance(value,str) or datetime.fromisoformat(value.replace('Z','+00:00')).tzinfo is None:
        raise ValueError('cost source timestamp requires a timezone')
    return _time(value)


def _verified_zero(event,policy):
    try:
        meta=event['metadata'];proof=meta['pricing_verification'];prices=proof['pricing']
        if policy['model']['provider']!='nous' or proof.get('source')!='https://inference-api.nousresearch.com/v1/models':return False
        if proof.get('model')!=policy['model']['name'] or proof.get('payment')!='verified_zero_catalog_price':return False
        if not isinstance(prices,dict) or not {'prompt','completion'}<=set(prices):return False
        if any(_decimal(value)!=0 for value in prices.values()):return False
        if not timedelta(0)<=_stamp(event['occurred_at'])-_stamp(proof['verified_at'])<=timedelta(minutes=5):return False
        digest=proof.get('catalog_sha256')
        if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):return False
        raw=base64.b64decode(meta['raw_http_response_b64'],validate=True)
        if hashlib.sha256(raw).hexdigest()!=meta.get('raw_http_sha256'):return False
        response=json.loads(raw)
        if response.get('model')!=policy['model']['name']:return False
        for ours,theirs in (('input_tokens','prompt_tokens'),('output_tokens','completion_tokens')):
            reported=response.get('usage',{}).get(theirs)
            if type(meta.get(ours)) is not int or meta[ours]<0 or type(reported) is not int or reported!=meta[ours]:return False
        return event.get('quality')=='actual'
    except (ValueError,TypeError,KeyError,AttributeError):return False


def export_supervisor_usage(state_dir,account,*,now=None):
    """Publish complete in-scope inference receipts while new reviews are excluded.

    Fixed subscription costs and invoice reconciliation are separate. USD
    charges without EUR conversion remain unknown; an evidenced exact zero
    stays zero in EUR without an invented exchange rate.
    """
    state_dir=Path(state_dir)
    with ReviewLease(state_dir) as lease:
        at=_now(now);policy=lease.policy
        state=json.loads(lease.db.execute('SELECT state FROM supervisor WHERE id=1').fetchone()[0])
        start=_stamp(state['activated_at'])
        if not isinstance(account,dict) or type(account.get('schema_version')) is not int or account['schema_version']!=1:
            raise ValueError('usage source requires an explicit wallet account')
        if account.get('experiment_id')!=policy['experiment_id'] or account.get('currency')!='EUR':raise ValueError('usage scope differs from wallet')
        if _stamp(account.get('activated_at'))!=start:raise ValueError('usage inception differs from wallet')
        if start>at:raise ValueError('usage inception is future-dated')
        reviews={}
        for review_id,raw in lease.db.execute('SELECT review_id,record FROM reviews'):
            review=json.loads(raw)
            if review.get('review_id')!=review_id or not start<=_stamp(review['started_at'])<=at:raise ValueError('review source identity/time differs')
            if review.get('completed_at') is not None and not _stamp(review['started_at'])<=_stamp(review['completed_at'])<=at:
                raise ValueError('review completion is outside source coverage')
            reviews[review_id]=review
        pending=state.get('pending_review')
        audited_pending=[review for review in reviews.values() if review.get('outcome')=='pending']
        if audited_pending!= ([] if pending is None else [pending]):
            raise ValueError('pending supervisor audit is incomplete or conflicting')
        completed=[review for review in reviews.values() if review.get('outcome') in {'changed','no_change','rejected'}]
        accounted={identity for review in completed for identity in review['trade_ids']}
        if accounted!=set(state['completed_trade_ids']):raise ValueError('completed supervisor audit is incomplete')
        if any(review.get('outcome') not in {'pending','changed','no_change','rejected','failed','deferred'} for review in reviews.values()):
            raise ValueError('supervisor audit outcome is invalid')
        ledger=state_dir/'cost_events.jsonl';raw_costs=ledger.read_bytes() if ledger.exists() else b''
        matched={}
        for line in raw_costs.decode('utf-8').splitlines():
            if not line.strip():continue
            event=json.loads(line)
            occurred=_stamp(event.get('occurred_at'))
            if occurred<start:continue
            if occurred>at:raise ValueError('usage event is outside source coverage')
            if event.get('category') not in {'hermes','openai','tools'}:continue
            meta=event.get('metadata',{});review_id=meta.get('review_id')
            if review_id not in reviews:raise ValueError('orphan prospective usage requires reconciliation')
            if review_id in matched:raise ValueError('duplicate review usage receipt')
            expected=(policy['experiment_id'],_hash(policy),policy['model']['provider'],policy['model']['name'])
            actual=tuple(meta.get(k) for k in ('experiment_id','policy_hash','provider','model'))
            if actual!=expected or _stamp(meta.get('request_started_at'))!=_stamp(reviews[review_id]['started_at']):
                raise ValueError('usage receipt differs from review scope/model')
            if occurred<_stamp(meta['request_started_at']):raise ValueError('usage receipt precedes review')
            matched[review_id]=event
        events=[]
        for review_id,review in reviews.items():
            event=matched.get(review_id)
            if review.get('outcome')=='deferred':
                if (review.get('decision')!={'status':'deferred_before_dispatch','reason':'invocation_budget','model_dispatched':False}
                        or review['decision']['model_dispatched'] is not False
                        or 'request' not in review or event is not None
                        or any(key in review for key in ('response','publication_intent','publication'))
                        or review.get('completed_at') is None):
                    raise ValueError('deferred review does not prove an undispatched request')
                continue
            if event is None:raise ValueError('review cost receipt is missing; reconciliation required')
            response=review.get('response',{})
            if review.get('outcome')=='pending' and (not response.get('output') or response.get('process_error')):
                raise ValueError('pending request cannot assert complete usage coverage')
            original=event.get('amount');currency=event.get('currency');quality=event.get('quality')
            if quality not in {'actual','estimated','missing'}:raise ValueError('usage quality is invalid')
            if not isinstance(currency,str) or not currency:raise ValueError('usage currency is missing')
            amount=None if original is None else _decimal(original)
            if amount is not None and amount<0:raise ValueError('usage charge is negative')
            converted=None
            if quality!='missing' and amount is not None:
                if amount==0 and _verified_zero(event,policy):converted=amount
                elif amount>0 and currency=='EUR':converted=amount
            events.append({'id':'review:'+review_id,'occurred_at':event['occurred_at'],'currency':'EUR',
                'amount':None if converted is None else str(converted),'quality':'missing' if converted is None else quality,
                'original_amount':original,'original_currency':currency,'source_event_hash':_hash(event)})
        result={'schema_version':1,'experiment_id':policy['experiment_id'],'policy_hash':_hash(policy),'currency':'EUR',
            'scope_start':start.isoformat(),'covered_through':at.isoformat(),'scope':'supervised_inference_only',
            'fixed_subscription_included':False,'invoice_reconciled':False,'source_hashes':{'ledger':hashlib.sha256(raw_costs).hexdigest(),
                'reviews':_hash(reviews)},'model_usage':{'mode':'events','source':'single_writer_supervisor_usage_v1',
                    'coverage_start':start.isoformat(),'coverage_end':at.isoformat(),'events':sorted(events,key=lambda row:(row['occurred_at'],row['id']))}}
        # Bind evidence before publishing its projection. The existing review
        # lease remains exclusive through commit and filesystem replacement.
        lease.db.execute('CREATE TABLE IF NOT EXISTS usage_source_binding (id INTEGER PRIMARY KEY,account_hash TEXT)')
        lease.db.execute('CREATE TABLE IF NOT EXISTS usage_source_snapshots (at TEXT PRIMARY KEY,payload TEXT)')
        binding=lease.db.execute('SELECT account_hash FROM usage_source_binding WHERE id=1').fetchone()
        if binding is None:lease.db.execute('INSERT INTO usage_source_binding VALUES (1,?)',(_hash(account),))
        elif binding[0]!=_hash(account):raise ValueError('usage source wallet binding changed')
        previous=lease.db.execute('SELECT at,payload FROM usage_source_snapshots ORDER BY at DESC LIMIT 1').fetchone()
        if previous is not None:
            if at<_stamp(previous[0]):raise ValueError('usage source coverage regressed')
            old={row['id']:row for row in json.loads(previous[1])['model_usage']['events']}
            latest={row['id']:row for row in events}
            if any(latest.get(key)!=row for key,row in old.items()):raise ValueError('observed usage source receipt changed or disappeared')
            if any(row['id'] not in old and _stamp(row['occurred_at'])<=_stamp(previous[0]) for row in events):
                raise ValueError('backdated receipt contradicts complete usage coverage')
        frozen=lease.db.execute('SELECT payload FROM usage_source_snapshots WHERE at=?',(at.isoformat(),)).fetchone()
        if frozen is not None and frozen[0]!=_json(result):raise ValueError('conflicting usage evidence at frozen timestamp')
        lease.db.execute('INSERT OR IGNORE INTO usage_source_snapshots VALUES (?,?)',(at.isoformat(),_json(result)))
        lease.db.commit()
        target=state_dir/'supervisor_usage_snapshot.json'
        temporary=_temporary(target,(_json(result)+'\n').encode());os.replace(temporary,target);_sync_dir(state_dir)
        return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--state-dir',required=True);args=parser.parse_args()
    state=Path(args.state_dir);account=json.loads((state/'wallet_account.json').read_text(encoding='utf-8'))
    result=export_supervisor_usage(state,account)
    print(_json({'status':'exported','experiment_id':result['experiment_id'],'covered_through':result['covered_through'],
                'model_events':len(result['model_usage']['events']),'fixed_subscription_included':False}))


if __name__=='__main__':main()
