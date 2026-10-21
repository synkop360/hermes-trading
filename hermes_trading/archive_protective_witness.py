"""Disabled version2 counted protective-credit receiver kernel.

Only verified, authenticated source adapters may supply views. This kernel
accounts for their bounded original suffix; it does not itself certify market
quotes, financial rows, independent durability or coherent source recovery.
No version1 identity/binding is upgraded and no runtime/transport is installed.
"""
from contextlib import closing
from copy import deepcopy
from datetime import datetime
import json,math,os,sqlite3,tempfile,time,uuid

from hermes_trading import archive_witness as original
from hermes_trading.archive_store import _root,sync_file,_sync_directory
from hermes_trading.reflection_supervision import _hash,_json,_now,_time

PURPOSE='paired_archive_protective_witness_v2'
SCHEMA=(
    'CREATE TABLE protective_meta(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL)',
    'CREATE TABLE protective_events(sequence INTEGER PRIMARY KEY,operation_id TEXT NOT NULL UNIQUE,payload TEXT NOT NULL,sha256 TEXT NOT NULL)',
)
PENDING={'sequence','kind','reservation_id','input_hash','created_at','credit_id','credit_generation'}
COMPLETION=PENDING|{'previous_prefix_sha256','prefix_sha256','record'}
RECORD={'sequence','batch_id','status','input_hash','payload_sha256','completed_sha256','settlement_sha256'}
EVENT={'schema_version','purpose','sequence','previous_sha256','operation_id','metadata_sha256','at','request','state'}
MAX_REQUEST_BYTES=4096


def _identity(identity):
    original._identity(identity)
    if identity['max_cycles']<2:raise ValueError('protective mode needs at least two original slots')


def _at(value):
    if not isinstance(value,str) or datetime.fromisoformat(value).tzinfo is None:
        raise ValueError('protective witness time must be aware')
    return _time(value)


def _point(point,identity):
    if not isinstance(point,dict) or set(point)!=original.POINT_FIELDS:
        raise ValueError('protective witness point fields differ')
    original._view(dict(point,identity_sha256=_hash(identity),prior_prefix_sha256=point['prefix_sha256'],
        pending=False,pending_reservation_id=None,latest_reservation_id=None),identity)
    if point['complete']>identity['max_cycles']:raise ValueError('original protective cycle bound exceeded')


def _packet(value,identity,at):
    if not isinstance(value,dict) or set(value)!=PENDING:raise ValueError('original packet fields differ')
    original.journal._ordinal(value['sequence']);original._token(value['reservation_id'])
    original.journal._digest(value['input_hash'])
    if not _at(value['created_at'])< _time(identity['deadline']) or _at(value['created_at'])>at:
        raise ValueError('original packet exceeds deadline or receiver time')
    if value['kind']=='ordinary':
        if value['credit_id'] is not None or value['credit_generation'] is not None:
            raise ValueError('ordinary packet cannot claim protective credit')
    elif value['kind']=='protective':
        original._token(value['credit_id']);original.journal._ordinal(value['credit_generation'])
        if value['reservation_id']!=value['credit_id']:raise ValueError('protective packet credit token differs')
    else:raise ValueError('original packet kind differs')


def _grant(identity,generation,operation_id,at,complete):
    return {'credit_id':_hash({'purpose':PURPOSE,'operation_id':operation_id,'generation':generation})[:32],
        'generation':generation,'issued_at':at.isoformat(),'issued_after_complete':complete,
        'deadline':identity['deadline'],'identity_sha256':_hash(identity)}


