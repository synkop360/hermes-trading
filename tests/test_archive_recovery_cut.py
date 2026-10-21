"""Original full-cohort/claim coherence checks; no model or market study."""
from copy import deepcopy
from contextlib import ExitStack,closing
from pathlib import Path
import asyncio,hashlib,importlib.util,json,sqlite3,tempfile,unittest,uuid
from types import SimpleNamespace
from unittest.mock import patch

from hermes_trading import archive_protective_session as session
from hermes_trading.reflection_supervision import _hash,_json


class SharedClaimTests(unittest.TestCase):
    def setUp(self):
        import test_archive_protective_session as native
        self.f=native.ProtectiveSessionTests();self.f.setUp()
        for function,args,kwargs in self.f._cleanups:self.addCleanup(function,*args,**kwargs)
        self.f._cleanups.clear()

    def test_shared_validator_preserves_original_canonical_cache_and_returns_independent_value(self):
        validate=getattr(session,'validate_cache',None)
        self.assertTrue(callable(validate),'shared original cache validator is missing')
        self.f.session.reconcile()
        raw,digest=self.f.owner.execute('SELECT payload,sha256 FROM protective_cache').fetchone()
        value=validate(raw,digest,self.f.f.binding,self.f.f.identity)
        self.assertEqual(value,self.f.session.cache)
        value['spent_generation']=99
        self.assertEqual(self.f.session.cache['spent_generation'],0)
        bad=deepcopy(self.f.session.cache);bad['schema_version']=True
        with self.assertRaises(ValueError):validate(_json(bad),_hash(bad),self.f.f.binding,self.f.f.identity)
        with self.assertRaises(ValueError):validate(raw,'0'*64,self.f.f.binding,self.f.f.identity)

    def test_shared_claim_reconciliation_rejects_original_claim_without_frozen_packet(self):
        reconcile=getattr(session,'reconcile_claim',None)
        self.assertTrue(callable(reconcile),'shared original claim reconciliation is missing')
        head=self.f.session.reconcile();self.f.outage();self.f.session.claim_protection()
        view=self.f.session._view(head)
        with self.assertRaisesRegex(ValueError,'missing|ambiguous'):
            reconcile(self.f.session.cache,head,view)
        self.assertEqual(self.f.f.f.market.collect.await_count,0)

    def test_recovery_claim_cannot_disappear_through_cached_consumption_counters(self):
        from hermes_trading import archive_recovery_cut
        require=getattr(archive_recovery_cut,'_original_claim',None)
        self.assertTrue(callable(require),'strict recovery claim requirement is missing')
        head=asyncio.run(self.f.enter());self.f.outage();self.f.session.claim_protection()
        claimed=deepcopy(self.f.session.cache['protection'])
        asyncio.run(self.f.f.protect(head));self.f.error=None
        self.f.session.reconcile();cache=deepcopy(self.f.session.cache);cache['protection']=claimed
        view=self.f.session._view(cache['head'])
        self.assertIsNone(session.reconcile_claim(cache,cache['head'],view))
        with self.assertRaisesRegex(ValueError,'claim|packet'):require(cache,view)

    def test_nonce_policy_must_be_explicit_and_strict_before_any_file_access(self):
        from hermes_trading import archive_recovery_cut
        from hermes_trading.paper_publication import _release_hashes
        with self.assertRaisesRegex(ValueError,'nonce'):
            archive_recovery_cut.inspect_cut('missing-cohort','missing-pair','missing.zip',owner=None,
                enabled=True,expected_cohort_sha256='0'*64,expected_archive_sha256='0'*64,
                expected_certificate_sha256='0'*64,binding=self.f.f.binding,identity=self.f.f.identity,
                expected_nonce_store_id='0'*32,expected_head_sha256='0'*64,expected_release=_release_hashes(),
                through_sequence=1,now=self.f.f.f.at,max_nonce_rows=True,max_nonce_pages=64)

    def test_cached_ancestor_must_match_original_event_contents(self):
        from hermes_trading import archive_recovery_cut
        import time
        head=self.f.session.reconcile();cache=deepcopy(self.f.session.cache)
        budget=SimpleNamespace(check=lambda:None,clock=time.monotonic,deadline=time.monotonic()+10)
        archive_recovery_cut._ancestor(self.f.f.path,cache,head,budget)
        cache['head']['state']['point']['complete']=1
        with self.assertRaisesRegex(ValueError,'ancestor'):
            archive_recovery_cut._ancestor(self.f.f.path,cache,head,budget)


