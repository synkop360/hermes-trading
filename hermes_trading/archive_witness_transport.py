"""Disabled authenticated witness protocol over an injected bounded exchange.

No listener, network adapter, installed key, receiver initialization, cancellation
or retirement endpoint. Transport must enforce byte/time bounds before decoding
and interrupt blocking I/O. Same-host tests do not certify independent recovery.
"""
from copy import deepcopy
from datetime import datetime
import hmac
import json
import math
import time
import uuid

from hermes_trading import archive_witness as witness
from hermes_trading.control_auth import SQLiteNonceReplayStore,sign_command,verify_command
from hermes_trading.reflection_supervision import _hash,_json,_now

REQUEST_PURPOSE='paired_archive_witness_request_v1'
RESPONSE_PURPOSE='paired_archive_witness_response_v1'
MAX_BODY_BYTES=8192
MAX_WIRE_BYTES=16384
REQUEST_FIELDS={'schema_version','purpose','experiment_id','binding','identity_sha256',
                'action','view','expected_head_sha256','operation_id'}
RESPONSE_FIELDS={'schema_version','purpose','experiment_id','binding','identity_sha256','request_sha256','head'}


def _secret(value):
    if not isinstance(value,str) or not 32 <= len(value) <= 4096:
        raise ValueError('witness authentication key unavailable')


def _pins(binding,identity):
    witness._identity(identity)
    if not isinstance(binding,dict) or set(binding)!={'witness_id','genesis_sha256'}:
        raise ValueError('witness transport binding differs')
    witness._token(binding['witness_id']);witness.journal._digest(binding['genesis_sha256'])


def _timeout(value):
    if type(value) not in (int,float) or not math.isfinite(value) or not 0 < value <= 10:
        raise ValueError('witness exchange deadline differs')


def _envelope(value):
    if not isinstance(value,dict) or set(value)!={'body','nonce','timestamp','signature'}:
        raise ValueError('witness envelope fields differ')
    if not isinstance(value['body'],str) or len(value['body'].encode()) > MAX_BODY_BYTES:
        raise ValueError('witness envelope body exceeds byte bound')
    witness._token(value['nonce']);witness.journal._digest(value['signature'])
    if not isinstance(value['timestamp'],str) or len(value['timestamp']) > 80:
        raise ValueError('witness envelope validity differs')
    try:
        at=datetime.fromisoformat(value['timestamp'])
        if at.tzinfo is None:raise ValueError('witness envelope validity must be aware')
    except (TypeError,ValueError) as error:
        raise ValueError('witness envelope validity differs') from error
    if len(_json(value).encode()) > MAX_WIRE_BYTES:
        raise ValueError('witness envelope exceeds wire bound')
    return at


def _fresh(at,now):
    if not 0 <= (_now(now)-at).total_seconds() < 300:
        raise ValueError('witness envelope validity differs')


def _decode(body):
    try:value=json.loads(body)
    except (ValueError,RecursionError) as error:raise ValueError('witness body malformed') from error
    if not isinstance(value,dict) or _json(value)!=body:raise ValueError('witness body must be canonical')
    return value


def _scope(value,fields,purpose,binding,identity):
    if (set(value)!=fields or type(value['schema_version']) is not int or value['schema_version']!=1 or
            value['purpose']!=purpose or value['experiment_id']!=identity['experiment_id'] or
            _hash(value['binding'])!=_hash(binding) or value['identity_sha256']!=_hash(identity)):
        raise ValueError('witness message scope/binding differs')


def _request(body,binding,identity):
    value=_decode(body);_scope(value,REQUEST_FIELDS,REQUEST_PURPOSE,binding,identity)
    action=value['action']
    if not isinstance(action,str) or action not in {'read','check','reserve'}:
        raise ValueError('witness remote action is unavailable')
    if action=='read':
        if any(value[key] is not None for key in ('view','expected_head_sha256','operation_id')):
            raise ValueError('witness read fields differ')
    else:
        witness._view(value['view'],identity)
        witness.journal._digest(value['expected_head_sha256']);witness._token(value['operation_id'])
    return value


def _signed(secret,nonce,at,value):
    body=_json(value)
    result={'body':body,'nonce':nonce,'timestamp':at.isoformat(),'signature':sign_command(secret,nonce,at,body)}
    _envelope(result);return result


