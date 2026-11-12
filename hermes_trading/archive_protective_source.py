"""Disabled original journal adapter for the version2 protective receiver.

The caller supplies an authenticated head and retains the real worker owner.
No grant cache/owner fence/transport is installed. Original packet and all-eight
settlement checks here do not replace a complete financial cohort certificate.
"""
from contextlib import closing
import json,math,sqlite3,time

from hermes_trading import archive_witness as original
from hermes_trading import archive_protective_witness as witness
from hermes_trading.archive_store import _root
from hermes_trading.operational_exits import PAIRED_OPERATIONAL_STOP_REASONS
from hermes_trading.paired_packet_storage import MAX_PACKET_BYTES
from hermes_trading.reflection_supervision import _hash,_now,_time


def _header(packet,record,head,identity,at):
    if (not isinstance(packet,dict) or packet.get('manifest_hash')!=identity['cohort_sha256'] or
            _hash(packet.get('release'))!=identity['release_sha256']):
        raise ValueError('original protective packet cohort/release differs')
    token=packet.get('witness_reservation_id');original._token(token)
    credit=head['state']['credit'];reservation=head['state']['reservation']
    if credit is not None and token==credit['credit_id']:
        if packet.get('operational_stop_reason') not in PAIRED_OPERATIONAL_STOP_REASONS or packet.get('operational_entries_blocked') is not True:
            raise ValueError('protective credit requires an explicit entry-blocked operational packet')
        kind='protective';generation=credit['generation']
    else:
        if (reservation is None or token!=reservation['reservation_id'] or record['sequence']!=reservation['sequence']):
            raise ValueError('original packet has no matching ordinary reservation or protective credit')
        kind='ordinary';generation=None
    header={'sequence':record['sequence'],'kind':kind,'reservation_id':token,'input_hash':record['input_hash'],
        'created_at':packet.get('created_at'),'credit_id':token if kind=='protective' else None,'credit_generation':generation}
    witness._packet(header,identity,at)
    return header


def _accounts(db,record,completed,check):
    if not isinstance(completed,dict) or not set(completed).issubset(original.journal.ACCOUNTS):
        raise ValueError('original account completion identities differ')
    for name,result in completed.items():
        check();arm,wallet=name.rsplit('_',1)
        if (not isinstance(result,dict) or result.get('input_hash')!=record['input_hash'] or result.get('arm')!=arm or
                type(result.get('wallet_eur')) is not int or result['wallet_eur']!=int(wallet)):
            raise ValueError('original account input or identity differs')
    if record['status']=='complete':
        row=db.execute('SELECT payload FROM paired_settlements WHERE batch_id=?',(record['batch_id'],)).fetchone()
        settled=None if row is None else original.journal.load_packet(db,row[0])
        if not isinstance(settled,dict) or set(settled)!=original.journal.ACCOUNTS or set(completed)!=original.journal.ACCOUNTS:
            raise ValueError('original completed cohort lacks eight settlements')
        for name,result in completed.items():
            check();measurement=settled[name]
            if (not isinstance(measurement,dict) or result.get('finalized') is not True or
                    _hash(result.get('net_equity'))!=_hash(measurement.get('net_equity')) or
                    _hash(result.get('measurement_error'))!=_hash(measurement.get('measurement_error'))):
                raise ValueError('original finalized account differs from settlement')


