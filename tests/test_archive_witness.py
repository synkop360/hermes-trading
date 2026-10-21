from copy import deepcopy
from contextlib import closing
from datetime import datetime,timedelta,timezone
import hashlib,json,sqlite3,tempfile,unittest,uuid
from pathlib import Path

from hermes_trading.archive_witness import initialize,read,apply
from hermes_trading.reflection_supervision import _hash

NOW=datetime(2026,10,16,tzinfo=timezone.utc)


class WitnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.source=self.root/'worker';self.source.mkdir()
        self.path=self.root/'receiver.sqlite'
        self.identity={'experiment_id':'witness-test','cohort_sha256':'a'*64,'release_sha256':'b'*64,
            'policy_sha256':'c'*64,'limits_sha256':'d'*64,'max_cycles':3,'deadline':(NOW+timedelta(hours=1)).isoformat()}
        self.binding=initialize(self.path,self.identity,source_root=self.source,enabled=True,now=NOW)

    def head(self,**kwargs):
        return read(self.path,self.binding,self.identity,enabled=True,**kwargs)

    def view(self,count=0,*,prior=None,pending=None,latest=None,revision=0,retired=0):
        return {'identity_sha256':_hash(self.identity),'complete':count,
            'prefix_sha256':_hash([]) if count==0 else _hash(['prefix',count]),
            'prior_prefix_sha256':self.head()['state']['point']['prefix_sha256'] if prior is None else prior,
            'archive_revision':revision,'retired_complete':retired,
            'checkpoint_sha256':_hash(None) if revision==0 else 'e'*64,
            'pending':pending is not None,'pending_reservation_id':pending,'latest_reservation_id':latest}

    def act(self,view,action='check',**kwargs):
        return apply(self.path,self.binding,self.identity,view,action=action,
            expected_head_sha256=kwargs.pop('expected_head_sha256',self.head()['head_sha256']),
            enabled=True,now=kwargs.pop('now',NOW),**kwargs)

    def reserve(self):
        return self.act(self.view(),action='reserve')['state']['reservation']['reservation_id']

    def test_unchanged_checks_do_not_consume_the_finite_event_budget(self):
        before=self.head()
        for _ in range(4):self.assertEqual(self.act(self.view()),before)
        token=self.reserve();reserved=self.head()
        for _ in range(4):self.assertEqual(self.act(self.view(pending=token)),reserved)

    def test_receiver_read_budget_rejects_existing_bytes_without_changing_history(self):
        before=self.head()
        with self.assertRaisesRegex(ValueError,'storage|bytes'):
            read(self.path,self.binding,self.identity,enabled=True,max_bytes=1)
        self.assertEqual(self.head(),before)

    def test_receiver_write_peak_budget_preserves_original_head(self):
        before=self.head();size=self.path.stat().st_size
        with self.assertRaisesRegex(ValueError,'storage|peak'):
            self.act(self.view(),action='reserve',max_bytes=size+1)
        self.assertEqual(self.head(),before)

    def test_receiver_deadline_and_nonfinite_bounds_fail_closed(self):
        before=self.head();ticks=iter([0,2,2,2])
        with self.assertRaises(TimeoutError):
            read(self.path,self.binding,self.identity,enabled=True,timeout_seconds=1,clock=lambda:next(ticks))
        for seconds in (True,0,float('nan'),float('inf'),11):
            with self.assertRaises(ValueError):read(self.path,self.binding,self.identity,enabled=True,timeout_seconds=seconds)
        self.assertEqual(self.head(),before)

    def test_receiver_timeout_at_precommit_rolls_back_original_reservation(self):
        before=self.head();view=self.view();elapsed=[0]
        def advance(point):
            if point=='before_commit':elapsed[0]=2
        with self.assertRaises(TimeoutError):
            self.act(view,action='reserve',timeout_seconds=1,clock=lambda:elapsed[0],fault=advance)
        self.assertEqual(self.head(),before)

    def test_receiver_budget_includes_sidecars_before_opening_sqlite(self):
        sidecar=self.path.with_name(self.path.name+'-wal');sidecar.write_bytes(b'not a real WAL'*100)
        try:
            with self.assertRaisesRegex(ValueError,'storage|bytes'):
                read(self.path,self.binding,self.identity,enabled=True,max_bytes=self.path.stat().st_size+1)
        finally:sidecar.unlink()

    def test_oversized_corrupt_event_is_rejected_before_json_decode(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('UPDATE witness_events SET payload=?',('x'*1024*1024,));db.commit()
        with self.assertRaisesRegex((ValueError,sqlite3.DataError),'large|big|bound|bytes'):
            self.head()

    def test_sqlite_enforces_page_ceiling_before_receiver_growth(self):
        original_size=self.path.stat().st_size;maximum=3*original_size+65536
        exhausted=False
        for _ in range(20):
            before=self.head()
            try:
                if before['state']['reservation'] is None:
                    self.act(self.view(),action='reserve',max_bytes=maximum)
                else:
                    self.act(self.view(),action='cancel',active_token=before['state']['reservation']['reservation_id'],max_bytes=maximum)
            except sqlite3.OperationalError as error:
                self.assertIn('full',str(error).lower());exhausted=True
                self.assertEqual(self.head(),before);break
        self.assertTrue(exhausted,'SQLite must reject growth past the configured page ceiling')
        self.assertLessEqual(self.path.stat().st_size,original_size)

    def test_chain_verification_checks_deadline_between_original_events(self):
        for _ in range(3):
            token=self.reserve();self.act(self.view(),action='cancel',active_token=token)
        before=self.head();elapsed=[0.0]
        def ticking():elapsed[0]+=.1;return elapsed[0]
        with self.assertRaises(TimeoutError):
            read(self.path,self.binding,self.identity,enabled=True,timeout_seconds=.35,clock=ticking)
        self.assertEqual(self.head(),before)

    def test_disabled_new_or_nested_paths_never_create_an_implicit_witness(self):
        with self.assertRaisesRegex(ValueError,'disabled'):
            initialize(self.root/'disabled.sqlite',self.identity,source_root=self.source)
        self.assertFalse((self.root/'disabled.sqlite').exists())
        with self.assertRaisesRegex(ValueError,'separate'):
            initialize(self.source/'bad.sqlite',self.identity,source_root=self.source,enabled=True,now=NOW)
        with self.assertRaisesRegex((ValueError,sqlite3.OperationalError),'missing|open'):
            read(self.root/'missing.sqlite',self.binding,self.identity,enabled=True)

    def test_genesis_and_identity_pin_cannot_be_silently_reinitialized(self):
        self.assertEqual(self.head()['state']['point']['complete'],0)
        with self.assertRaisesRegex(ValueError,'existing'):
            initialize(self.path,self.identity,source_root=self.source,enabled=True,now=NOW)
        with self.assertRaisesRegex(ValueError,'binding|identity'):
            read(self.path,dict(self.binding,genesis_sha256='f'*64),self.identity,enabled=True)
        with self.assertRaisesRegex(ValueError,'identity'):
            read(self.path,self.binding,dict(self.identity,max_cycles=4),enabled=True)

    def test_reserve_before_collection_and_resume_only_original_pending_packet(self):
        token=self.reserve();head=self.head()
        self.assertEqual(head['state']['reservation']['sequence'],1)
        self.assertEqual(head['state']['point']['complete'],0)
        with self.assertRaisesRegex(ValueError,'unresolved|original'):
            self.act(self.view(),action='reserve')
        with self.assertRaisesRegex(ValueError,'unresolved|original'):
            self.act(self.view(),action='reserve',active_token=token)
        resumed=self.act(self.view(pending=token),action='reserve')
        self.assertEqual(resumed['state']['reservation']['reservation_id'],token)
        self.assertEqual(resumed['state']['point']['complete'],0)

    def test_completion_and_lost_reply_retry_do_not_allocate_an_extra_slot(self):
        token=self.reserve();view=self.view(1,latest=token)
        operation=uuid.uuid4().hex;before=self.head()['head_sha256']
        result=self.act(view,operation_id=operation,expected_head_sha256=before)
        self.assertEqual(result['state']['point']['complete'],1)
        self.assertIsNone(result['state']['reservation'])
        retry=self.act(view,operation_id=operation,expected_head_sha256=before,now=NOW+timedelta(minutes=1))
        self.assertEqual(retry,result)
        with self.assertRaisesRegex(ValueError,'operation'):
            self.act(dict(view,prefix_sha256='f'*64),operation_id=operation,expected_head_sha256=before)

    def test_rollback_changed_prefix_or_unreserved_completion_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'reservation|complete'):
            self.act(self.view(1,latest=uuid.uuid4().hex))
        token=self.reserve();self.act(self.view(1,latest=token))
        with self.assertRaisesRegex(ValueError,'regress|rollback'):
            self.act(self.view(0))
        with self.assertRaisesRegex(ValueError,'prefix'):
            self.act(dict(self.view(1),prefix_sha256='f'*64))
        with self.assertRaisesRegex(ValueError,'prefix'):
            self.act(self.view(1,prior='f'*64))

    def test_limits_and_deadline_never_reset(self):
        for count in range(1,4):
            token=self.act(self.view(count-1),action='reserve')['state']['reservation']['reservation_id']
            self.act(self.view(count,latest=token))
        with self.assertRaisesRegex(ValueError,'cycle'):
            self.act(self.view(3),action='reserve')
        other=self.root/'deadline.sqlite'
        binding=initialize(other,self.identity,source_root=self.source,enabled=True,now=NOW)
        head=read(other,binding,self.identity,enabled=True)
        with self.assertRaisesRegex(ValueError,'deadline'):
            apply(other,binding,self.identity,self.view(prior=_hash([])),action='reserve',expected_head_sha256=head['head_sha256'],
                enabled=True,now=NOW+timedelta(hours=1))

    def test_cancel_requires_live_token_and_no_frozen_packet(self):
        token=self.reserve()
        with self.assertRaisesRegex(ValueError,'token|original|unresolved'):
            self.act(self.view(),action='cancel',active_token=uuid.uuid4().hex)
        with self.assertRaisesRegex(ValueError,'pending|frozen'):
            self.act(self.view(pending=token),action='cancel',active_token=token)
        self.act(self.view(),action='cancel',active_token=token)
        self.assertIsNone(self.head()['state']['reservation'])
        self.assertNotEqual(self.reserve(),token)

    def test_corruption_and_stale_head_never_reset_or_overwrite_evidence(self):
        old=self.head()['head_sha256'];self.reserve()
        with self.assertRaisesRegex(ValueError,'head'):
            self.act(self.view(),expected_head_sha256=old)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE witness_events SET sha256=? WHERE sequence=1",('f'*64,));db.commit()
        with self.assertRaisesRegex(ValueError,'checksum|chain'):self.head()

    def test_fault_before_commit_rolls_back_and_after_commit_remains_reserved(self):
        def fail(point):raise RuntimeError('synthetic '+point)
        before=self.head()
        with self.assertRaisesRegex(RuntimeError,'before_commit'):
            self.act(self.view(),action='reserve',fault=lambda point:fail(point) if point=='before_commit' else None)
        self.assertEqual(self.head(),before)
        with self.assertRaisesRegex(RuntimeError,'after_commit'):
            self.act(self.view(),action='reserve',fault=lambda point:fail(point) if point=='after_commit' else None)
        self.assertIsNotNone(self.head()['state']['reservation'])


if __name__=='__main__':unittest.main()