class DatabaseDeadlineTests(unittest.TestCase):
    def test_actual_readonly_sql_is_interrupted_by_shared_deadline(self):
        from hermes_trading.archive_certificate import _database
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'original.sqlite'
            with closing(sqlite3.connect(path)) as db:db.execute('CREATE TABLE original(id)');db.commit()
            original=path.read_bytes();checks=[]
            def deadline():
                checks.append(True)
                if len(checks)>=2:raise TimeoutError('original shared SQL deadline')
            with self.assertRaisesRegex(TimeoutError,'shared SQL deadline'):
                with _database(path,check=deadline) as db:
                    db.execute('WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<10000000) SELECT SUM(x) FROM n').fetchone()
            self.assertEqual(path.read_bytes(),original)
            self.assertGreaterEqual(len(checks),2)


class RecoveryCutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api=None
        if importlib.util.find_spec('hermes_trading.archive_recovery_cut') is None:return
        from hermes_trading import archive_recovery_cut
        cls.api=archive_recovery_cut
        import test_archive_protective_worker as native
        from hermes_trading.archive_capacity import StorageDomain
        from hermes_trading.archive_nonce import BoundedArchiveNonceStore
        from hermes_trading.archive_protective_receiver import ProtectiveReceiver
        from hermes_trading.archive_protective_transport import ProtectiveServer
        from hermes_trading.archive_protective_export import export_pair,restore_pair
        from hermes_trading.archive_coordinator import capture
        from hermes_trading.archive_snapshot import snapshot
        from hermes_trading.archive_store import seal,restore
        from hermes_trading.paper_publication import _release_hashes
        from hermes_trading.reflection_supervision import initialize_supervisor
        cls.stack=ExitStack();cls.addClassCleanup(cls.stack.close)
        f=cls.f=native.ProtectiveWorkerTests();f.setUp()
        for function,args,kwargs in f._cleanups:cls.stack.callback(function,*args,**kwargs)
        f._cleanups.clear();f.repo.close();cls.root=f.f.path.parent
        cls.domain=StorageDomain(cls.root,domain_id='native-cohort-cut',limit_bytes=128*1024**2,
            reserve_bytes=1024**2,enabled=True);cls.domain.initialize()
        cls.owner=cls.stack.enter_context(cls.domain.ownership())
        cls.generation=uuid.uuid4().hex
        options=dict(owner=cls.owner,expected_store_id=cls.generation,binding=f.f.binding,identity=f.f.identity,
            enabled=True,max_rows=16,max_pages=64)
        with cls.owner.admit(4*1024**2) as lease:
            BoundedArchiveNonceStore.initialize(cls.root/'bounded.sqlite',lease=lease,**options)
        nonce=BoundedArchiveNonceStore(cls.root/'bounded.sqlite',**options)
        receiver=ProtectiveReceiver(f.f.path,f.f.binding,f.f.identity,enabled=True,max_bytes=1024**2,clock=cls.domain.clock)
        cls.stack.callback(receiver.close)
        with cls.owner.admit(1024**2):receiver.prepare()
        f.f.head=receiver.read
        f.server=ProtectiveServer(f.f.path,f.f.binding,f.f.identity,f.server.secret,nonce,
            enabled=True,max_db_bytes=1024**2,clock=cls.domain.clock,receiver=receiver)
        for profile in f.f.f.manifest['profiles']:
            folder=f.f.f.root/profile['directory'];(folder/'history').mkdir(exist_ok=True)
            (folder/'history/v0001.yaml').write_bytes((folder/'strategy.yaml').read_bytes())
        result=asyncio.run(f.invoke(f.outage));assert result['completed_batches']==2
        head=receiver.read();cls.artifact=cls.root/'pair.zip'
        receipt=export_pair(receiver,nonce,cls.artifact,enabled=True,expected_head_sha256=head['head_sha256'],max_bytes=2*1024**2)
        cls.pair=cls.root/'restored-pair'
        restore_pair(cls.artifact,cls.pair,owner=cls.owner,enabled=True,
            expected_archive_sha256=receipt['archive_sha256'],expected_certificate_sha256=receipt['certificate_sha256'],
            binding=f.f.binding,identity=f.f.identity,expected_nonce_store_id=cls.generation,
            expected_head_sha256=head['head_sha256'],max_bytes=2*1024**2,max_nonce_rows=16,max_nonce_pages=64)
        pc=cls.root/'original-supervisor';pc.mkdir()
        for name in ('reflection_owner.json','strategy.yaml','goal.yaml','trades.jsonl'):
            (pc/name).write_bytes((f.f.f.primary/name).read_bytes())
        initialize_supervisor(pc,now=f.f.f.at)
        with closing(sqlite3.connect(pc/'watcher_cycle.sqlite')) as db:
            db.execute('CREATE TABLE cycle(id)');db.commit()
        cls.release=_release_hashes();cohort=cls.root/'cohort'
        result=capture(pc,cohort,enabled=True,
            worker_capture=lambda target,**limits:snapshot(f.f.f.root,target,enabled=True,**limits),
            experiment_id='paired-test',expected_release=cls.release,through_sequence=1,
            max_bytes=16*1024**2,timeout_seconds=90,now=f.f.f.at)
        cls.inventory=result['manifest_sha256'];cls.cohort_certificate=result['certificate']
        names=[r['path'] for r in json.loads((cohort/'snapshot_inventory.json').read_text())['files']]+['snapshot_inventory.json']
        digest=seal(cohort,cls.root/'objects',names,experiment='paired-test',first_batch=1,last_batch=2)
        cls.cohort=cls.root/'restored-cohort';restore(cls.root/'objects',digest,cls.cohort)
        cls.options=dict(owner=cls.owner,enabled=True,expected_cohort_sha256=cls.inventory,
            expected_archive_sha256=receipt['archive_sha256'],expected_certificate_sha256=receipt['certificate_sha256'],
            binding=f.f.binding,identity=f.f.identity,expected_nonce_store_id=cls.generation,
            expected_head_sha256=head['head_sha256'],expected_release=cls.release,through_sequence=1,
            max_bytes=32*1024**2,timeout_seconds=120,now=f.f.f.at,max_nonce_rows=16,max_nonce_pages=64)

    def inspect(self,**changes):
        self.assertIsNotNone(self.api,'whole-cohort recovery-cut API is missing')
        return self.api.inspect_cut(self.cohort,self.pair,self.artifact,**dict(self.options,**changes))

    def files(self):
        return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for root in (self.cohort,self.pair)
                for p in root.rglob('*') if p.is_file()}

    def test_original_full_cohort_and_offline_claim_are_bound_without_resumption_authority(self):
        if self.api is None:self.inspect()
        before=self.files();result=self.inspect()
        verified=self.api.verify_cut(self.cohort,self.pair,self.artifact,
            expected_cut_sha256=result['cut_sha256'],**self.options)
        self.assertEqual(verified,result)
        self.assertEqual(result['cut']['source_point']['complete'],2)
        self.assertEqual(result['cut']['receiver_point']['complete'],1)
        self.assertEqual(result['cut']['original_claim']['sequence'],2)
        self.assertIsNotNone(result['cut']['original_claim']['input_hash'])
        self.assertEqual(result['cut']['cohort_certificate_sha256'],_hash(self.cohort_certificate))
        self.assertEqual(len(result['cut']['account_snapshots']),8)
        for field in ('independent_backup_verified','post_cut_activity_reconciled','runtime_resumption_authorized','retirement_authorized'):
            self.assertIs(result[field],False)
        self.assertEqual(self.f.f.f.market.collect.await_count,2)
        self.assertEqual(self.files(),before)

    def test_stale_external_cut_or_wrong_pair_certificate_cannot_be_selected(self):
        if self.api is None:self.inspect()
        with self.assertRaisesRegex(ValueError,'cut'):
            self.api.verify_cut(self.cohort,self.pair,self.artifact,expected_cut_sha256='0'*64,**self.options)
        with self.assertRaisesRegex(ValueError,'certificate|digest'):
            self.inspect(expected_certificate_sha256='0'*64)

    def test_extra_pair_file_and_changed_cohort_after_inspection_are_rejected(self):
        if self.api is None:self.inspect()
        extra=self.pair/'nonces.sqlite-journal';extra.write_bytes(b'unknown hot recovery')
        try:
            with self.assertRaisesRegex(ValueError,'inventory|member|sidecar'):self.inspect()
        finally:extra.unlink()
        target=self.cohort/'worker/adaptive_50/goal.yaml';original=target.read_bytes()
        def mutate(point):
            if point=='before_return':target.write_bytes(original+b'\n# changed after verification\n')
        try:
            with self.assertRaisesRegex(ValueError,'inventory|evidence|changed'):self.inspect(fault=mutate)
        finally:target.write_bytes(original)

    def test_disabled_budget_and_expired_deadline_cannot_inspect(self):
        if self.api is None:self.inspect()
        from hermes_trading.archive_capacity import CapacityUnavailable
        with self.assertRaisesRegex(ValueError,'disabled'):self.inspect(enabled=False)
        with self.assertRaisesRegex(ValueError,'byte|budget|bound'):self.inspect(max_bytes=1)
        with self.assertRaises((TimeoutError,ValueError,CapacityUnavailable)):self.inspect(timeout_seconds=.000001)

    def test_duplicate_completion_marker_key_is_not_accepted_as_original_bytes(self):
        if self.api is None:self.inspect()
        marker=self.pair/'RESTORE_COMPLETE.json';original=marker.read_bytes()
        marker.write_bytes(original[:-1]+b',"retirement_authorized":false}')
        try:
            with self.assertRaisesRegex(ValueError,'marker'):self.inspect()
        finally:marker.write_bytes(original)

    def test_same_head_and_nonce_floor_cannot_hide_lost_nonce_rows(self):
        if self.api is None:self.inspect()
        path=self.pair/'nonces.sqlite';original=path.read_bytes()
        try:
            with closing(sqlite3.connect(path)) as db:
                count=db.execute('SELECT COUNT(*) FROM nonce_entries').fetchone()[0];self.assertGreater(count,0)
                db.execute('DELETE FROM nonce_entries');db.commit()
            with self.assertRaisesRegex(ValueError,'database byte evidence'):self.inspect()
        finally:path.write_bytes(original)

    def test_unregistered_input_and_insufficient_recovery_allowance_fail_before_sql(self):
        if self.api is None:self.inspect()
        from hermes_trading.archive_capacity import CapacityExceeded
        from hermes_trading.archive_nonce import NONCE_PEAK_BYTES
        with patch('sqlite3.connect',side_effect=AssertionError('SQL opened before admission/registration')):
            with self.assertRaisesRegex(ValueError,'registered'):
                self.api.inspect_cut(self.f.f.f.root,self.pair,self.artifact,**self.options)
        with self.owner.admit(0) as lease:used=lease.report['state_bytes']
        filler=self.root/'owned-recovery-headroom.bin'
        length=self.domain.limit_bytes-self.domain.reserve_bytes-used-NONCE_PEAK_BYTES+1
        self.assertGreater(length,0)
        try:
            with filler.open('xb') as stream:stream.truncate(length)
            with patch('sqlite3.connect',side_effect=AssertionError('SQL opened before capacity denial')):
                with self.assertRaises(CapacityExceeded):self.inspect()
        finally:filler.unlink(missing_ok=True)


if __name__=='__main__':unittest.main()
