"""Disabled owner-scoped durable protective credit session.

The actual retained worker SQLite connection is required. Cached heads are
recovery evidence, never startup authority. Each new session must authenticate
and reconcile before offline protection; this does not install a worker mode.
"""
from contextlib import closing
from copy import deepcopy
import json,sqlite3,uuid

from hermes_trading import archive_protective_witness as witness
from hermes_trading.archive_protective_source import source_view
from hermes_trading.archive_protective_transport import ProtectiveClient
from hermes_trading.archive_store import _root
from hermes_trading.reflection_supervision import _hash,_json,_now,_time

PURPOSE='paired_archive_worker_grant_cache_v2'
CACHE_FIELDS={'schema_version','purpose','binding_sha256','identity_sha256','owner_nonce',
              'head','spent_generation','protection'}
CLAIM_FIELDS={'credit_id','generation','sequence','input_hash'}
MAX_CACHE_BYTES=16384


def worker_identity(root,config,client):
    """Validate explicit version2 opt-in; no listener or activation is installed."""
    if (not isinstance(config,dict) or set(config)!={'schema_version','binding','identity_sha256'} or
            type(config['schema_version']) is not int or config['schema_version']!=2):
        raise ValueError('protective worker configuration differs')
    witness._binding(config['binding'])
    if not isinstance(client,ProtectiveClient) or client.enabled is not True:
        raise ValueError('protective worker requires an authenticated adapter')
    identity=witness.original.identity_for(root);witness._identity(identity)
    if config['identity_sha256']!=_hash(identity):raise ValueError('protective worker original identity differs')
    return identity


def validate_cache(raw,digest,binding,identity):
    """Validate original canonical cache evidence without granting ownership."""
    try:value=json.loads(raw)
    except (ValueError,RecursionError) as error:raise ValueError('protective cache malformed') from error
    if not isinstance(value,dict) or set(value)!=CACHE_FIELDS or _json(value)!=raw or _hash(value)!=digest:
        raise ValueError('protective cache checksum/shape differs')
    if (type(value['schema_version']) is not int or value['schema_version']!=2 or value['purpose']!=PURPOSE or
            value['binding_sha256']!=_hash(binding) or value['identity_sha256']!=_hash(identity)):
        raise ValueError('protective cache scope differs')
    witness.original._token(value['owner_nonce']);witness.validate_head(value['head'],binding,identity)
    spent=value['spent_generation'];claim=value['protection']
    if type(spent) is not int or not 0<=spent<=identity['max_cycles']:raise ValueError('protective spent generation differs')
    if claim is not None:
        if not isinstance(claim,dict) or set(claim)!=CLAIM_FIELDS:raise ValueError('protective claim fields differ')
        witness.original._token(claim['credit_id'])
        if (type(claim['generation']) is not int or claim['generation']!=spent or spent<1 or
                type(claim['sequence']) is not int or not 1<=claim['sequence']<=identity['max_cycles']):
            raise ValueError('protective claim generation/sequence differs')
        if claim['input_hash'] is not None:witness.original.journal._digest(claim['input_hash'])
    return value


def reconcile_claim(cache,head,view):
    """Reconcile the original claim against original packets or consumption."""
    claim=None if cache is None else cache['protection']
    if claim is None:return None
    candidates=[*view['suffix'],view['pending']]
    match=next((event for event in candidates if event is not None and event['kind']=='protective' and
        event['sequence']==claim['sequence'] and event['credit_id']==claim['credit_id'] and
        event['credit_generation']==claim['generation']),None)
    if match is not None:
        if claim['input_hash'] is not None and claim['input_hash']!=match['input_hash']:
            raise ValueError('original claimed protective packet changed')
        return dict(claim,input_hash=match['input_hash'])
    if (head['state']['last_consumed_generation']>=claim['generation'] and
            head['state']['point']['complete']>=claim['sequence']):return None
    raise ValueError('claimed protective packet is missing; ambiguous recovery requires reconciliation')


