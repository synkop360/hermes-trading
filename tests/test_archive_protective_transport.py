"""Invented authenticated version2 messages; no listener or paid services."""
from copy import deepcopy
from datetime import timedelta
import json,secrets,unittest,uuid

import test_archive_protective_witness as kernel
from hermes_trading.archive_protective_transport import ProtectiveClient,ProtectiveServer
from hermes_trading.control_auth import SQLiteNonceReplayStore,sign_command
from hermes_trading.persistence.repository import StateRepository
from hermes_trading.reflection_supervision import _json


class ProtectiveTransportTests(unittest.TestCase):
    def setUp(self):
        self.f=kernel.ProtectiveWitnessTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.repo=StateRepository.open(self.f.path.parent/'nonce.sqlite');self.repo.migrate();self.addCleanup(self.repo.close)
        self.secret=secrets.token_hex(32);self.calls=[]
        self.server=ProtectiveServer(self.f.path,self.f.binding,self.f.identity,self.secret,
            SQLiteNonceReplayStore(self.repo),enabled=True,max_db_bytes=1024*1024)
        self.client=ProtectiveClient(self.f.binding,self.f.identity,self.secret,self.exchange,enabled=True,now=lambda:self.f.now)

    def exchange(self,envelope,*,timeout_seconds,max_response_bytes):
        result=self.server.handle(envelope,now=self.f.now,timeout_seconds=timeout_seconds)
        self.assertLessEqual(len(_json(result).encode()),max_response_bytes)
        self.calls.append((deepcopy(envelope),deepcopy(result)));return result

    def read(self):return self.client.read(binding=self.f.binding,identity=self.f.identity,timeout=10)
    def apply(self,head,view=None,action='reserve',operation=None):
        return self.client.apply(view or self.f.view(head),binding=self.f.binding,identity=self.f.identity,
            action=action,expected_head_sha256=head['head_sha256'],operation_id=operation or uuid.uuid4().hex,timeout=10)

    def resign(self,envelope,**changes):
        value=json.loads(envelope['body']);value.update(changes);body=_json(value);nonce=uuid.uuid4().hex
        return dict(body=body,nonce=nonce,timestamp=self.f.now.isoformat(),signature=sign_command(self.secret,nonce,self.f.now,body))

    def test_signed_normal_plus_protective_suffix_and_idempotent_lost_reply(self):
        self.assertEqual(self.read(),self.f.head());head=self.apply(self.read())
        normal=self.f.completion(head,'ordinary')
        protected=self.f.completion(head,'protective',sequence=2,previous=normal['prefix_sha256'])
        view=self.f.view(head,suffix=[normal,protected]);operation=uuid.uuid4().hex
        def lose(*args,**kwargs):self.exchange(*args,**kwargs);raise OSError('lost reply')
        self.client.exchange=lose
        with self.assertRaises(OSError):self.apply(head,view,'check',operation)
        self.client.exchange=self.exchange
        result=self.apply(head,view,'check',operation)
        self.assertEqual(result,self.f.head());self.assertEqual(result['state']['point']['complete'],2)
        self.assertIsNone(result['state']['credit']);self.assertEqual(result['sequence'],2)
        self.assertNotEqual(self.calls[-1][0]['nonce'],self.calls[-2][0]['nonce'])

    def test_purpose_version_identity_and_remote_cancel_are_rejected(self):
        self.read();request=self.calls[-1][0];before=self.f.head()
        for changes in ({'purpose':'paired_archive_witness_request_v1'},{'schema_version':1},
                {'identity_sha256':'a'*64},{'action':'cancel'}):
            with self.assertRaises(ValueError):self.server.handle(self.resign(request,**changes),now=self.f.now)
        self.assertEqual(self.f.head(),before)

    def test_durable_replay_wrong_key_and_expired_messages(self):
        self.read();request=self.calls[-1][0]
        with self.assertRaisesRegex(ValueError,'authentication|replay'):self.server.handle(request,now=self.f.now)
        self.repo.close();self.repo=StateRepository.open(self.f.path.parent/'nonce.sqlite');self.addCleanup(self.repo.close)
        self.server.nonce_store=SQLiteNonceReplayStore(self.repo)
        with self.assertRaisesRegex(ValueError,'authentication|replay'):self.server.handle(request,now=self.f.now)
        fresh=self.resign(request);fresh['signature']='0'*64
        with self.assertRaisesRegex(ValueError,'authentication'):self.server.handle(fresh,now=self.f.now)
        with self.assertRaises(ValueError):self.server.handle(self.resign(request),now=self.f.now+timedelta(seconds=300))

    def test_response_tampering_replay_and_signed_malformed_head_are_integrity_errors(self):
        self.read();old=self.calls[-1][1];self.client.exchange=lambda *a,**k:old
        with self.assertRaisesRegex(ValueError,'nonce|binding'):self.read()
        def tamper(*args,**kwargs):
            result=self.exchange(*args,**kwargs);body=json.loads(result['body']);body['head']['state']['credit']['generation']=0
            result['body']=_json(body);return result
        self.client.exchange=tamper
        with self.assertRaisesRegex(ValueError,'authentication'):self.read()
        def malformed(*args,**kwargs):
            result=tamper(*args,**kwargs)
            result['signature']=sign_command(self.secret,result['nonce'],self.f.now,result['body']);return result
        self.client.exchange=malformed
        with self.assertRaises(ValueError):self.read()

    def test_disabled_nondurable_nonce_store_and_bounded_deadline(self):
        self.client.enabled=False
        with self.assertRaisesRegex(ValueError,'disabled'):self.read()
        self.client.enabled=True;self.server.nonce_store=set()
        with self.assertRaisesRegex(ValueError,'durable'):self.read()
        self.server.nonce_store=SQLiteNonceReplayStore(self.repo)
        self.client.exchange=lambda *a,**k:dict(body='x'*8193,nonce='a'*32,timestamp=self.f.now.isoformat(),signature='b'*64)
        with self.assertRaisesRegex(ValueError,'bound'):self.read()
        clock=[0]
        def slow(*args,**kwargs):result=self.exchange(*args,**kwargs);clock[0]=10;return result
        self.client.exchange=slow;self.client.clock=lambda:clock[0]
        with self.assertRaises(TimeoutError):self.read()


class AuthenticatedProtectiveSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_eight_account_suffix_reconciles_over_signed_protocol_after_lost_reply(self):
        import test_archive_protective_source as native
        f=native.ProtectiveSourceTests();f.setUp()
        for function,args,kwargs in f._cleanups:self.addCleanup(function,*args,**kwargs)
        f._cleanups.clear()
        repo=StateRepository.open(f.path.parent/'native-nonce.sqlite');repo.migrate();self.addCleanup(repo.close)
        secret=secrets.token_hex(32)
        server=ProtectiveServer(f.path,f.binding,f.identity,secret,SQLiteNonceReplayStore(repo),enabled=True)
        calls=[]
        def exchange(envelope,*,timeout_seconds,max_response_bytes):
            response=server.handle(envelope,now=f.f.at,timeout_seconds=timeout_seconds)
            self.assertLessEqual(len(_json(response).encode()),max_response_bytes)
            calls.append(envelope);return response
        client=ProtectiveClient(f.binding,f.identity,secret,exchange,enabled=True,now=f.f.clock)
        def apply(head,view=None,action='check',operation=None):
            return client.apply(view or f.view(head),binding=f.binding,identity=f.identity,action=action,
                expected_head_sha256=head['head_sha256'],operation_id=operation or uuid.uuid4().hex,timeout=10)
        f.apply=apply
        head=await f.enter();await f.protect(head);view=f.view(head)
        self.assertEqual([row['kind'] for row in view['suffix']],['ordinary','protective'])
        operation=uuid.uuid4().hex
        def lose(*args,**kwargs):exchange(*args,**kwargs);raise OSError('native reply lost')
        client.exchange=lose
        with self.assertRaises(OSError):apply(head,view,operation=operation)
        client.exchange=exchange;done=apply(head,view,operation=operation)
        self.assertEqual(done,f.head());self.assertEqual(done['state']['point']['complete'],2)
        self.assertIsNone(done['state']['credit']);self.assertEqual(f.f.market.collect.await_count,2)
        self.assertNotEqual(calls[-1]['nonce'],calls[-2]['nonce'])
        for profile in f.f.manifest['profiles']:
            self.assertEqual(f.f.positions(profile['directory'])[0].status.value,'closed')
