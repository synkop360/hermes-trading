"""Disabled all-eight-account acknowledged quote retention.

Requires a recorded retired archive, original cohort revalidation, exclusive
worker/publication ownership and admitted aggregate peak. Authenticated copy
claims are not independent-copy verification; installation must supply that
separate recovery authority. This API neither deploys nor activates anything.
"""
from contextlib import closing,nullcontext
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import uuid

from hermes_trading import archive_journal as journal,archive_store as store
from hermes_trading import archive_quote_state as state
from hermes_trading.archive_quote_plan import select_prefix
from hermes_trading.archive_capacity import StorageOwner,_safe_info
from hermes_trading.archive_shared_history import _requests
from hermes_trading.archive_shared_resolver import _Budget,_chain,_restore,_cohort,_bind
from hermes_trading.archive_snapshot import ownership,_owner_path
from hermes_trading.paired_primary import resolve_primary
from hermes_trading.persistence.artifacts import _artifact_lock,_fsync_directory
from hermes_trading.reflection_supervision import _connect,_hash,_json,_now


def _bundle(db,decision_id):
    """Original row bytes/rowids for an acknowledged side-effect-free quote."""
    decision=db.execute('SELECT rowid,* FROM decisions WHERE decision_id=?',(decision_id,)).fetchone()
    if decision is None:return None
    d=dict(decision)
    if d['action']!='hold' or d['reason']!='position_open':return None
    if db.execute('SELECT 1 FROM order_intents WHERE decision_id=?',(decision_id,)).fetchone():return None
    payload=db.execute('SELECT rowid,* FROM projection_payloads WHERE decision_id=?',(decision_id,)).fetchone()
    incident=db.execute('SELECT rowid,* FROM incidents WHERE incident_id=?',('projection:item:'+decision_id,)).fetchone()
    processed=db.execute('SELECT rowid,* FROM candles_processed WHERE candle_id=?',(d['candle_id'],)).fetchone()
    if payload is None or incident is None or processed is None:return None
    p,i,c=dict(payload),dict(incident),dict(processed)
    if i['status']!='resolved' or i['reason']!='resolved' or i['resolved_at'] is None:return None
    raw=json.loads(p['payload'])
    if (type(raw) is not dict or set(raw)!={'decision','intents','orders','fills','positions','closed_positions'} or
            any(raw[key]!=[] for key in ('intents','orders','fills','positions','closed_positions')) or
            raw['decision']!={key:value for key,value in d.items() if key!='rowid'}):return None
    if (not c['timeframe'].startswith('paper-position:') or len(c['timeframe'])<=len('paper-position:') or
            c['asset']!=d['asset'] or c['close_timestamp']!=d['decided_at'] or
            c['candle_id']!=f"{c['asset']}:{c['timeframe']}:{c['close_timestamp']}"):
        return None
    state.timestamp(c['close_timestamp'])
    return dict(decision=d,payload=p,incident=i,processed=c)


def _saved(db,plan,progress):
    db.execute('UPDATE paired_quote_retention SET progress=? WHERE operation_id=?',
        (state.envelope(progress),plan['operation_id']));db.commit()


def _readonly(path):
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True,timeout=0)
    db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');return db