class WitnessServer:
    def __init__(self,path,binding,identity,secret,nonce_store,*,enabled=False,
                 max_db_bytes=witness.MAX_WITNESS_BYTES,clock=time.monotonic,receiver=None):
        _pins(binding,identity)
        self.path=path;self.binding=deepcopy(binding);self.identity=deepcopy(identity);self.secret=secret
        self.nonce_store=nonce_store;self.enabled=enabled;self.max_db_bytes=max_db_bytes;self.clock=clock
        if receiver is not None:
            from hermes_trading.archive_receiver import VerifiedReceiver
            if (not isinstance(receiver,VerifiedReceiver) or receiver.path!=witness._root(path) or
                    _hash(receiver.binding)!=_hash(binding) or _hash(receiver.identity)!=_hash(identity) or
                    receiver.max_bytes!=max_db_bytes):
                raise ValueError('prepared receiver binding differs')
        self.receiver=receiver

    def handle(self,envelope,*,now,timeout_seconds=10):
        if self.enabled is not True:raise ValueError('witness server is disabled')
        _secret(self.secret);_timeout(timeout_seconds)
        if not isinstance(self.nonce_store,SQLiteNonceReplayStore):
            raise ValueError('witness requires a durable SQLite nonce store')
        started=self.clock();deadline=started+timeout_seconds
        def remaining():
            at=self.clock()
            if at < started or not math.isfinite(at):raise ValueError('witness exchange clock differs')
            if at >= deadline:raise TimeoutError('witness exchange deadline exceeded')
            return deadline-at
        at=_envelope(envelope);received=_now(now);_fresh(at,received)
        request=_request(envelope['body'],self.binding,self.identity)
        if not verify_command(self.secret,envelope['nonce'],at,envelope['body'],envelope['signature'],self.nonce_store,now=received):
            raise ValueError('witness request authentication/replay failed')
        options={'enabled':True,'max_bytes':self.max_db_bytes,'timeout_seconds':remaining(),'clock':self.clock}
        if self.receiver is not None:
            if request['action']=='read':head=self.receiver.read(timeout_seconds=remaining())
            else:head=self.receiver.apply(request['view'],action=request['action'],
                expected_head_sha256=request['expected_head_sha256'],operation_id=request['operation_id'],
                now=received,timeout_seconds=remaining())
        elif request['action']=='read':
            head=witness.read(self.path,self.binding,self.identity,**options)
        else:
            head=witness.apply(self.path,self.binding,self.identity,request['view'],action=request['action'],
                expected_head_sha256=request['expected_head_sha256'],operation_id=request['operation_id'],now=received,**options)
        remaining()
        return _signed(self.secret,envelope['nonce'],received,{'schema_version':1,'purpose':RESPONSE_PURPOSE,
            'experiment_id':self.identity['experiment_id'],'binding':self.binding,'identity_sha256':_hash(self.identity),
            'request_sha256':_hash(envelope),'head':head})


class WitnessClient:
    def __init__(self,binding,identity,secret,exchange,*,enabled=False,now=None,clock=time.monotonic):
        _pins(binding,identity)
        self.binding=deepcopy(binding);self.identity=deepcopy(identity);self.secret=secret;self.exchange=exchange
        self.enabled=enabled;self.now=now or _now;self.clock=clock;self.total_wire_bytes=0

    def _call(self,binding,identity,action,view,expected_head,operation_id,timeout):
        if self.enabled is not True:raise ValueError('witness client is disabled')
        _secret(self.secret);_timeout(timeout);_pins(binding,identity)
        if _hash(binding)!=_hash(self.binding) or _hash(identity)!=_hash(self.identity):
            raise ValueError('witness client original binding/identity differs')
        started=self.clock();deadline=started+timeout
        at=_now(self.now());nonce=uuid.uuid4().hex
        value={'schema_version':1,'purpose':REQUEST_PURPOSE,'experiment_id':identity['experiment_id'],
            'binding':binding,'identity_sha256':_hash(identity),'action':action,'view':view,
            'expected_head_sha256':expected_head,'operation_id':operation_id}
        envelope=_signed(self.secret,nonce,at,value);_request(envelope['body'],binding,identity)
        request_bytes=len(_json(envelope).encode());self.total_wire_bytes+=request_bytes
        # The exchange must enforce bounds before parsing and interrupt stalled
        # I/O. This callback interface cannot preempt arbitrary Python callbacks.
        response=self.exchange(envelope,timeout_seconds=timeout,max_response_bytes=MAX_WIRE_BYTES-request_bytes)
        observed=self.clock()
        if not math.isfinite(observed) or observed < started:raise ValueError('witness exchange clock differs')
        if observed >= deadline:raise TimeoutError('witness exchange deadline exceeded')
        response_at=_envelope(response)
        size=len(_json(response).encode());self.total_wire_bytes+=size
        if size+request_bytes > MAX_WIRE_BYTES:raise ValueError('witness response exceeds wire budget')
        if response['nonce']!=nonce:raise ValueError('witness response nonce/binding differs')
        _fresh(response_at,_now(self.now()))
        if not hmac.compare_digest(sign_command(self.secret,nonce,response_at,response['body']),response['signature']):
            raise ValueError('witness response authentication failed')
        result=_decode(response['body']);_scope(result,RESPONSE_FIELDS,RESPONSE_PURPOSE,binding,identity)
        if result['request_sha256']!=_hash(envelope):raise ValueError('witness response request binding differs')
        return witness._worker_head(result['head'],binding,identity)

    def read(self,*,binding,identity,timeout):
        return self._call(binding,identity,'read',None,None,None,timeout)

    def apply(self,view,*,binding,identity,action,expected_head_sha256,operation_id,timeout):
        return self._call(binding,identity,action,view,expected_head_sha256,operation_id,timeout)
