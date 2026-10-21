"""Retained quote replay watermarks and coordinated-retention read gates.

Resolved typed incidents use the existing canonical schema. They are control
state, never evidence that a strategy or a storage domain is qualified.
"""
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3

from hermes_trading.reflection_supervision import _hash,_json

FLOOR='archive:quote-floor:'
RECEIPT='archive:quote-receipt:'
PURPOSE='paired_quote_retention_v1'
MAX_PLAN_BYTES=4*1024**2
MAX_ITEMS=4096
MAX_OPERATIONS=1024


def timestamp(value):
    if not isinstance(value,str) or not 1<=len(value)<=64:raise ValueError('quote retention timestamp differs')
    result=datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:raise ValueError('quote retention timestamp needs timezone')
    return result


def envelope(payload):return _json(dict(payload=payload,sha256=_hash(payload)))


def unpack(raw):
    if not isinstance(raw,str) or len(raw.encode())>MAX_PLAN_BYTES:raise ValueError('quote retention record exceeds bound')
    try:
        value=json.loads(raw)
        if (type(value) is not dict or set(value)!={'payload','sha256'} or
                type(value['payload']) is not dict or _hash(value['payload'])!=value['sha256']):
            raise ValueError('quote retention record checksum differs')
        return value['payload']
    except (TypeError,RecursionError,json.JSONDecodeError) as error:
        raise ValueError('quote retention record is malformed') from error


def floor_key(asset,timeframe):return FLOOR+_hash([asset,timeframe])


def read_floor(db,asset,timeframe):
    row=db.execute('SELECT status,reason FROM incidents WHERE incident_id=?',(floor_key(asset,timeframe),)).fetchone()
    if row is None:return None
    value=unpack(row[1])
    if (row[0]!='resolved' or set(value)!={'schema_version','purpose','asset','timeframe','through','operation_id','plan_sha256'} or
            type(value['schema_version']) is not int or value['schema_version']!=1 or value['purpose']!=PURPOSE or
            value['asset']!=asset or value['timeframe']!=timeframe or not timeframe.startswith('paper-position:') or
            len(timeframe)<=len('paper-position:')):
        raise ValueError('quote retention replay floor differs')
    timestamp(value['through'])
    from hermes_trading.archive_journal import _digest
    _digest(value['plan_sha256'])
    if not isinstance(value['operation_id'],str) or len(value['operation_id'])!=32:
        raise ValueError('quote retention floor operation differs')
    return value


def quote_already_retired(db,asset,timeframe,close_timestamp):
    if not timeframe.startswith('paper-position:'):return False
    value=read_floor(db,asset,timeframe)
    if value is not None and timestamp(close_timestamp)<=timestamp(value['through']):return True
    from hermes_trading.archive_quote_checkpoint_state import read_local,terminal_quote_retired
    local=read_local(db)
    if local is None:return False
    retained=local['payload']['active_floors'].get(floor_key(asset,timeframe))
    if retained is not None and timestamp(close_timestamp)<=timestamp(retained['through']):return True
    return terminal_quote_retired(db,asset,timeframe,close_timestamp,local['payload']['terminal'])


def store_incident(db,key,payload,at):
    db.execute('''INSERT INTO incidents(incident_id,status,reason,severity,revision,opened_at,resolved_at)
        VALUES(?,'resolved',?,'info',1,?,?) ON CONFLICT(incident_id) DO UPDATE SET
        status='resolved',reason=excluded.reason,revision=incidents.revision+1,resolved_at=excluded.resolved_at''',
        (key,envelope(payload),at,at))