def _verify_archive(db,root,manifest,operation_id,archive,cache,budget,now,*,preflight=None):
    checkpoint=journal._checkpoint(db);source=journal._read_journal(db,checkpoint,budget.check)
    if source['pending'] or source['cohort_sha256']!=_hash(manifest):raise ValueError('quote retention current cohort is pending or differs')
    experiment=next(iter(manifest['profiles']))
    config=json.loads((root/experiment['directory']/'wallet_account.json').read_text(encoding='utf-8'))
    requests=_requests(db,checkpoint,config['experiment_id'],budget.check)
    request=next((r for r in requests if r['operation_id']==operation_id),None)
    if request is None:raise ValueError('quote retention requires a recorded retired archive operation')
    chain=_chain(archive,request['archive_sha256'],[request],request['experiment_id'],budget)
    archived_manifest=chain[-1][1];target=cache/'cohort';_restore(archive,archived_manifest,target,budget)
    if preflight is not None:preflight(target/'worker')
    relative=resolve_primary(manifest).profile_directory(manifest,'adaptive')+'/paper_publications.sqlite'
    certificate,_=_cohort(target,archived_manifest,relative,_hash(manifest),request['experiment_id'],now,budget)
    _bind(request,certificate)
    with closing(_readonly(target/'worker'/relative)) as original:
        row=original.execute('SELECT payload FROM paired_batches WHERE rowid=?',(request['through_sequence'],)).fetchone()
        if row is None:raise ValueError('quote retention original cutoff packet is missing')
        cut=journal.load_packet(original,row[0])['created_at'];state.timestamp(cut)
    return request,target/'worker',cut


def _new_plan(root,manifest,original,request,cut,budget,operation_id=None):
    accounts={};count=0
    for profile in sorted(manifest['profiles'],key=lambda p:(p['arm'],p['wallet_eur'])):
        budget.check();name=f"{profile['arm']}_{profile['wallet_eur']}";directory=profile['directory'];items=[]
        with closing(_readonly(original/directory/'trading.db')) as source,closing(sqlite3.connect(
                (root/directory/'trading.db').resolve().as_uri()+'?mode=ro',uri=True,timeout=0)) as current:
            source.set_progress_handler(lambda:budget.check() or 0,1000)
            current.set_progress_handler(lambda:budget.check() or 0,1000)
            current.row_factory=sqlite3.Row
            high_decision=current.execute('SELECT MAX(rowid) FROM decisions').fetchone()[0]
            high_payload=current.execute('SELECT MAX(rowid) FROM projection_payloads').fetchone()[0]
            for (identity,) in source.execute("SELECT decision_id FROM decisions WHERE action='hold' AND reason='position_open' ORDER BY rowid"):
                if count>=state.MAX_ITEMS:break
                budget.check();bundle=_bundle(source,identity)
                if bundle is None or state.timestamp(bundle['decision']['decided_at'])>state.timestamp(cut):continue
                active=_bundle(current,identity)
                if active is None:continue
                if _hash(bundle)!=_hash(active):raise ValueError('quote retention original account bundle differs')
                # Keep implicit rowid high-water rows so future originals never reuse their sequence.
                if active['decision']['rowid']==high_decision or active['payload']['rowid']==high_payload:continue
                c=bundle['processed'];items.append(dict(decision_id=identity,asset=c['asset'],timeframe=c['timeframe'],
                    at=c['close_timestamp'],bundle_sha256=_hash(bundle)));count+=1
                if count>=state.MAX_ITEMS:break
        accounts[name]=dict(directory=directory,items=items)
    plan=dict(schema_version=1,purpose=state.PURPOSE,operation_id=operation_id or uuid.uuid4().hex,
        archive_operation_id=request['operation_id'],archive_sha256=request['archive_sha256'],
        cohort_sha256=_hash(manifest),cut_at=cut,accounts=accounts)
    if len(state.envelope(plan).encode())>state.MAX_PLAN_BYTES:raise ValueError('quote retention plan byte bound exceeded')
    return plan


def _verify_plan_originals(plan,original,budget):
    for account in plan['accounts'].values():
        with closing(_readonly(original/account['directory']/'trading.db')) as source:
            source.set_progress_handler(lambda:budget.check() or 0,1000)
            for item in account['items']:
                budget.check();bundle=_bundle(source,item['decision_id'])
                if bundle is None or _hash(bundle)!=item['bundle_sha256'] or state.timestamp(item['at'])>state.timestamp(plan['cut_at']):
                    raise ValueError('quote retention planned original evidence differs')
                c=bundle['processed']
                if (item['asset'],item['timeframe'],item['at'])!=(c['asset'],c['timeframe'],c['close_timestamp']):
                    raise ValueError('quote retention planned candle identity differs')


