from contextlib import closing
from datetime import UTC,datetime,timedelta
import base64,json,random,secrets,tempfile,unittest
from unittest.mock import patch
from pathlib import Path

from hermes_trading import archive_store as store
from hermes_trading.archive_transfer import ReadServer,ReadClient,transfer
from hermes_trading.control_auth import SQLiteNonceReplayStore,sign_command
from hermes_trading.persistence.repository import StateRepository
from hermes_trading.reflection_supervision import _json,_hash

NOW=datetime(2026,10,16,tzinfo=UTC)


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.source=self.root/'source';self.source.mkdir()
        self.sender=self.root/'sender';self.receiver=self.root/'receiver'
        raw=random.Random(20261016).randbytes(store.CHUNK_BYTES+50)
        (self.source/'rows.bin').write_bytes(raw)
        self.digest=store.seal(self.source,self.sender,['rows.bin'],experiment='synthetic-transfer',first_batch=1,last_batch=2)
        self.repo=StateRepository.open(self.root/'nonce.sqlite');self.repo.migrate();self.addCleanup(self.repo.close)
        self.secret=secrets.token_hex(32);self.calls=[]
        self.server=ReadServer(self.sender,'synthetic-transfer',self.secret,SQLiteNonceReplayStore(self.repo),enabled=True)
        self.client=ReadClient('synthetic-transfer',self.secret,self.exchange,now=lambda:NOW)

    def exchange(self,envelope,*,timeout_seconds,max_response_bytes):
        self.assertGreater(timeout_seconds,0);self.assertLessEqual(timeout_seconds,120)
        request=json.loads(envelope['body']);self.calls.append((request['kind'],request['sha256']))
        result=self.server.handle(envelope,now=NOW)
        self.assertLessEqual(len(_json(result).encode()),max_response_bytes)
        return result

    def invoke(self,**kwargs):
        return transfer(self.client,self.receiver,self.digest,
            **dict({'enabled':True,'max_wire_bytes':16*1024*1024,'timeout_seconds':30},**kwargs))

    def envelope(self,**overrides):
        body=_json(dict({'schema_version':1,'purpose':'paired_archive_read_v1','experiment_id':'synthetic-transfer',
            'kind':'manifest','sha256':self.digest},**overrides))
        nonce=secrets.token_hex(16)
        return {'body':body,'nonce':nonce,'timestamp':NOW.isoformat(),
            'signature':sign_command(self.secret,nonce,NOW,body)}

    def test_authenticated_roundtrip_and_repeat_uses_verified_cache(self):
        receipt=self.invoke()
        self.assertEqual(receipt['manifest_sha256'],self.digest)
        self.assertFalse(receipt['retirement_authorized'])
        target=self.root/'restored';store.restore(self.receiver,self.digest,target)
        self.assertEqual((target/'rows.bin').read_bytes(),(self.source/'rows.bin').read_bytes())
        count=len(self.calls);again=self.invoke()
        self.assertEqual(len(self.calls),count)
        self.assertEqual(again['wire_bytes'],0)

    def test_interrupted_transfer_reuses_verified_objects_without_publishing_manifest(self):
        def fault(point):
            if point=='after_object':raise RuntimeError('disconnect')
        with self.assertRaisesRegex(RuntimeError,'disconnect'):self.invoke(fault=fault)
        self.assertFalse((self.receiver/'manifests'/(self.digest+'.json')).exists())
        first=[value for kind,value in self.calls if kind=='object'][0]
        self.invoke()
        self.assertEqual(self.calls.count(('object',first)),1)
        store.validate(self.receiver,self.digest)

    def test_signature_scope_expiry_and_durable_nonce_replay(self):
        envelope=self.envelope();self.server.handle(envelope,now=NOW)
        self.repo.close();self.repo=StateRepository.open(self.root/'nonce.sqlite');self.repo.migrate()
        self.addCleanup(self.repo.close)
        reopened=ReadServer(self.sender,'synthetic-transfer',self.secret,SQLiteNonceReplayStore(self.repo),enabled=True)
        with self.assertRaisesRegex(ValueError,'authentication'):reopened.handle(envelope,now=NOW)
        for update in ({'experiment_id':'wrong'},{'purpose':'control_command'},{'kind':'../../keys'},{'schema_version':True}):
            with self.subTest(update=update),self.assertRaises(ValueError):reopened.handle(self.envelope(**update),now=NOW)
        for offset in (-1,300):
            with self.assertRaisesRegex(ValueError,'validity'):reopened.handle(self.envelope(),now=NOW+timedelta(seconds=offset))
        bad=self.envelope();bad['signature']='0'*64
        with self.assertRaisesRegex(ValueError,'authentication'):reopened.handle(bad,now=NOW)

    def test_tampered_transport_object_and_corrupt_cache_are_rejected(self):
        original=self.client.exchange
        def tampered(envelope,**kwargs):
            result=original(envelope,**kwargs)
            if result['kind']=='object':result['data_base64']=base64.b64encode(b'wrong').decode()
            return result
        self.client.exchange=tampered
        with self.assertRaises(ValueError):self.invoke()
        self.assertFalse((self.receiver/'manifests'/(self.digest+'.json')).exists())
        self.client.exchange=original;self.invoke()
        next((self.receiver/'objects').iterdir()).write_bytes(b'corrupt')
        with self.assertRaises(ValueError):self.invoke()

    def test_disabled_and_finite_budget_deadline_fail_closed(self):
        with self.assertRaisesRegex(ValueError,'disabled'):self.invoke(enabled=False)
        with self.assertRaises(ValueError):self.invoke(max_wire_bytes=10)
        tick=iter((0,100,200,300))
        with self.assertRaisesRegex(TimeoutError,'deadline'):self.invoke(clock=lambda:next(tick))
        self.assertFalse((self.receiver/'manifests'/(self.digest+'.json')).exists())
        server=ReadServer(self.sender,'synthetic-transfer',self.secret,SQLiteNonceReplayStore(self.repo))
        with self.assertRaisesRegex(ValueError,'disabled'):server.handle(self.envelope(),now=NOW)

    def test_complete_parent_chain_is_transferred_and_verified(self):
        (self.source/'rows.bin').write_bytes(b'next snapshot')
        self.digest=store.seal(self.source,self.sender,['rows.bin'],experiment='synthetic-transfer',
            first_batch=3,last_batch=4,parent=self.digest)
        result=self.invoke()
        self.assertEqual(result['chain_length'],2)
        self.assertEqual(len(store.validate(self.receiver,self.digest)),2)

    def test_unsupported_directory_durability_is_reported_before_any_ack(self):
        with patch.object(store,'directory_fsync_supported',return_value=False,create=True):
            result=self.invoke()
        self.assertFalse(result['local_directory_fsync_supported'])
        self.assertFalse(result['retirement_authorized'])


if __name__=='__main__':unittest.main()