def _state(state,identity,at):
    if not isinstance(state,dict) or set(state)!={'point','reservation','credit','last_consumed_generation','pending_protection'}:
        raise ValueError('protective receiver state fields differ')
    _point(state['point'],identity);complete=state['point']['complete']
    original.journal._ordinal(state['last_consumed_generation'],zero=True)
    if state['last_consumed_generation']>complete:raise ValueError('protective generation exceeds original completions')
    credit=state['credit'];reservation=state['reservation'];pending=state['pending_protection']
    if credit is not None:
        if (not isinstance(credit,dict) or set(credit)!={'credit_id','generation','issued_at','issued_after_complete','deadline','identity_sha256'} or
                credit['identity_sha256']!=_hash(identity) or credit['deadline']!=identity['deadline']):
            raise ValueError('protective credit identity differs')
        original._token(credit['credit_id']);original.journal._ordinal(credit['generation'])
        original.journal._ordinal(credit['issued_after_complete'],zero=True)
        if (credit['generation']!=state['last_consumed_generation']+1 or credit['issued_after_complete']>complete or
                not _at(credit['issued_at'])<_time(identity['deadline']) or _at(credit['issued_at'])>at):
            raise ValueError('protective credit generation/time differs')
    if reservation is not None:
        if (not isinstance(reservation,dict) or set(reservation)!={'sequence','reservation_id','reserved_at','input_hash'} or
                credit is None or pending is not None):raise ValueError('ordinary reservation lacks protective capacity')
        original._token(reservation['reservation_id']);original.journal._ordinal(reservation['sequence'])
        if reservation['input_hash'] is not None:original.journal._digest(reservation['input_hash'])
        if (reservation['sequence']!=complete+1 or reservation['sequence']>=identity['max_cycles'] or
                not _at(reservation['reserved_at'])<_time(identity['deadline']) or _at(reservation['reserved_at'])>at):
            raise ValueError('ordinary reservation original bound differs')
    if pending is not None:
        _packet(pending,identity,at)
        if credit is None or pending['kind']!='protective' or pending['sequence']!=complete+1:
            raise ValueError('original protective pending packet differs')
        _credit(credit,pending)


def _credit(credit,packet):
    if (credit is None or packet['credit_id']!=credit['credit_id'] or
            packet['credit_generation']!=credit['generation'] or
            _at(packet['created_at'])<_at(credit['issued_at'])):
        raise ValueError('original protective credit differs or is spent')


def _request(request,identity,at):
    if (not isinstance(request,dict) or set(request)!={'action','view','active_token','expected_head_sha256','operation_id'} or
            request['action'] not in ('check','reserve','cancel') or len(_json(request).encode())>MAX_REQUEST_BYTES):
        raise ValueError('protective witness request differs or exceeds bound')
    original._token(request['operation_id']);original.journal._digest(request['expected_head_sha256'])
    if request['active_token'] is not None:original._token(request['active_token'])
    if request['action']!='cancel' and request['active_token'] is not None:raise ValueError('unexpected active token')
    view=request['view']
    if (not isinstance(view,dict) or set(view)!={'identity_sha256','point','prior_prefix_sha256','suffix','pending'} or
            view['identity_sha256']!=_hash(identity) or not isinstance(view['suffix'],list) or len(view['suffix'])>2):
        raise ValueError('protective source view differs')
    _point(view['point'],identity);original.journal._digest(view['prior_prefix_sha256'])
    if view['pending'] is not None:_packet(view['pending'],identity,at)
    for event in view['suffix']:
        if not isinstance(event,dict) or set(event)!=COMPLETION:raise ValueError('original suffix fields differ')
        _packet({key:event[key] for key in PENDING},identity,at)
        for key in ('previous_prefix_sha256','prefix_sha256'):original.journal._digest(event[key])
        record=event['record']
        if not isinstance(record,dict) or set(record)!=RECORD:raise ValueError('original journal record fields differ')
        original.journal._ordinal(record['sequence'])
        if (record['sequence']!=event['sequence'] or record['input_hash']!=event['input_hash'] or
                record['status']!='complete' or not isinstance(record['batch_id'],str) or not 1<=len(record['batch_id'])<=128):
            raise ValueError('original journal record identity/input differs')
        for key in ('input_hash','payload_sha256','completed_sha256','settlement_sha256'):original.journal._digest(record[key])
        if event['prefix_sha256']!=_hash({'previous':event['previous_prefix_sha256'],'record':record}):
            raise ValueError('original journal record prefix proof differs')


