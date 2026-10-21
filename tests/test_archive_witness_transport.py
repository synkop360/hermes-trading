"""Synthetic authenticated protocol checks; no sockets, providers or live data."""
from copy import deepcopy
from datetime import timedelta
import json,secrets,unittest,uuid

import test_archive_witness as kernel
import test_archive_witness_worker as native
from hermes_trading.archive_witness_transport import WitnessServer,WitnessClient,REQUEST_PURPOSE,RESPONSE_PURPOSE
from hermes_trading.control_auth import SQLiteNonceReplayStore,sign_command
from hermes_trading.persistence.repository import StateRepository
from hermes_trading.reflection_supervision import _json,_hash


class WitnessTransportTests(unittest.TestCase):
    def setUp(self):
        self.f=kernel.WitnessTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.repo=StateRepository.open(self.f.root/'nonce.sqlite');self.repo.migrate();self.addCleanup(lambda:self.repo.close())
        self.secret=secrets.token_hex(32);self.calls=[]
        self.server=self.make_server()
        self.client=WitnessClient(self.f.binding,self.f.identity,self.secret,self.exchange,enabled=True,now=lambda:kernel.NOW)

    def make_server(self,**kwargs):
        return WitnessServer(self.f.path,self.f.binding,self.f.identity,self.secret,SQLiteNonceReplayStore(self.repo),enabled=kwargs.pop('enabled',True),**kwargs)

    def exchange(self,envelope,*,timeout_seconds,max_response_bytes):
        self.assertGreater(timeout_seconds,0);self.assertLessEqual(timeout_seconds,10)
        result=self.server.handle(envelope,now=kernel.NOW,timeout_seconds=timeout_seconds)
        self.assertLessEqual(len(_json(result).encode()),max_response_bytes)
        self.calls.append((deepcopy(envelope),deepcopy(result)));return result

    def read(self):return self.client.read(binding=self.f.binding,identity=self.f.identity,timeout=10)
    def apply(self,view,action='reserve',**overrides):
        return self.client.apply(view,binding=self.f.binding,identity=self.f.identity,action=action,
            expected_head_sha256=overrides.pop('expected_head_sha256',self.f.head()['head_sha256']),
            operation_id=overrides.pop('operation_id',uuid.uuid4().hex),timeout=10,**overrides)

    def resign(self,envelope,**updates):
        value=json.loads(envelope['body']);value.update(updates);body=_json(value)
        nonce=uuid.uuid4().hex
        return {'body':body,'nonce':nonce,'timestamp':kernel.NOW.isoformat(),
                'signature':sign_command(self.secret,nonce,kernel.NOW,body)}

    def test_signed_read_reserve_pending_and_completion_roundtrip(self):
        self.assertEqual(self.read(),self.f.head())
        response_body=json.loads(self.calls[-1][1]['body']);self.assertEqual(response_body['purpose'],RESPONSE_PURPOSE)
        token=self.apply(self.f.view())['state']['reservation']['reservation_id']
        self.apply(self.f.view(pending=token),action='check')
        complete=self.apply(self.f.view(1,latest=token),action='check')
        self.assertEqual(complete['state']['point']['complete'],1);self.assertIsNone(complete['state']['reservation'])
        self.assertEqual(json.loads(self.calls[-1][0]['body'])['purpose'],REQUEST_PURPOSE)

    def test_request_replay_stays_rejected_after_nonce_database_reopen(self):
        self.read();envelope=self.calls[-1][0]
        self.repo.close();self.repo=StateRepository.open(self.f.root/'nonce.sqlite');self.repo.migrate()
        reopened=self.make_server()
        with self.assertRaisesRegex(ValueError,'authentication|replay'):reopened.handle(envelope,now=kernel.NOW)

    def test_bad_signature_scope_validity_and_remote_recovery_actions_rejected(self):
        self.read();original=self.calls[-1][0];before=self.f.head()
        bad=deepcopy(original);bad['nonce']=uuid.uuid4().hex;bad['signature']='0'*64
        with self.assertRaisesRegex(ValueError,'authentication'):self.server.handle(bad,now=kernel.NOW)
        for update in ({'purpose':'control_command'},{'experiment_id':'wrong'},{'action':'cancel'},
                       {'action':'initialize'},{'action':'retire'},{'schema_version':True},{'identity_sha256':'f'*64}):
            with self.subTest(update=update),self.assertRaises(ValueError):self.server.handle(self.resign(original,**update),now=kernel.NOW)
        for seconds in (-1,300):
            with self.assertRaisesRegex(ValueError,'validity'):
                self.server.handle(self.resign(original),now=kernel.NOW+timedelta(seconds=seconds))
        self.assertEqual(self.f.head(),before)

    def test_memory_nonce_store_and_changed_client_identity_are_rejected(self):
        self.read();envelope=self.calls[-1][0];before=self.f.head()
        self.server.nonce_store=set()
        with self.assertRaisesRegex(ValueError,'durable'):self.server.handle(self.resign(envelope),now=kernel.NOW)
        with self.assertRaisesRegex(ValueError,'identity|binding'):
            self.client.read(binding=self.f.binding,identity=dict(self.f.identity,max_cycles=2),timeout=10)
        self.assertEqual(self.f.head(),before)

    def test_wrong_key_and_noncanonical_or_cross_receiver_requests_are_rejected(self):
        self.read();envelope=self.calls[-1][0];before=self.f.head()
        bad_key=WitnessClient(self.f.binding,self.f.identity,secrets.token_hex(32),self.exchange,enabled=True,now=lambda:kernel.NOW)
        with self.assertRaisesRegex(ValueError,'authentication'):
            bad_key.read(binding=self.f.binding,identity=self.f.identity,timeout=10)
        other=dict(self.f.binding,witness_id='f'*32)
        with self.assertRaisesRegex(ValueError,'binding|scope'):
            self.server.handle(self.resign(envelope,binding=other),now=kernel.NOW)
        bad=self.resign(envelope);bad['body']=bad['body']+' '
        bad['signature']=sign_command(self.secret,bad['nonce'],kernel.NOW,bad['body'])
        with self.assertRaisesRegex(ValueError,'canonical'):
            self.server.handle(bad,now=kernel.NOW)
        self.assertEqual(self.f.head(),before)

    def test_response_tamper_and_old_signed_response_cannot_satisfy_new_request(self):
        self.read();old=deepcopy(self.calls[-1][1])
        self.client.exchange=lambda *args,**kwargs:old
        with self.assertRaisesRegex(ValueError,'binding|nonce'):self.read()
        def tamper(*args,**kwargs):
            result=self.exchange(*args,**kwargs);value=json.loads(result['body']);value['head']['state']['point']['complete']=2
            result['body']=_json(value);return result
        self.client.exchange=tamper
        with self.assertRaisesRegex(ValueError,'authentication'):self.read()

    def test_lost_reserve_reply_retry_uses_new_nonce_but_same_operation(self):
        operation=uuid.uuid4().hex;head=self.f.head()['head_sha256'];view=self.f.view()
        def lose(*args,**kwargs):self.exchange(*args,**kwargs);raise RuntimeError('lost reply')
        self.client.exchange=lose
        with self.assertRaisesRegex(RuntimeError,'lost reply'):
            self.apply(view,operation_id=operation,expected_head_sha256=head)
        self.client.exchange=self.exchange
        retry=self.apply(view,operation_id=operation,expected_head_sha256=head)
        self.assertEqual(retry['sequence'],1);self.assertEqual(retry['state']['reservation']['reservation_id'],operation)
        self.assertNotEqual(self.calls[-1][0]['nonce'],self.calls[-2][0]['nonce'])

    def test_disabled_and_oversized_or_late_exchange_fail_closed(self):
        self.read();envelope=self.calls[-1][0]
        with self.assertRaisesRegex(ValueError,'disabled'):self.make_server(enabled=False).handle(envelope,now=kernel.NOW)
        disabled=WitnessClient(self.f.binding,self.f.identity,self.secret,self.exchange,now=lambda:kernel.NOW)
        with self.assertRaisesRegex(ValueError,'disabled'):disabled.read(binding=self.f.binding,identity=self.f.identity,timeout=10)
        self.client.exchange=lambda *args,**kwargs:{'body':'x'*8193,'nonce':'a'*32,'timestamp':kernel.NOW.isoformat(),'signature':'b'*64}
        with self.assertRaisesRegex(ValueError,'bound|bytes'):self.read()
        elapsed=[0]
        def late(*args,**kwargs):result=self.exchange(*args,**kwargs);elapsed[0]=2;return result
        client=WitnessClient(self.f.binding,self.f.identity,self.secret,late,enabled=True,now=lambda:kernel.NOW,clock=lambda:elapsed[0])
        with self.assertRaises(TimeoutError):client.read(binding=self.f.binding,identity=self.f.identity,timeout=1)


class AuthenticatedNativeWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f=native.NativeWitnessWorkerTests();self.f.setUp()
        for function,args,kwargs in self.f._cleanups:self.addCleanup(function,*args,**kwargs)
        self.f._cleanups.clear()
        self.repo=StateRepository.open(self.f.path.parent/'nonce.sqlite');self.repo.migrate();self.addCleanup(self.repo.close)
        self.secret=secrets.token_hex(32)
        self.server=WitnessServer(self.f.path,self.f.binding,self.f.identity,self.secret,SQLiteNonceReplayStore(self.repo),enabled=True)
        self.f.client=WitnessClient(self.f.binding,self.f.identity,self.secret,self.exchange,enabled=True,now=self.f.f.clock)

    def exchange(self,envelope,*,timeout_seconds,max_response_bytes):
        return self.server.handle(envelope,now=self.f.f.at,timeout_seconds=timeout_seconds)

    async def test_native_two_common_batches_use_authenticated_receiver(self):
        result=await self.f.invoke()
        self.assertEqual(result['completed_batches'],2);self.assertEqual(self.f.head()['state']['point']['complete'],2)
        await self.f.invoke();self.assertEqual(self.f.f.market.collect.await_count,2)

    async def test_tampered_receiver_response_blocks_native_collection(self):
        def tampered(*args,**kwargs):
            result=self.exchange(*args,**kwargs);value=json.loads(result['body']);value['head']['state']['point']['complete']=99
            result['body']=_json(value);return result
        self.f.client.exchange=tampered
        with self.assertRaisesRegex(ValueError,'authentication'):await self.f.invoke()
        self.assertEqual(self.f.f.market.collect.await_count,0)
        heartbeat=json.loads((self.f.f.primary/'heartbeat.json').read_text())
        self.assertEqual(heartbeat['status'],'degraded');self.assertIn('witness_failed',heartbeat['error'])


if __name__=='__main__':unittest.main()
