"""Disabled, purpose-separated version2 protocol over a bounded exchange.

Reuses only version1 canonical envelope/cryptographic helpers, not its scope or
state transitions. No listener, grant cache, owner fence or worker activation.
The exchange must interrupt blocked I/O and enforce limits before decoding.
"""
from copy import deepcopy
import hmac,math,time,uuid

from hermes_trading import archive_protective_witness as witness
from hermes_trading import archive_witness_transport as wire
from hermes_trading.archive_nonce import BoundedArchiveNonceStore,NONCE_PEAK_BYTES
from hermes_trading.control_auth import SQLiteNonceReplayStore,sign_command,verify_command
from hermes_trading.reflection_supervision import _hash,_json,_now

REQUEST_PURPOSE='paired_archive_protective_request_v2'
RESPONSE_PURPOSE='paired_archive_protective_response_v2'


def _pins(binding,identity):
    witness._binding(binding);witness._identity(identity)


def _scope(value,fields,purpose,binding,identity):
    if (set(value)!=fields or type(value['schema_version']) is not int or value['schema_version']!=2 or
            value['purpose']!=purpose or value['experiment_id']!=identity['experiment_id'] or
            _hash(value['binding'])!=_hash(binding) or value['identity_sha256']!=_hash(identity)):
        raise ValueError('protective message purpose/scope/binding differs')


def _request(body,binding,identity,at):
    value=wire._decode(body);_scope(value,wire.REQUEST_FIELDS,REQUEST_PURPOSE,binding,identity)
    if value['action']=='read':
        if any(value[key] is not None for key in ('view','expected_head_sha256','operation_id')):
            raise ValueError('protective read fields differ')
    elif value['action'] in ('check','reserve'):
        witness._request({key:value[key] for key in ('action','view','expected_head_sha256','operation_id')}|
            {'active_token':None},identity,at)
    else:raise ValueError('protective remote action is unavailable')
    return value


def _remaining(clock,started,deadline):
    observed=clock()
    if not math.isfinite(observed) or observed<started:raise ValueError('protective exchange clock differs')
    if observed>=deadline:raise TimeoutError('protective exchange deadline exceeded')
    return deadline-observed


class ProtectiveServer:
    def __init__(self,path,binding,identity,secret,nonce_store,*,enabled=False,
                 max_db_bytes=witness.original.MAX_WITNESS_BYTES,clock=time.monotonic,receiver=None):
        _pins(binding,identity)
        self.path=path;self.binding=deepcopy(binding);self.identity=deepcopy(identity);self.secret=secret
        self.nonce_store=nonce_store;self.enabled=enabled;self.max_db_bytes=max_db_bytes;self.clock=clock
        if receiver is not None:
            from hermes_trading.archive_protective_receiver import ProtectiveReceiver
            if (not isinstance(receiver,ProtectiveReceiver) or receiver.path!=witness._root(path) or
                    _hash(receiver.binding)!=_hash(binding) or _hash(receiver.identity)!=_hash(identity) or
                    receiver.max_bytes!=max_db_bytes):
                raise ValueError('prepared protective receiver binding differs')
        self.receiver=receiver

    def handle(self,envelope,*,now,timeout_seconds=10):
        if self.enabled is not True:raise ValueError('protective server is disabled')
        wire._secret(self.secret);wire._timeout(timeout_seconds)
        if not isinstance(self.nonce_store,(SQLiteNonceReplayStore,BoundedArchiveNonceStore)):
            raise ValueError('protective server requires a durable nonce store')
        started=self.clock();deadline=started+timeout_seconds
        at=wire._envelope(envelope);received=_now(now);wire._fresh(at,received)
        request=_request(envelope['body'],self.binding,self.identity,received)
        if isinstance(self.nonce_store,BoundedArchiveNonceStore):
            store=self.nonce_store;store.require_scope(self.binding,self.identity)
            if (self.clock is not store.owner.domain.clock or
                    self.receiver is not None and self.receiver.clock is not self.clock):
                raise ValueError('admitted protective receiver requires the original domain clock')
            if type(self.max_db_bytes) is not int or not 1<=self.max_db_bytes<=witness.original.MAX_WITNESS_BYTES:
                raise ValueError('admitted protective receiver extent bound differs')
            with store.owner.admit(NONCE_PEAK_BYTES+self.max_db_bytes,
                    timeout_seconds=_remaining(self.clock,started,deadline)) as lease:
                lease.require_path(witness._root(self.path))
                with store.armed(lease,deadline=deadline):
                    return self._accepted(envelope,request,at,received,started,deadline,lease=lease)
        return self._accepted(envelope,request,at,received,started,deadline)

    def _accepted(self,envelope,request,at,received,started,deadline,*,lease=None):
        if not verify_command(self.secret,envelope['nonce'],at,envelope['body'],envelope['signature'],self.nonce_store,now=received):
            raise ValueError('protective request authentication/replay failed')
        if lease is not None:lease.check(remaining_peak_bytes=self.max_db_bytes)
        options=dict(enabled=True,max_bytes=self.max_db_bytes,
            timeout_seconds=_remaining(self.clock,started,deadline),clock=self.clock)
        if lease is not None:options['request_deadline']=deadline
        if self.receiver is not None:
            prepared_options=dict(timeout_seconds=options['timeout_seconds'])
            if lease is not None:prepared_options['request_deadline']=deadline
            if request['action']=='read':head=self.receiver.read(**prepared_options)
            else:head=self.receiver.apply(request['view'],action=request['action'],
                expected_head_sha256=request['expected_head_sha256'],operation_id=request['operation_id'],
                now=received,**prepared_options)
        elif request['action']=='read':head=witness.read(self.path,self.binding,self.identity,**options)
        else:
            head=witness.apply(self.path,self.binding,self.identity,request['view'],action=request['action'],
                expected_head_sha256=request['expected_head_sha256'],operation_id=request['operation_id'],now=received,**options)
        witness.validate_head(head,self.binding,self.identity)
        response=wire._signed(self.secret,envelope['nonce'],received,dict(schema_version=2,purpose=RESPONSE_PURPOSE,
            experiment_id=self.identity['experiment_id'],binding=self.binding,identity_sha256=_hash(self.identity),
            request_sha256=_hash(envelope),head=head))
        _remaining(self.clock,started,deadline)
        if len(_json(envelope).encode())+len(_json(response).encode())>wire.MAX_WIRE_BYTES:
            raise ValueError('protective response exceeds combined wire bound')
        if lease is not None:lease.check()
        return response