def _advance(before,request,identity,at):
    _state(before,identity,at);_request(request,identity,at)
    result=deepcopy(before);view=request['view'];old=before['point'];point=view['point']
    if view['prior_prefix_sha256']!=old['prefix_sha256']:
        raise ValueError('original source prefix differs')
    if point['complete']-old['complete']!=len(view['suffix']):raise ValueError('original suffix count regressed or jumped')
    if point['archive_revision']<old['archive_revision'] or point['retired_complete']<old['retired_complete']:
        raise ValueError('original archive checkpoint regressed')
    if point['archive_revision']==old['archive_revision'] and any(point[key]!=old[key] for key in ('retired_complete','checkpoint_sha256')):
        raise ValueError('same revision original checkpoint differs')
    previous=old['prefix_sha256'];complete=old['complete'];protected=False
    for event in view['suffix']:
        if event['sequence']!=complete+1 or event['previous_prefix_sha256']!=previous or event['prefix_sha256']==previous:
            raise ValueError('original suffix sequence/prefix differs')
        packet={key:event[key] for key in PENDING};reservation=result['reservation']
        if event['kind']=='ordinary':
            if (protected or reservation is None or reservation['sequence']!=event['sequence'] or
                    reservation['reservation_id']!=event['reservation_id'] or
                    (reservation['input_hash'] is not None and reservation['input_hash']!=event['input_hash']) or
                    _at(event['created_at'])<_at(reservation['reserved_at'])):
                raise ValueError('ordinary completion lacks its original reservation')
            result['reservation']=None
        else:
            if reservation is not None:raise ValueError('unresolved original normal reservation precedes protection')
            _credit(result['credit'],packet)
            if result['pending_protection'] is not None and _hash(result['pending_protection'])!=_hash(packet):
                raise ValueError('original frozen protective packet changed')
            result['last_consumed_generation']=result['credit']['generation']
            result['credit']=None;result['pending_protection']=None;protected=True
        complete=event['sequence'];previous=event['prefix_sha256']
    if point['prefix_sha256']!=previous:raise ValueError('final original suffix prefix differs')
    result['point']=deepcopy(point);pending=view['pending'];reservation=result['reservation']
    if pending is not None:
        if pending['sequence']!=complete+1:raise ValueError('original pending packet sequence differs')
        if pending['kind']=='ordinary':
            if (reservation is None or reservation['reservation_id']!=pending['reservation_id'] or
                    _at(pending['created_at'])<_at(reservation['reserved_at']) or
                    (reservation['input_hash'] is not None and reservation['input_hash']!=pending['input_hash'])):
                raise ValueError('original pending normal packet differs')
            reservation['input_hash']=pending['input_hash']
        else:
            if reservation is not None:raise ValueError('unresolved original reservation cannot be borrowed')
            _credit(result['credit'],pending)
            if result['pending_protection'] is not None and _hash(result['pending_protection'])!=_hash(pending):
                raise ValueError('original protective pending packet changed')
            result['pending_protection']=deepcopy(pending)
    elif result['pending_protection'] is not None:
        raise ValueError('original pending protective packet is missing')
    if reservation is not None and pending is None:
        if request['action']!='cancel' or request['active_token']!=reservation['reservation_id'] or reservation['input_hash'] is not None:
            raise ValueError('unresolved original reservation requires explicit recovery')
    if request['action']=='cancel':
        if reservation is None or pending is not None or reservation['input_hash'] is not None or request['active_token']!=reservation['reservation_id']:
            raise ValueError('only explicit unfrozen original reservation cancellation is allowed')
        result['reservation']=None
    elif request['action']=='reserve' and reservation is None:
        if pending is not None:raise ValueError('pending protection blocks ordinary reservation')
        if at>=_time(identity['deadline']):raise ValueError('ordinary reservation exceeds original deadline')
        if complete+1>=identity['max_cycles']:raise ValueError('last original slot is reserved for protective capacity')
        if result['credit'] is None:
            result['credit']=_grant(identity,result['last_consumed_generation']+1,request['operation_id'],at,complete)
        result['reservation']={'sequence':complete+1,'reservation_id':request['operation_id'],
            'reserved_at':at.isoformat(),'input_hash':None}
    _state(result,identity,at)
    return result