def _retire_account(root,plan,name,budget,hit):
    account=plan['accounts'][name];path=root/account['directory']/'trading.db';wanted=state.receipt(plan,name)
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=rw',uri=True,timeout=0,isolation_level=None)) as db:
        db.row_factory=sqlite3.Row;db.execute('PRAGMA synchronous=FULL');db.execute('PRAGMA foreign_keys=ON')
        db.set_progress_handler(lambda:budget.check() or 0,1000)
        db.execute('BEGIN IMMEDIATE')
        try:
            existing=db.execute('SELECT status,reason FROM incidents WHERE incident_id=?',(state.RECEIPT+plan['operation_id'],)).fetchone()
            if existing is not None:
                if existing[0]!='resolved' or state.unpack(existing[1])!=wanted:raise ValueError('quote retention local acknowledgement differs')
                for item in account['items']:
                    if db.execute('SELECT 1 FROM decisions WHERE decision_id=?',(item['decision_id'],)).fetchone():
                        raise ValueError('quote retention acknowledged decision reappeared')
                    if not state.quote_already_retired(db,item['asset'],item['timeframe'],item['at']):
                        raise ValueError('quote retention acknowledged replay protection missing')
                db.commit();return
            for item in account['items']:
                budget.check();bundle=_bundle(db,item['decision_id'])
                if bundle is None or _hash(bundle)!=item['bundle_sha256']:raise ValueError('quote retention current account bundle changed')
                db.execute('DELETE FROM projection_payloads WHERE decision_id=?',(item['decision_id'],))
                db.execute('DELETE FROM incidents WHERE incident_id=?',('projection:item:'+item['decision_id'],))
                db.execute('DELETE FROM decisions WHERE decision_id=?',(item['decision_id'],))
                db.execute('DELETE FROM candles_processed WHERE candle_id=?',(bundle['processed']['candle_id'],))
                previous=state.read_floor(db,item['asset'],item['timeframe'])
                through=item['at'] if previous is None or state.timestamp(previous['through'])<state.timestamp(item['at']) else previous['through']
                state.store_incident(db,state.floor_key(item['asset'],item['timeframe']),dict(schema_version=1,purpose=state.PURPOSE,
                    asset=item['asset'],timeframe=item['timeframe'],through=through,operation_id=plan['operation_id'],plan_sha256=wanted['plan_sha256']),plan['cut_at'])
            state.store_incident(db,state.RECEIPT+plan['operation_id'],wanted,plan['cut_at'])
            if db.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('quote retention account linkage differs')
            hit('before_account_commit',name);db.commit()
        except BaseException:db.rollback();raise
    hit('after_account_commit',name)


def _stage(db,root,plan,progress,name,lease,hit):
    current=progress['accounts'][name];directory=root/plan['accounts'][name]['directory']
    record=current['stage']
    if record is not None:
        path=lease.require_path(directory/record['name'])
        if path.exists():
            info=_safe_info(path)
            if record['identity'] is None:
                # The creation/registration crash window can leave only EMPTY
                # unclaimed files. Keep and count them; never infer ownership.
                if info.st_size or len(current['orphans'])>=4:raise ValueError('quote retention unclaimed stage requires reconciliation')
                current['orphans'].append(dict(name=path.name,bytes=0));current['stage']=None
                _saved(db,plan,progress)
            elif list((info.st_dev,info.st_ino))!=record['identity']:
                raise ValueError('quote retention original stage was replaced')
            else:return path
        else:
            # Replacement may have committed before the global acknowledgement.
            # Rebuild from the CURRENT artifact, never reuse a stale snapshot.
            current['stage']=None;_saved(db,plan,progress)
    record=dict(name='.quote-'+plan['operation_id']+'-'+uuid.uuid4().hex+'.tmp',identity=None)
    current['stage']=record;_saved(db,plan,progress)
    path=lease.require_path(directory/record['name'])
    with path.open('xb') as stream:stream.flush();os.fsync(stream.fileno())
    hit('after_empty_stage',name)
    info=_safe_info(path);record['identity']=[info.st_dev,info.st_ino];_saved(db,plan,progress)
    _fsync_directory(directory);return path