class ProtectiveSession:
    def __init__(self,root,owner,binding,identity,client,*,enabled=False,now=None):
        witness.original._enabled(enabled);witness._binding(binding);witness._identity(identity)
        if not isinstance(client,ProtectiveClient) or client.enabled is not True:
            raise ValueError('protective session requires an enabled authenticated client')
        self.root=_root(root);self.owner=owner;self.binding=deepcopy(binding);self.identity=deepcopy(identity)
        self.client=client;self.now=now or _now;self.owner_nonce=uuid.uuid4().hex
        self.live=False;self.outage=False;self.invalid=False;self.closed=False
        self._owner();self.cache=self._read_cache();self._cache_hash=None if self.cache is None else _hash(self.cache)

    def _owner(self):
        if self.closed or self.invalid:raise ValueError('protective owner session is closed or invalid')
        if not isinstance(self.owner,sqlite3.Connection):raise ValueError('protective owner connection differs')
        path=_root(self.root/'worker_runtime.sqlite')
        names=self.owner.execute('PRAGMA database_list').fetchall()
        if (len(names)!=1 or _root(names[0][2])!=path or self.owner.in_transaction or
                self.owner.execute('PRAGMA locking_mode').fetchone()[0].lower()!='exclusive'):
            raise ValueError('protective session requires the actual retained worker owner')
        # A mode setting alone is insufficient: another connection must be
        # unable to acquire the real file's EXCLUSIVE lock between commits.
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=rw',uri=True,timeout=0,isolation_level=None)) as probe:
            try:probe.execute('BEGIN EXCLUSIVE')
            except sqlite3.OperationalError as error:
                if 'locked' not in str(error).lower():raise
            else:
                probe.rollback();raise ValueError('protective worker owner no longer retains its lease')

    def _read_cache(self):
        if not self.owner.execute("SELECT 1 FROM sqlite_master WHERE name='protective_cache'").fetchone():return None
        lengths=self.owner.execute('SELECT id,length(payload),length(sha256) FROM protective_cache').fetchall()
        if len(lengths)!=1 or lengths[0][0]!=1 or not 0<lengths[0][1]<=MAX_CACHE_BYTES or lengths[0][2]!=64:
            raise ValueError('protective cache byte bound or shape differs')
        raw,digest=self.owner.execute('SELECT payload,sha256 FROM protective_cache WHERE id=1').fetchone()
        return validate_cache(raw,digest,self.binding,self.identity)

    def _check(self):
        try:
            self._owner();current=self._read_cache()
            if (None if current is None else _hash(current))!=self._cache_hash:
                raise ValueError('protective local cache changed during retained ownership')
            if _hash(witness.original.identity_for(self.root))!=_hash(self.identity):
                raise ValueError('protective original source identity changed')
        except BaseException:
            self.invalid=True;self.live=False;self.outage=False;raise

    def _save(self,head,spent,claim):
        self._check()
        value=dict(schema_version=2,purpose=PURPOSE,binding_sha256=_hash(self.binding),identity_sha256=_hash(self.identity),
            owner_nonce=self.owner_nonce,head=head,spent_generation=spent,protection=claim)
        raw=_json(value)
        if len(raw.encode())>MAX_CACHE_BYTES:raise ValueError('protective cache exceeds byte bound')
        try:
            self.owner.execute('BEGIN EXCLUSIVE')
            self.owner.execute('CREATE TABLE IF NOT EXISTS protective_cache(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL,sha256 TEXT NOT NULL)')
            self.owner.execute('INSERT OR REPLACE INTO protective_cache VALUES(1,?,?)',(raw,_hash(value)))
            self.owner.commit()
        except BaseException:
            self.owner.rollback();self.invalid=True;self.live=False;self.outage=False;raise
        self.cache=value;self._cache_hash=_hash(value)

    def _remote(self,callback,*,allow_outage):
        try:return callback()
        except (OSError,TimeoutError):
            self.outage=bool(allow_outage and self.live and not self.invalid)
            if not allow_outage:self.invalid=True;self.live=False
            raise
        except BaseException:
            self.invalid=True;self.live=False;self.outage=False;raise

    def _view(self,head):
        try:return source_view(self.root,self.binding,self.identity,head,enabled=True,now=_now(self.now()))
        except BaseException:
            self.invalid=True;self.live=False;self.outage=False;raise

    def _claim_in_view(self,head,view):
        return reconcile_claim(self.cache,head,view)

    def _sync(self,action):
        self._check();was_live=self.live
        head=self._remote(lambda:self.client.read(binding=self.binding,identity=self.identity,timeout=10),allow_outage=True)
        try:
            witness.validate_head(head,self.binding,self.identity)
            if self.cache is not None:
                old=self.cache['head']
                if head['sequence']<old['sequence'] or (head['sequence']==old['sequence'] and head['head_sha256']!=old['head_sha256']):
                    raise ValueError('protective authenticated receiver head regressed')
            view=self._view(head);claim=self._claim_in_view(head,view)
        except BaseException:
            self.invalid=True;self.live=False;self.outage=False;raise
        operation=uuid.uuid4().hex
        result=self._remote(lambda:self.client.apply(view,binding=self.binding,identity=self.identity,action=action,
            expected_head_sha256=head['head_sha256'],operation_id=operation,timeout=10),allow_outage=action=='check')
        try:
            witness.validate_head(result,self.binding,self.identity)
            request=dict(action=action,view=view,active_token=None,expected_head_sha256=head['head_sha256'],operation_id=operation)
            expected=witness._advance(head['state'],request,self.identity,witness._at(result['at']))
            changed=_hash(expected)!=_hash(head['state'])
            if (_hash(result['state'])!=_hash(expected) or result['sequence']!=head['sequence']+int(changed) or
                    (not changed and result!=head)):
                raise ValueError('protective acknowledged transition differs')
            spent=max(0 if self.cache is None else self.cache['spent_generation'],result['state']['last_consumed_generation'])
            if claim is not None and result['state']['last_consumed_generation']>=claim['generation']:claim=None
            self._save(result,spent,claim)
        except BaseException:
            self.invalid=True;self.live=False;self.outage=False;raise
        self.live=True;self.outage=False
        return deepcopy(result)

    def reconcile(self):return self._sync('check')
    def reserve(self):return self._sync('reserve')

    def claim_protection(self):
        self._check()
        if not self.live or not self.outage or self.cache is None:raise ValueError('protective action lacks a current reconciled outage owner')
        if self.cache['protection'] is not None:raise ValueError('protective grant already claimed; only original frozen replay is allowed')
        at=_now(self.now())
        if at>=_time(self.identity['deadline']):raise ValueError('protective original deadline reached')
        head=self.cache['head'];view=self._view(head)
        request=dict(action='check',view=view,active_token=None,expected_head_sha256=head['head_sha256'],operation_id=uuid.uuid4().hex)
        state=witness._advance(head['state'],request,self.identity,at);grant=state['credit']
        if (view['pending'] is not None or state['reservation'] is not None or grant is None or
                grant['generation']<=self.cache['spent_generation'] or state['point']['complete']>=self.identity['max_cycles']):
            raise ValueError('protective grant pending, spent or original capacity exhausted')
        claim=dict(credit_id=grant['credit_id'],generation=grant['generation'],sequence=state['point']['complete']+1,input_hash=None)
        self._save(head,grant['generation'],claim)
        return grant['credit_id']

    def replay_protection(self):
        self._check()
        if not self.live or self.cache is None or self.cache['protection'] is None:
            raise ValueError('protective original replay lacks current owner reconciliation')
        view=self._view(self.cache['head']);claim=self._claim_in_view(self.cache['head'],view)
        pending=view['pending']
        if claim is None or pending is None or pending['kind']!='protective' or pending['input_hash']!=claim['input_hash']:
            raise ValueError('protective replay requires the original frozen pending packet')
        return claim['credit_id']

    def close(self):self.closed=True;self.live=False;self.outage=False