def _genesis(identity,operation,at):
    return dict(original._genesis_state(),credit=_grant(identity,1,operation,at,0),
        last_consumed_generation=0,pending_protection=None)


def _event(meta,sequence,previous,operation,at,request,state):
    value={'schema_version':2,'purpose':PURPOSE,'metadata_sha256':_hash(meta),'sequence':sequence,
        'previous_sha256':previous,'operation_id':operation,'at':at.isoformat(),'request':request,'state':state}
    raw=_json(value)
    if len(raw.encode())>original.MAX_EVENT_BYTES:raise ValueError('protective event exceeds bound')
    return value,raw,_hash(value)


def _binding(binding):
    if (not isinstance(binding,dict) or set(binding)!={'schema_version','purpose','witness_id','genesis_sha256'} or
            type(binding['schema_version']) is not int or binding['schema_version']!=2 or binding['purpose']!=PURPOSE):
        raise ValueError('protective version2 binding differs')
    original._token(binding['witness_id']);original.journal._digest(binding['genesis_sha256'])


def validate_head(head,binding,identity):
    """Validate a head supplied by an already authenticated receiver adapter.

    This checks shape/domain/identity/state, not a network MAC or independent
    high-water floor. Callers must verify authentication before using a grant.
    """
    _binding(binding);_identity(identity)
    if (not isinstance(head,dict) or set(head)!={'schema_version','purpose','witness_id','genesis_sha256',
            'identity_sha256','sequence','head_sha256','at','state'} or type(head['schema_version']) is not int or
            head['schema_version']!=2 or head['purpose']!=PURPOSE or head['witness_id']!=binding['witness_id'] or
            head['genesis_sha256']!=binding['genesis_sha256'] or head['identity_sha256']!=_hash(identity) or
            type(head['sequence']) is not int or not 0<=head['sequence']<original.MAX_EVENTS):
        raise ValueError('protective receiver head binding differs')
    original.journal._digest(head['head_sha256']);_state(head['state'],identity,_at(head['at']))
    return deepcopy(head)