class ProtectiveClient:
    def __init__(self,binding,identity,secret,exchange,*,enabled=False,now=None,clock=time.monotonic):
        _pins(binding,identity)
        self.binding=deepcopy(binding);self.identity=deepcopy(identity);self.secret=secret;self.exchange=exchange
        self.enabled=enabled;self.now=now or _now;self.clock=clock;self.total_wire_bytes=0

    def _call(self,binding,identity,action,view,expected_head,operation_id,timeout):
        if self.enabled is not True:raise ValueError('protective client is disabled')
        wire._secret(self.secret);wire._timeout(timeout);_pins(binding,identity)
        if _hash(binding)!=_hash(self.binding) or _hash(identity)!=_hash(self.identity):
            raise ValueError('protective client original binding/identity differs')
        started=self.clock();deadline=started+timeout;at=_now(self.now());nonce=uuid.uuid4().hex
        request=dict(schema_version=2,purpose=REQUEST_PURPOSE,experiment_id=identity['experiment_id'],
            binding=binding,identity_sha256=_hash(identity),action=action,view=view,
            expected_head_sha256=expected_head,operation_id=operation_id)
        envelope=wire._signed(self.secret,nonce,at,request);_request(envelope['body'],binding,identity,at)
        size=len(_json(envelope).encode());self.total_wire_bytes+=size
        response=self.exchange(envelope,timeout_seconds=_remaining(self.clock,started,deadline),
            max_response_bytes=wire.MAX_WIRE_BYTES-size)
        _remaining(self.clock,started,deadline);response_at=wire._envelope(response)
        received_size=len(_json(response).encode());self.total_wire_bytes+=received_size
        if size+received_size>wire.MAX_WIRE_BYTES:raise ValueError('protective response exceeds combined wire bound')
        if response['nonce']!=nonce:raise ValueError('protective response nonce/binding differs')
        wire._fresh(response_at,_now(self.now()))
        if not hmac.compare_digest(sign_command(self.secret,nonce,response_at,response['body']),response['signature']):
            raise ValueError('protective response authentication failed')
        result=wire._decode(response['body']);_scope(result,wire.RESPONSE_FIELDS,RESPONSE_PURPOSE,binding,identity)
        if result['request_sha256']!=_hash(envelope):raise ValueError('protective response request binding differs')
        head=witness.validate_head(result['head'],binding,identity)
        _remaining(self.clock,started,deadline)
        return head

    def read(self,*,binding,identity,timeout):
        return self._call(binding,identity,'read',None,None,None,timeout)

    def apply(self,view,*,binding,identity,action,expected_head_sha256,operation_id,timeout):
        return self._call(binding,identity,action,view,expected_head_sha256,operation_id,timeout)