def _artifact(db,root,plan,progress,name,original,budget,lease,hit):
    account=plan['accounts'][name];path=lease.require_path(root/account['directory']/'decisions.jsonl')
    target={item['decision_id'] for item in account['items']}
    with closing(_readonly(original/account['directory']/'trading.db')) as archived:
        expected={identity:_bundle(archived,identity)['decision'] for identity in target}
    stage=_stage(db,root,plan,progress,name,lease,hit)
    with _artifact_lock(path):
        identity=None
        if path.exists():
            before=_safe_info(path);identity=(before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)
        stage_info=_safe_info(stage)
        if [stage_info.st_dev,stage_info.st_ino]!=progress['accounts'][name]['stage']['identity']:
            raise ValueError('quote retention original artifact stage changed')
        size=0;written=0;digest=hashlib.sha256()
        with stage.open('r+b') as output:
            opened=os.fstat(output.fileno())
            if [opened.st_dev,opened.st_ino]!=progress['accounts'][name]['stage']['identity']:
                raise ValueError('quote retention opened artifact stage was replaced')
            output.truncate(0);output.seek(0)
            if path.exists():
                with path.open('rb') as source:
                    while line:=source.readline(budget.limit+1):
                        budget.check();size+=len(line)
                        if size>budget.limit:raise ValueError('quote retention artifact byte bound exceeded')
                        try:event=json.loads(line)
                        except (ValueError,UnicodeError,RecursionError):event=None
                        key=event.get('decision_id') if isinstance(event,dict) else None
                        if key in target:
                            wanted={k:v for k,v in expected[key].items() if k!='rowid'}
                            if event!=wanted:raise ValueError('quote retention human decision differs from archived original')
                        else:output.write(line);digest.update(line);written+=len(line)
                        hit('artifact_chunk',name)
            output.flush();os.fsync(output.fileno())
        if path.exists():
            after=_safe_info(path)
            if identity!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):raise ValueError('quote retention current artifact changed')
        elif identity is not None:raise ValueError('quote retention current artifact disappeared')
        hit('before_artifact_replace',name);budget.check()
        final=_safe_info(stage)
        if ([final.st_dev,final.st_ino]!=progress['accounts'][name]['stage']['identity'] or final.st_size!=written):
            raise ValueError('quote retention original stage changed before publication')
        verified=hashlib.sha256();seen=0
        with stage.open('rb') as stream:
            if [os.fstat(stream.fileno()).st_dev,os.fstat(stream.fileno()).st_ino]!=progress['accounts'][name]['stage']['identity']:
                raise ValueError('quote retention opened stage changed before publication')
            while raw:=stream.read(1024*1024):
                budget.check();seen+=len(raw)
                if seen>written:raise ValueError('quote retention stage byte bound differs')
                verified.update(raw)
        if seen!=written or verified.hexdigest()!=digest.hexdigest():raise ValueError('quote retention stage content changed')
        if path.exists():
            final_source=_safe_info(path)
            if identity!=(final_source.st_dev,final_source.st_ino,final_source.st_size,final_source.st_mtime_ns):
                raise ValueError('quote retention source artifact changed before publication')
        elif identity is not None:raise ValueError('quote retention source artifact disappeared before publication')
        os.replace(stage,path);_fsync_directory(path.parent)
        hit('after_artifact_replace',name)
    progress['accounts'][name].update(done=True,stage=None);_saved(db,plan,progress)