def operations(db,check=lambda:None,record_bytes=lambda size:None):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='paired_quote_retention'").fetchone():return []
    from hermes_trading.archive_journal import ACCOUNTS,_digest
    result=[]
    for row in db.execute('SELECT operation_id,plan,progress FROM paired_quote_retention ORDER BY rowid LIMIT ?', (MAX_OPERATIONS+1,)):
        check()
        record_bytes(len(row[1].encode())+len(row[2].encode()))
        if len(result)>=MAX_OPERATIONS:raise ValueError('quote retention operation bound exceeded')
        plan=unpack(row[1]);progress=unpack(row[2])
        if (set(plan)!={'schema_version','purpose','operation_id','archive_operation_id','archive_sha256','cohort_sha256','cut_at','accounts'} or
                plan['schema_version']!=1 or type(plan['schema_version']) is not int or plan['purpose']!=PURPOSE or
                plan['operation_id']!=row[0] or not isinstance(row[0],str) or len(row[0])!=32 or
                any(c not in '0123456789abcdef' for c in row[0]) or
                not isinstance(plan['archive_operation_id'],str) or len(plan['archive_operation_id'])!=32 or
                any(c not in '0123456789abcdef' for c in plan['archive_operation_id']) or
                type(plan['accounts']) is not dict or set(plan['accounts'])!=ACCOUNTS):
            raise ValueError('quote retention plan scope differs')
        _digest(plan['cohort_sha256']);_digest(plan['archive_sha256']);timestamp(plan['cut_at'])
        if (set(progress)!={'status','accounts'} or progress['status'] not in ('pending','complete') or
                type(progress['accounts']) is not dict or set(progress['accounts'])!=ACCOUNTS):
            raise ValueError('quote retention cohort progress differs')
        count=0
        for name,account in plan['accounts'].items():
            if (type(account) is not dict or set(account)!={'directory','items'} or type(account['items']) is not list or
                    not isinstance(account['directory'],str) or Path(account['directory']).name!=account['directory']):
                raise ValueError('quote retention account plan differs')
            count+=len(account['items'])
            if count>MAX_ITEMS:raise ValueError('quote retention item bound exceeded')
            seen=set()
            for item in account['items']:
                check()
                if (type(item) is not dict or set(item)!={'decision_id','asset','timeframe','at','bundle_sha256'} or
                        not isinstance(item['decision_id'],str) or item['decision_id'] in seen or
                        not isinstance(item['asset'],str) or not isinstance(item['timeframe'],str) or
                        not item['timeframe'].startswith('paper-position:')):
                    raise ValueError('quote retention item differs')
                seen.add(item['decision_id']);timestamp(item['at']);_digest(item['bundle_sha256'])
            state=progress['accounts'][name]
            if (type(state) is not dict or set(state)!={'done','stage','orphans'} or type(state['done']) is not bool or
                    type(state['orphans']) is not list or len(state['orphans'])>4):
                raise ValueError('quote retention account progress differs')
            if state['stage'] is not None:
                stage=state['stage']
                if (type(stage) is not dict or set(stage)!={'name','identity'} or not isinstance(stage['name'],str) or
                        Path(stage['name']).name!=stage['name'] or not stage['name'].startswith('.quote-'+row[0]+'-') or
                        (stage['identity'] is not None and (type(stage['identity']) is not list or len(stage['identity'])!=2 or
                         any(type(n) is not int or n<0 for n in stage['identity'])))):
                    raise ValueError('quote retention original stage ownership differs')
            if state['done'] and state['stage'] is not None:raise ValueError('quote retention completed stage differs')
        if progress['status']=='complete' and not all(s['done'] for s in progress['accounts'].values()):
            raise ValueError('quote retention complete cohort differs')
        result.append((plan,progress))
    if sum(progress['status']=='pending' for _,progress in result)>1:raise ValueError('multiple partial quote retention operations')
    return result


def assert_quote_retention_complete(db):
    rows=operations(db)
    from hermes_trading.archive_quote_checkpoint_state import context
    if context(db)[1] is not None or any(progress['status']!='complete' for _,progress in rows):raise ValueError('quote retention cohort is incomplete')
    return rows