def _load(db,binding,identity,check):
    _binding(binding);_identity(identity);check()
    schema=[tuple(row) for row in db.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name")]
    if schema!=[('table','protective_events',SCHEMA[1]),('table','protective_meta',SCHEMA[0])]:
        raise ValueError('protective receiver original schema differs')
    rows=db.execute('SELECT payload FROM protective_meta').fetchall()
    if len(rows)!=1:raise ValueError('protective receiver metadata missing')
    try:meta=json.loads(rows[0][0])
    except (ValueError,RecursionError) as error:raise ValueError('protective metadata malformed') from error
    if (not isinstance(meta,dict) or set(meta)!={'schema_version','purpose','witness_id','identity'} or
            type(meta['schema_version']) is not int or meta['schema_version']!=2 or meta['purpose']!=PURPOSE or
            meta['witness_id']!=binding['witness_id'] or _hash(meta['identity'])!=_hash(identity) or _json(meta)!=rows[0][0]):
        raise ValueError('protective metadata original identity differs')
    previous=None;head=None;seen=set()
    for index,row in enumerate(db.execute('SELECT sequence,operation_id,payload,sha256 FROM protective_events ORDER BY sequence')):
        check()
        if index>=original.MAX_EVENTS:raise ValueError('protective receiver event bound exceeded')
        try:event=json.loads(row[2])
        except (ValueError,RecursionError) as error:raise ValueError('protective event malformed') from error
        if (not isinstance(event,dict) or set(event)!=EVENT or type(event['sequence']) is not int or
                event['sequence']!=index or row[0]!=index or event['operation_id']!=row[1] or row[1] in seen or
                type(event['schema_version']) is not int or event['schema_version']!=2 or event['purpose']!=PURPOSE or
                event['metadata_sha256']!=_hash(meta) or event['previous_sha256']!=previous or
                _json(event)!=row[2] or _hash(event)!=row[3]):raise ValueError('protective original event chain differs')
        original._token(row[1]);at=_at(event['at'])
        if index==0:
            if event['request'] is not None or row[3]!=binding['genesis_sha256'] or at>=_time(identity['deadline']):
                raise ValueError('protective original genesis differs')
            expected=_genesis(identity,row[1],at)
        else:
            if (not isinstance(event['request'],dict) or at<_at(head['at']) or
                    event['request'].get('expected_head_sha256')!=previous or event['request'].get('operation_id')!=row[1]):
                raise ValueError('protective event head/time differs')
            expected=_advance(head['state'],event['request'],identity,at)
        if _hash(expected)!=_hash(event['state']):raise ValueError('protective original transition differs')
        seen.add(row[1]);previous=row[3];head=event
    if head is None:raise ValueError('protective receiver genesis missing')
    return {'schema_version':2,'purpose':PURPOSE,'witness_id':binding['witness_id'],
        'genesis_sha256':binding['genesis_sha256'],'identity_sha256':_hash(identity),
        'sequence':head['sequence'],'head_sha256':previous,'at':head['at'],'state':head['state']},meta


def initialize(path,identity,*,source_root,enabled=False,now=None,max_bytes=original.MAX_WITNESS_BYTES):
    original._enabled(enabled);_identity(identity);path=_root(path);source_root=_root(source_root);at=_now(now)
    if path.exists() or not path.parent.is_dir() or source_root==path or source_root in path.parents:
        raise ValueError('protective receiver requires a fresh independent target')
    if _hash(original.identity_for(source_root))!=_hash(identity):raise ValueError('protective source identity differs')
    view=original.source_view(source_root,identity,prior_complete=0,enabled=True)
    if view['complete'] or view['pending'] or at>=_time(identity['deadline']):
        raise ValueError('protective receiver initialization requires original genesis before deadline')
    meta={'schema_version':2,'purpose':PURPOSE,'witness_id':uuid.uuid4().hex,'identity':deepcopy(identity)}
    operation=uuid.uuid4().hex;event,raw,digest=_event(meta,0,None,operation,at,None,_genesis(identity,operation,at))
    with tempfile.TemporaryDirectory(prefix='.protective-witness-',dir=path.parent) as temporary:
        stage=_root(temporary)/'receiver.sqlite';stage.touch()
        budget=original._ReceiverBudget(stage,max_bytes,10,time.monotonic)
        with closing(sqlite3.connect(stage,isolation_level=None,timeout=0)) as db:
            db.execute('PRAGMA synchronous=FULL');budget.attach(db);db.execute('BEGIN IMMEDIATE')
            try:
                budget.write_ceiling(db)
                for statement in SCHEMA:db.execute(statement)
                db.execute('INSERT INTO protective_meta VALUES(1,?)',(_json(meta),))
                db.execute('INSERT INTO protective_events VALUES(?,?,?,?)',(0,operation,raw,digest))
                budget.check();db.commit();budget.check()
            except BaseException:db.rollback();raise
        sync_file(stage);os.link(stage,path);_sync_directory(path.parent)
    return {'schema_version':2,'purpose':PURPOSE,'witness_id':meta['witness_id'],'genesis_sha256':digest}


class _RequestBudget(original._ReceiverBudget):
    """V2 may shorten its SQL budget to an outer request's absolute deadline."""
    def __init__(self,*args,request_deadline=None,**kwargs):
        super().__init__(*args,**kwargs)
        if request_deadline is not None:
            if type(request_deadline) not in (int,float) or not math.isfinite(request_deadline):
                raise ValueError('protective absolute request deadline differs')
            self.deadline=min(self.deadline,request_deadline)
            self.check()


def read(path,binding,identity,*,enabled=False,max_bytes=original.MAX_WITNESS_BYTES,timeout_seconds=10,clock=time.monotonic,
         request_deadline=None):
    original._enabled(enabled);_binding(binding);_identity(identity);path=_root(path)
    budget=_RequestBudget(path,max_bytes,timeout_seconds,clock,request_deadline=request_deadline)
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=0)) as db:
        try:
            budget.attach(db);db.execute('BEGIN');result=_load(db,binding,identity,budget.check_time)[0]
            budget.check();return result
        except sqlite3.Error as error:budget.sql_error(error)