def retain_quote_outcomes(root,*,archive_root,archive_operation_id,storage_owner,cache_root,
                          enabled=False,max_bytes,timeout_seconds=120,now=None,owner_db=None,fault=None,operation_id=None):
    """Complete/resume one bounded all-eight transition. Not runtime installation.

    Retrying after acknowledgement loss uses retained local receipts. Financial
    rows committed by a protective exit after interruption remain authoritative.
    The caller must restore archive availability to complete a partial cohort.
    """
    if enabled is not True:raise ValueError('quote retention is disabled')
    if not isinstance(storage_owner,StorageOwner):raise ValueError('quote retention requires original storage owner')
    root=store._root(root);archive=store._root(archive_root);cache=store._root(cache_root)
    budget=_Budget(max_bytes,timeout_seconds,storage_owner.domain.clock);view_now=_now(now)
    if not isinstance(archive_operation_id,str) or len(archive_operation_id)!=32:raise ValueError('quote retention archive operation differs')
    if operation_id is not None and (not isinstance(operation_id,str) or len(operation_id)!=32 or any(c not in '0123456789abcdef' for c in operation_id)):
        raise ValueError('quote retention requested operation identity differs')
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    from hermes_trading.paired_paper import _profiles
    profiles=_profiles(root,manifest);selected=resolve_primary(manifest)
    shared=profiles[('adaptive',selected.wallet_eur)][0]/'paper_publications.sqlite'
    sql_paths=[path/'trading.db' for path,_,_ in profiles.values()]+[shared]
    sql_bytes=sum(path.stat().st_size+(path.with_name(path.name+'-wal').stat().st_size if path.with_name(path.name+'-wal').exists() else 0) for path in sql_paths)
    artifact_peak=max(((path/'decisions.jsonl').stat().st_size if (path/'decisions.jsonl').exists() else 0) for path,_,_ in profiles.values())
    peak=max_bytes+2*sql_bytes+artifact_peak+2*state.MAX_PLAN_BYTES+131072
    # Source restoration, SQL rollback/WAL and one simultaneous artifact copy.
    with storage_owner.admit(peak,timeout_seconds=budget.deadline-storage_owner.domain.clock(),timeout_ceiling_seconds=120) as lease:
        lease.require_path(root);lease.require_path(cache)
        with (ownership(root,role='worker',enabled=True) if owner_db is None else nullcontext(owner_db)) as worker:
            names=worker.execute('PRAGMA database_list').fetchall()
            if (not names or Path(names[0][2]).resolve()!=_owner_path(root,'worker').resolve() or
                    worker.execute('PRAGMA locking_mode').fetchone()[0].lower()!='exclusive' or worker.in_transaction):
                raise ValueError('quote retention worker ownership differs')
            worker.execute('BEGIN EXCLUSIVE');worker.commit()
            db=_connect(shared)
            try:
                def hit(point,name):
                    if fault:fault(point,name)
                    budget.check()
                    if point=='artifact_chunk':lease.require_path(root)
                    else:lease.check()
                db.set_progress_handler(lambda:budget.check() or 0,1000)
                # Validate original cohort BEFORE creating or changing any plan.
                with tempfile.TemporaryDirectory(prefix='.quote-original-',dir=cache) as temporary:
                    request,original,cut=_verify_archive(db,root,manifest,archive_operation_id,archive,Path(temporary),budget,view_now)
                    db.execute('''CREATE TABLE IF NOT EXISTS paired_quote_retention(
                        operation_id TEXT PRIMARY KEY,plan TEXT NOT NULL,progress TEXT NOT NULL)''');db.commit()
                    from hermes_trading import archive_quote_checkpoint_state as checkpoint
                    from hermes_trading.archive_quote_segment import _control_footprint
                    _control_footprint(root,manifest,budget,db=db,account_controls=False)
                    committed=checkpoint.read_checkpoint(db)
                    rows=state.operations(db,budget.check,budget.decoded)
                    if committed is not None and operation_id is not None and not any(p['operation_id']==operation_id for p,_ in rows):
                        expected=checkpoint.operation_id(_hash(manifest),checkpoint.next_ordinal(db,committed))
                        if operation_id!=expected:
                            raise ValueError('quote retention compacted request cannot receive a new ordinal')
                    control_bytes=_control_footprint(root,manifest,budget,db=db,sealed=False)
                    pending=[(p,s) for p,s in rows if s['status']=='pending']
                    if pending:
                        plan,progress=pending[0]
                        if plan['archive_operation_id']!=archive_operation_id:raise ValueError('quote retention pending archive differs')
                        if operation_id is not None and plan['operation_id']!=operation_id:raise ValueError('quote retention pending operation differs')
                    else:
                        completed=[(p,s) for p,s in rows if p['archive_operation_id']==archive_operation_id and (operation_id is None or p['operation_id']==operation_id)]
                        if completed:
                            plan,progress=completed[-1];state.assert_quote_cohort(root,db=db,manifest=manifest,check=budget.check)
                            return _result(plan,progress)
                        state.assert_quote_cohort(root,db=db,manifest=manifest,check=budget.check)
                        if len(rows)>=checkpoint.MAX_TAIL:raise ValueError('quote retention detailed tail requires checkpoint')
                        next_sequence=checkpoint.next_ordinal(db,committed)
                        requested=operation_id
                        if committed is not None:
                            identity=checkpoint.operation_id(_hash(manifest),next_sequence)
                            if requested is not None and requested!=identity:
                                raise ValueError('quote retention compacted request cannot receive a new ordinal')
                            requested=identity
                        eligible=_new_plan(root,manifest,original,request,cut,budget,requested)
                        budget.check()
                        plan=select_prefix(eligible,existing_bytes=control_bytes,max_bytes=checkpoint.MAX_DETAILED_BYTES,
                                           max_items=state.MAX_ITEMS,check=budget.check)
                        if plan is None:
                            return dict(status='no_outcomes',retired_outcomes=0,operation_id=eligible['operation_id'],
                                        activated=False,deployed=False,capacity_qualified=False,profitability_qualified=False)
                        progress=dict(status='pending',accounts={name:dict(done=False,stage=None,orphans=[]) for name in journal.ACCOUNTS})
                        encoded_plan=state.envelope(plan);encoded_progress=state.envelope(progress)
                        lease.check();budget.check()
                        db.execute('INSERT INTO paired_quote_retention(rowid,operation_id,plan,progress) VALUES(?,?,?,?)',
                            (next_sequence,plan['operation_id'],encoded_plan,encoded_progress));db.commit()
                    if plan['archive_sha256']!=request['archive_sha256'] or plan['cohort_sha256']!=_hash(manifest) or plan['cut_at']!=cut:
                        raise ValueError('quote retention original plan binding differs')
                    _verify_plan_originals(plan,original,budget);hit('after_plan_commit',None)
                    for name in sorted(journal.ACCOUNTS):
                        if progress['accounts'][name]['done']:continue
                        _retire_account(root,plan,name,budget,hit)
                        _artifact(db,root,plan,progress,name,original,budget,lease,hit)
                        hit('after_account_ack',name)
                    state.assert_quote_cohort(root,db=db,manifest=manifest,protective=True,check=budget.check)
                    progress['status']='complete';_saved(db,plan,progress)
                    state.assert_quote_cohort(root,db=db,manifest=manifest,check=budget.check);hit('after_cohort_complete',None)
                    return _result(plan,progress)
            finally:db.close()


def _result(plan,progress):
    return dict(schema_version=1,purpose=state.PURPOSE,operation_id=plan['operation_id'],plan_sha256=_hash(plan),
        status=progress['status'],accounts={name:len(account['items']) for name,account in plan['accounts'].items()},
        retired_outcomes=sum(len(account['items']) for account in plan['accounts'].values()),
        deployed=False,activated=False,capacity_qualified=False,profitability_qualified=False)