def assert_quote_cohort(root,*,db=None,manifest=None,protective=False,sealed=False,check=lambda:None):
    """Require all eight local receipts; absence of global metadata is not fresh state.

    Only the execution caller validates the existing protective admission. This
    boolean never grants publication, capture or measured-report authority.
    """
    root=Path(root)
    check()
    if manifest is None:manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    if db is None:
        from hermes_trading.paired_primary import resolve_primary
        primary=resolve_primary(manifest).profile_directory(manifest,'adaptive')
        shared=root/primary/'paper_publications.sqlite'
        if not shared.is_file():
            # A first legacy worker has not yet created its journal. Local
            # retirement markers distinguish this from lost global control.
            for profile in manifest['profiles']:
                check();path=root/profile['directory']/'trading.db'
                if not path.is_file():continue
                suffix='?mode=ro&immutable=1' if sealed else '?mode=ro'
                with closing(sqlite3.connect(path.resolve().as_uri()+suffix,uri=True,timeout=0)) as account:
                    account.set_progress_handler(lambda:check() or 0,1000)
                    if account.execute("SELECT 1 FROM sqlite_master WHERE name='incidents'").fetchone() and account.execute(
                            "SELECT 1 FROM incidents WHERE incident_id LIKE 'archive:quote-%' LIMIT 1").fetchone():
                        raise ValueError('quote retention shared journal is missing after account transition')
            return False
        suffix='?mode=ro&immutable=1' if sealed else '?mode=ro'
        with closing(sqlite3.connect(shared.resolve().as_uri()+suffix,uri=True,timeout=0)) as source:
            return assert_quote_cohort(root,db=source,manifest=manifest,protective=protective,sealed=sealed,check=check)
    from hermes_trading import archive_quote_checkpoint_state as checkpoint
    current,pending,cp_progress=checkpoint.context(db)
    rows=operations(db,check)
    ordinals=dict(db.execute('SELECT operation_id,rowid FROM paired_quote_retention')) if rows else {}
    first=1 if current is None else current['through_ordinal']+1
    if sorted(ordinals.values())!=list(range(first,first+len(rows))):
        raise ValueError('quote checkpoint detailed ordinals are not contiguous')
    if current is not None:
        if current['cohort_sha256']!=_hash(manifest) or any(value<=current['through_ordinal'] for value in ordinals.values()):
            raise ValueError('quote checkpoint original prefix resurrected or cohort differs')
        if any(value>current['legacy_operation_highwater'] and key!=checkpoint.operation_id(_hash(manifest),value)
               for key,value in ordinals.items()):raise ValueError('quote checkpoint request ordinal identity differs')
    incomplete=pending is not None or any(progress['status']!='complete' for _,progress in rows)
    for profile in manifest['profiles']:
        check()
        path=root/profile['directory']/'trading.db'
        if not path.exists():
            if rows or current is not None or pending is not None:raise ValueError('quote retention account is missing')
            continue
        name=f"{profile['arm']}_{profile['wallet_eur']}"
        suffix='?mode=ro&immutable=1' if sealed else '?mode=ro'
        with closing(sqlite3.connect(path.resolve().as_uri()+suffix,uri=True,timeout=0)) as account:
            account.set_progress_handler(lambda:check() or 0,1000)
            if not account.execute("SELECT 1 FROM sqlite_master WHERE name='incidents'").fetchone():
                if rows or current is not None or pending is not None:raise ValueError('quote retention account control is missing')
                continue
            authority=checkpoint.account_authority(account,name,current,pending,cp_progress,check)
            if authority is not None and (authority['cohort_sha256']!=_hash(manifest) or
                    authority['accounts'][name]['directory']!=profile['directory']):
                raise ValueError('quote checkpoint account cohort differs')
            through=0 if authority is None else authority['through_ordinal']
            effective=[(p,s) for p,s in rows if ordinals[p['operation_id']]>through]
            plans={p['operation_id']:(p,s) for p,s in effective}
            base={} if authority is None else authority['accounts'][name]['active_floors']
            authorized={(floor['asset'],floor['timeframe']):timestamp(floor['through']) for floor in base.values()}
            applied={}
            for plan,progress in effective:
                check()
                local=account.execute('SELECT status,reason FROM incidents WHERE incident_id=?',(RECEIPT+plan['operation_id'],)).fetchone()
                if local is not None and (local[0]!='resolved' or unpack(local[1])!=receipt(plan,name)):
                    raise ValueError('quote retention applied account receipt differs')
                if progress['accounts'][name]['done'] and local is None:
                    raise ValueError('quote retention completed account receipt is missing')
                applied[plan['operation_id']]=local is not None
                if local is None:continue
                for item in plan['accounts'][name]['items']:
                    check();stream=(item['asset'],item['timeframe']);at=timestamp(item['at'])
                    if stream not in authorized or authorized[stream]<at:authorized[stream]=at
            for floor in base.values():
                check();observed=read_floor(account,floor['asset'],floor['timeframe'])
                if observed is None or timestamp(observed['through'])!=authorized.get((floor['asset'],floor['timeframe'])):
                    raise ValueError('quote checkpoint retained active replay floor is missing or stale')
            for key,status,raw in account.execute("SELECT incident_id,status,reason FROM incidents WHERE incident_id LIKE 'archive:quote-%'"):
                check()
                if key==checkpoint.INCIDENT:continue
                value=unpack(raw);operation=value.get('operation_id');found=plans.get(operation)
                if found is None and key in base and value==base[key] and status=='resolved':
                    if timestamp(value['through'])!=authorized.get((value['asset'],value['timeframe'])):
                        raise ValueError('quote checkpoint retained floor was not advanced')
                    continue
                if found is None or status!='resolved':raise ValueError('quote retention global plan or local marker missing')
                plan,progress=found
                if (value.get('plan_sha256')!=_hash(plan) or plan['cohort_sha256']!=_hash(manifest) or
                        plan['accounts'][name]['directory']!=profile['directory']):
                    raise ValueError('quote retention account binding differs')
                if key.startswith(FLOOR):
                    floor=read_floor(account,value['asset'],value['timeframe'])
                    if (floor is None or key!=floor_key(value['asset'],value['timeframe']) or
                            timestamp(value['through'])!=authorized.get((value['asset'],value['timeframe']))):
                        raise ValueError('quote retention replay floor exceeds original authorized cut')
                elif key!=RECEIPT+operation or value!=receipt(plan,name):
                    raise ValueError('quote retention local receipt differs')
            for plan,progress in effective:
                check()
                state=progress['accounts'][name]
                if applied[plan['operation_id']]:
                    for item in plan['accounts'][name]['items']:
                        check()
                        if account.execute('SELECT 1 FROM decisions WHERE decision_id=?',(item['decision_id'],)).fetchone():
                            raise ValueError('quote retention completed decision resurrected')
                        if not quote_already_retired(account,item['asset'],item['timeframe'],item['at']):
                            raise ValueError('quote retention completed replay floor missing')
    if incomplete and not protective:raise ValueError('quote retention cohort is incomplete')
    return incomplete


def receipt(plan,name):
    return dict(schema_version=1,purpose=PURPOSE,operation_id=plan['operation_id'],plan_sha256=_hash(plan),
                account=name,account_sha256=_hash(plan['accounts'][name]))