def _transact(db,head,meta,request,identity,at,budget,*,fault=None,precommit=None,committed=None):
    """One original V2 operation after full or owned-prefix verification."""
    _request(request,identity,at);operation=request['operation_id']
    retry=db.execute('SELECT payload FROM protective_events WHERE operation_id=?',(operation,)).fetchone()
    if retry is not None:
        if _hash(json.loads(retry[0])['request'])!=_hash(request):raise ValueError('protective operation ID reused for another original request')
        budget.check()
        if precommit is not None:precommit()
        db.commit();return head,None
    if head['head_sha256']!=request['expected_head_sha256']:raise ValueError('protective expected receiver head differs')
    if at<_at(head['at']):raise ValueError('protective receiver time regressed')
    state=_advance(head['state'],request,identity,at)
    if _hash(state)==_hash(head['state']):
        budget.check()
        if precommit is not None:precommit()
        db.commit();return head,None
    if head['sequence']+1>=original.MAX_EVENTS:raise ValueError('protective receiver event bound reached')
    event,raw,digest=_event(meta,head['sequence']+1,head['head_sha256'],operation,at,request,state)
    result=dict(head,sequence=event['sequence'],head_sha256=digest,at=event['at'],state=state)
    budget.write_ceiling(db)
    db.execute('INSERT INTO protective_events VALUES(?,?,?,?)',(event['sequence'],operation,raw,digest))
    if fault is not None:fault('before_commit')
    budget.check()
    if precommit is not None:precommit()
    db.commit()
    if committed is not None:committed(result)
    if fault is not None:fault('after_commit')
    budget.check();return result,event


def apply(path,binding,identity,view,*,action='check',active_token=None,expected_head_sha256,
          operation_id,enabled=False,now=None,max_bytes=original.MAX_WITNESS_BYTES,
          timeout_seconds=10,clock=time.monotonic,fault=None,request_deadline=None):
    original._enabled(enabled);_binding(binding);_identity(identity);path=_root(path);at=_now(now)
    request={'action':action,'view':view,'active_token':active_token,'expected_head_sha256':expected_head_sha256,'operation_id':operation_id}
    _request(request,identity,at);budget=_RequestBudget(path,max_bytes,timeout_seconds,clock,request_deadline=request_deadline)
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=rw',uri=True,timeout=0,isolation_level=None)) as db:
        try:
            budget.attach(db);db.execute('PRAGMA synchronous=FULL');db.execute('BEGIN IMMEDIATE')
            head,meta=_load(db,binding,identity,budget.check_time)
            return _transact(db,head,meta,request,identity,at,budget,fault=fault)[0]
        except sqlite3.Error as error:db.rollback();budget.sql_error(error)
        except BaseException:db.rollback();raise
