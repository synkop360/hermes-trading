"""One audited recovery of the original paper storage latch after compaction."""
from contextlib import closing
from copy import deepcopy
import hashlib,json,os,sqlite3
from pathlib import Path

from hermes_trading.archive_snapshot import _safe_root,ownership
from hermes_trading.paper_publication import _temporary,_sync_dir,_release_hashes
from hermes_trading.reflection_supervision import _hash,_json,_now,_time,assert_writer_owner
from hermes_trading.paired_primary import resolve_primary
from hermes_trading.paired_worker import _profiles,_stats,_active_storage_positions
from hermes_trading.paired_storage_admission import inspect_storage

PREDECESSOR='19fdcb676120b21b44d54076de08e148259e34bdfe61bbdbdffbf20b694e12ed'


def _read(path):
    path=_safe_root(path)
    if not path.is_file() or path.stat().st_size>4*1024*1024:raise ValueError('recovery metadata bound differs')
    body=json.loads(path.read_bytes())
    if not isinstance(body,dict):raise ValueError('recovery metadata object required')
    return body


def _save(path,body):
    temporary=_temporary(path,(_json(body)+'\n').encode())
    try:os.replace(temporary,path);_sync_dir(path.parent)
    finally:temporary.unlink(missing_ok=True)


def recover_storage(root,*,approval_ref,expected_runtime=PREDECESSOR,now=None,fault=None):
    if os.environ.get('HERMES_TRADING_MODE')!='paper' or os.environ.get('HERMES_TRADING_ALLOW_REAL_ORDERS')!='false':
        raise ValueError('explicit paper-only storage recovery required')
    if not isinstance(approval_ref,str) or not 1<=len(approval_ref)<=256:raise ValueError('explicit recovery approval required')
    if _hash(_release_hashes())!=expected_runtime:raise ValueError('recovery predecessor source differs')
    root=_safe_root(Path(root).absolute());at=now or _now()
    if at.tzinfo is None:raise ValueError('aware recovery clock required')
    audit=_safe_root(root/'.paper-storage-recovery');intent_path=_safe_root(audit/'intent.json')
    completed=_safe_root(audit/'receipt.json');retired=_safe_root(audit/'retired-halt.json')
    with ownership(root,role='worker',enabled=True) as db:
        payload=db.execute('SELECT state FROM lifecycle WHERE id=1').fetchone()[0];state=json.loads(payload)
        manifest=_read(root/'manifest.json');profiles=_profiles(root,manifest);primary=profiles[('adaptive',resolve_primary(manifest).wallet_eur)][0]
        marker=_safe_root(primary/'experiment_halt.json')
        if completed.exists():
            intent=_read(intent_path);receipt=_read(completed)
            if (intent.get('intent_hash')!=_hash({k:v for k,v in intent.items() if k!='intent_hash'}) or
                    intent.get('root')!=str(root) or intent.get('source')!=expected_runtime or
                    intent.get('approval_ref')!=approval_ref or intent.get('configuration_hash')!=state.get('configuration_hash') or
                    receipt.get('intent_hash')!=intent.get('intent_hash') or
                    receipt.get('event_id')!=intent.get('event_id') or state.get('storage_recovery')!=intent.get('event_id') or
                    not retired.is_file() or hashlib.sha256(retired.read_bytes()).hexdigest()!=intent.get('halt_sha256')):
                raise ValueError('completed recovery evidence differs')
            return {'kind':'paper_storage_recovery','status':'already_recovered'}
        policy=assert_writer_owner(primary,'external_llm');stats=_stats(primary)
        if (state.get('status')!='running' or state.get('stop_reason') or at>=_time(state['deadline']) or
                stats['pending'] or stats.get('latest') is None or
                stats['latest']['packet'].get('operational_stop_reason')=='paired_storage_stop' or
                stats['complete']>=state['limits']['max_cycles'] or
                any(value!=0 for value in _active_storage_positions(profiles).values())):
            raise ValueError('only a live closed-position settled nonterminal storage pause is recoverable')
        storage=inspect_storage(root,_read(root/'storage_limits.json'))
        if storage.get('status')!='ready' or storage['free_bytes']<storage['limits']['reserve_bytes']+2*1024*1024:
            raise ValueError('verified storage headroom is required before recovery')
        publication=_safe_root(primary/'paper_publications.sqlite')
        with closing(sqlite3.connect(publication.as_uri()+'?mode=rw',uri=True,timeout=0,isolation_level=None)) as journal:
            journal.execute('PRAGMA locking_mode=EXCLUSIVE');journal.execute('BEGIN EXCLUSIVE');journal.commit()
            if (journal.execute("SELECT 1 FROM paired_batches WHERE status!='complete' LIMIT 1").fetchone() or
                    journal.execute("SELECT input_hash FROM paired_batches WHERE status='complete' ORDER BY rowid DESC LIMIT 1").fetchone()[0]!=stats['latest']['input_hash']):
                raise ValueError('shared batch changed before recovery')
            tables={row[0] for row in journal.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'publications' in tables and journal.execute('SELECT 1 FROM publications WHERE receipt IS NULL LIMIT 1').fetchone():
                raise ValueError('pending publication requires original reconciliation')
            if intent_path.exists():
                intent=_read(intent_path)
                if (intent.get('intent_hash')!=_hash({k:v for k,v in intent.items() if k!='intent_hash'}) or
                        intent.get('approval_ref')!=approval_ref or intent.get('root')!=str(root) or
                        intent.get('source')!=expected_runtime or intent.get('input_hash')!=stats['latest']['input_hash'] or
                        payload not in (intent.get('before_state'),intent.get('after_state'))):
                    raise ValueError('recovery intent requires explicit reconciliation')
            else:
                halt=_read(marker)
                if (state.get('storage_stop') is not True or halt.get('reason')!='storage_stop' or
                        halt.get('experiment_id')!=policy['experiment_id'] or halt.get('policy_hash')!=_hash(policy)):
                    raise ValueError('original bound storage halt required')
                raw=marker.read_bytes()
                body=dict(schema_version=1,root=str(root),source=expected_runtime,approval_ref=approval_ref,
                    configuration_hash=state['configuration_hash'],input_hash=stats['latest']['input_hash'],
                    halt_sha256=hashlib.sha256(raw).hexdigest(),before_state=payload,observed_at=at.isoformat())
                event=_hash(body);after=deepcopy(state)
                after.update(storage_stop=False,storage_report=dict(storage,observed_at=at.isoformat()),storage_recovery=event)
                body.update(event_id=event,after_state=_json(after))
                owner={key:body[key] for key in ('schema_version','root','source','approval_ref','configuration_hash','halt_sha256')}
                if audit.exists() and any(audit.iterdir()) and not (audit/'owner.json').exists():
                    raise ValueError('unowned recovery namespace')
                audit.mkdir(exist_ok=True)
                if (audit/'owner.json').exists():
                    if _read(audit/'owner.json')!=owner:raise ValueError('recovery preparation owner differs')
                    if {p.name for p in audit.iterdir()}-{'owner.json','original-halt.json','original-halt.bin'}:
                        raise ValueError('unexpected recovery preparation evidence')
                else:_save(audit/'owner.json',owner)
                if (audit/'original-halt.json').exists():
                    if _read(audit/'original-halt.json')!=halt:raise ValueError('prepared original halt differs')
                else:_save(audit/'original-halt.json',halt)
                # Retain byte-exact marker separately from its parsed evidence.
                original=_safe_root(audit/'original-halt.bin')
                if original.exists():
                    if original.read_bytes()!=raw:raise ValueError('prepared original marker bytes differ')
                else:
                    temporary=_temporary(original,raw);os.replace(temporary,original);_sync_dir(audit)
                intent=dict(body,intent_hash=_hash(body));_save(intent_path,intent)
                if fault:fault('intent')
            if hashlib.sha256(_safe_root(audit/'original-halt.bin').read_bytes()).hexdigest()!=intent['halt_sha256']:
                raise ValueError('original halt preimage changed before recovery')
            if payload==intent['before_state']:
                db.execute('BEGIN EXCLUSIVE')
                try:
                    changed=db.execute('UPDATE lifecycle SET state=? WHERE id=1 AND state=?',(intent['after_state'],payload))
                    if changed.rowcount!=1:raise ValueError('recovery lifecycle compare-and-set failed')
                    db.commit()
                except BaseException:db.rollback();raise
                if fault:fault('lifecycle')
            if marker.exists():
                if retired.exists() or hashlib.sha256(marker.read_bytes()).hexdigest()!=intent['halt_sha256']:
                    raise ValueError('recovery halt marker changed')
                os.replace(marker,retired);_sync_dir(primary);_sync_dir(audit)
            if not retired.is_file() or hashlib.sha256(retired.read_bytes()).hexdigest()!=intent['halt_sha256']:
                raise ValueError('retained original storage halt differs')
            _save(completed,dict(schema_version=1,event_id=intent['event_id'],status='recovered',intent_hash=intent['intent_hash']))
    return {'kind':'paper_storage_recovery','status':'recovered'}