def source_view(root,binding,identity,head,*,enabled=False,now=None,max_bytes=128*1024*1024,
                timeout_seconds=10,clock=time.monotonic,sealed=False):
    """Build one SQLite-snapshot view, with at most two original completions.

    Source files are never created and financial rows are never changed. The
    journal/main/WAL/SHM/rollback bytes have a separate source-only ceiling;
    cooperative deadlines do not preempt blocked I/O or a packet decode.
    """
    original._enabled(enabled);head=witness.validate_head(head,binding,identity);root=_root(root);at=_now(now)
    if type(sealed) is not bool:raise ValueError('protective source sealed mode differs')
    if type(max_bytes) is not int or not 1<=max_bytes<=4*1024**3:raise ValueError('protective source byte bound differs')
    if type(timeout_seconds) not in (int,float) or not math.isfinite(timeout_seconds) or not 0<timeout_seconds<=10:
        raise ValueError('protective source deadline differs')
    started=clock();deadline=started+timeout_seconds
    from hermes_trading.paired_paper import _profiles
    from hermes_trading.paired_primary import resolve_primary
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    primary=_profiles(root,manifest)[('adaptive',resolve_primary(manifest).wallet_eur)][0];path=primary/'paper_publications.sqlite'
    def check():
        value=clock()
        if not math.isfinite(value) or value<started:raise ValueError('protective source clock differs')
        if value>=deadline:raise TimeoutError('protective source deadline exceeded')
        total=0
        for suffix in ('','-wal','-shm','-journal'):
            file=_root(path.with_name(path.name+suffix))
            if not file.exists():continue
            if not file.is_file():raise ValueError('protective source file is nonregular')
            total+=file.stat().st_size
        if total>max_bytes:raise ValueError('protective source byte bound exceeded')
    check()
    from hermes_trading.archive_certificate import _database
    identity_options={'database_factory':_database,'check':check} if sealed else {}
    if _hash(original.identity_for(root,**identity_options))!=_hash(identity):raise ValueError('original protective source identity changed')
    old=head['state']['point'];prior=old['complete']
    def inspect(db):
        check();checkpoint=None if db is None else original.journal._checkpoint(db)
        exists=db is not None and db.execute("SELECT 1 FROM sqlite_master WHERE name='paired_batches'").fetchone() is not None
        if not exists:
            if checkpoint is not None or prior:raise ValueError('original protective journal regressed or is missing')
            return {'identity_sha256':_hash(identity),'point':original._genesis_state()['point'],
                'prior_prefix_sha256':_hash([]),'suffix':[],'pending':None}
        check();data=original.journal._read_journal(db,checkpoint,check=check)
        retired=0 if checkpoint is None else checkpoint['retired_complete']
        if data['cohort_sha256']!=identity['cohort_sha256']:raise ValueError('original protective source cohort differs')
        if checkpoint is not None:
            request=original.journal._operation(db,checkpoint['operation_id'])['request']
            if request['experiment_id']!=identity['experiment_id'] or request['release_sha256']!=identity['release_sha256']:
                raise ValueError('original protective checkpoint identity differs')
        if data['complete']<prior or prior<retired:raise ValueError('original protective source lifetime/prefix is unavailable')
        if data['complete']-prior>2:raise ValueError('original protective source suffix exceeds two completions')
        previous=original.journal._prefix(checkpoint,data['rows'],prior)
        if previous!=old['prefix_sha256']:raise ValueError('original protective source prior prefix differs')
        suffix=[];pending=None
        for record in data['rows']:
            check()
            row=db.execute('SELECT payload,completed FROM paired_batches WHERE batch_id=?',(record['batch_id'],)).fetchone()
            packet=original.journal.load_packet(db,row[0]);completed=original.journal.load_packet(db,row[1])
            if packet.get('manifest_hash')!=identity['cohort_sha256'] or _hash(packet.get('release'))!=identity['release_sha256']:
                raise ValueError('original packet cohort/release differs')
            _accounts(db,record,completed,check)
            if record['sequence']<=prior:continue
            header=_header(packet,record,head,identity,at)
            if record['status']=='pending':pending=header
            else:
                following=_hash({'previous':previous,'record':record})
                suffix.append(dict(header,previous_prefix_sha256=previous,prefix_sha256=following,record=record))
                previous=following
        point={'complete':data['complete'],'prefix_sha256':previous,'archive_revision':0 if checkpoint is None else checkpoint['revision'],
            'retired_complete':retired,'checkpoint_sha256':_hash(checkpoint)}
        witness._point(point,identity)
        return {'identity_sha256':_hash(identity),'point':point,'prior_prefix_sha256':old['prefix_sha256'],'suffix':suffix,'pending':pending}
    if not path.exists():result=inspect(None)
    else:
        connection=(_database(path,check=check) if sealed else
                    closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=0)))
        with connection as db:
            db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,MAX_PACKET_BYTES)
            db.set_progress_handler(lambda:int(clock()>=deadline),1000)
            db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
            try:result=inspect(db)
            except sqlite3.OperationalError as error:
                if 'interrupted' in str(error).lower():raise TimeoutError('protective source SQL deadline exceeded') from error
                raise
    check()
    if _hash(original.identity_for(root,**identity_options))!=_hash(identity):raise ValueError('original protective source identity changed during read')
    return result
