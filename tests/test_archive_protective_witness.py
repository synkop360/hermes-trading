"""Disabled version2 receiver accounting; invented verified-source views only."""
from copy import deepcopy
from contextlib import closing
from datetime import timedelta
import json,sqlite3,tempfile,unittest
from pathlib import Path

import test_paired_worker as fixtures
from hermes_trading import archive_witness as original
from hermes_trading import archive_protective_witness as witness
from hermes_trading.reflection_supervision import _hash,_json,_time


class ProtectiveWitnessTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.WorkerTests();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.f.limits['max_cycles']=4;(self.f.root/'worker_limits.json').write_text(json.dumps(self.f.limits))
        self.identity=original.identity_for(self.f.root);self.now=self.f.now
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/'protective.sqlite'
        self.binding=witness.initialize(self.path,self.identity,source_root=self.f.root,enabled=True,now=self.now,max_bytes=1024*1024)
        self.operations=0

    def head(self):return witness.read(self.path,self.binding,self.identity,enabled=True,max_bytes=1024*1024)

    def view(self,head,*,suffix=(),pending=None):
        point=deepcopy(head['state']['point'])
        if suffix:point.update(complete=suffix[-1]['sequence'],prefix_sha256=suffix[-1]['prefix_sha256'])
        return {'identity_sha256':_hash(self.identity),'point':point,
            'prior_prefix_sha256':head['state']['point']['prefix_sha256'],'suffix':list(suffix),'pending':pending}

    def completion(self,head,kind,*,sequence=None,previous=None,credit=None,reservation=None):
        state=head['state'];grant=credit or state['credit'];reservation=reservation or state['reservation']
        sequence=sequence or state['point']['complete']+1
        previous=previous or state['point']['prefix_sha256']
        token=grant['credit_id'] if kind=='protective' else reservation['reservation_id']
        record={'sequence':sequence,'batch_id':f'batch-{sequence}','status':'complete',
            'input_hash':_hash(['input',sequence]),'payload_sha256':_hash(['payload',sequence]),
            'completed_sha256':_hash(['completed',sequence]),'settlement_sha256':_hash(['settled',sequence])}
        return {'sequence':sequence,'kind':kind,'reservation_id':token,'input_hash':_hash(['input',sequence]),
            'created_at':self.now.isoformat(),'credit_id':grant['credit_id'] if kind=='protective' else None,
            'credit_generation':grant['generation'] if kind=='protective' else None,
            'previous_prefix_sha256':previous,'prefix_sha256':_hash({'previous':previous,'record':record}),'record':record}

    def apply(self,head,view=None,*,action='check',operation=None,**kwargs):
        self.operations+=1
        return witness.apply(self.path,self.binding,self.identity,view or self.view(head),action=action,
            expected_head_sha256=head['head_sha256'],operation_id=operation or f'{self.operations:032x}',
            enabled=True,now=self.now,max_bytes=1024*1024,**kwargs)

    def test_distinct_disabled_protocol_does_not_accept_version1_binding(self):
        with self.assertRaises(ValueError):witness.read(self.path,self.binding,self.identity)
        with self.assertRaises(ValueError):witness.read(self.path,{'witness_id':'a'*32,'genesis_sha256':'b'*64},self.identity,enabled=True)
        head=self.head();self.assertEqual(head['schema_version'],2)
        self.assertEqual(head['state']['credit']['generation'],1)
        self.assertEqual(head['state']['credit']['deadline'],self.identity['deadline'])

    def test_normal_admission_leaves_last_original_slot_for_protection(self):
        head=self.head()
        for _ in range(3):
            head=self.apply(head,action='reserve')
            head=self.apply(head,self.view(head,suffix=[self.completion(head,'ordinary')]))
        with self.assertRaisesRegex(ValueError,'protective|capacity|slot'):self.apply(head,action='reserve')
        head=self.apply(head,self.view(head,suffix=[self.completion(head,'protective')]))
        self.assertEqual(head['state']['point']['complete'],4)
        self.assertIsNone(head['state']['credit'])
        with self.assertRaises(ValueError):self.apply(head,action='reserve')

    def test_lost_normal_ack_and_protective_suffix_reconcile_once(self):
        head=self.apply(self.head(),action='reserve');normal=self.completion(head,'ordinary')
        protective=self.completion(head,'protective',sequence=2,previous=normal['prefix_sha256'])
        view=self.view(head,suffix=[normal,protective]);after=self.apply(head,view,operation='a'*32)
        self.assertEqual(after['state']['point']['complete'],2);self.assertIsNone(after['state']['reservation'])
        self.assertIsNone(after['state']['credit'])
        self.assertEqual(self.apply(head,view,operation='a'*32),after)

    def test_valid_original_record_proof_drives_prefix_and_tampering_is_rejected(self):
        head=self.apply(self.head(),action='reserve');event=self.completion(head,'ordinary')
        for corrupted in (dict(event,prefix_sha256='f'*64),
                dict(event,record=dict(event['record'],input_hash='f'*64))):
            with self.assertRaises(ValueError):self.apply(head,self.view(head,suffix=[corrupted]))
        self.assertEqual(self.apply(head,self.view(head,suffix=[event]))['state']['point']['complete'],1)

    def test_unreserved_normal_arbitrary_jump_and_wrong_prefix_are_rejected(self):
        head=self.head()
        unreserved=self.completion(head,'ordinary',reservation={'reservation_id':'e'*32})
        with self.assertRaisesRegex(ValueError,'reservation'):
            self.apply(head,self.view(head,suffix=[unreserved]))
        reserved=self.apply(head,action='reserve')
        event=self.completion(reserved,'ordinary')
        for bad_head,bad_view in ((reserved,dict(self.view(reserved),point=dict(reserved['state']['point'],complete=3))),
                (reserved,self.view(reserved,suffix=[dict(event,previous_prefix_sha256='f'*64)]))):
            with self.assertRaises(ValueError):self.apply(bad_head,bad_view)

    def test_unfrozen_normal_reservation_cannot_be_borrowed_or_cancelled_implicitly(self):
        head=self.apply(self.head(),action='reserve')
        with self.assertRaisesRegex(ValueError,'unresolved|original|reservation'):self.apply(head)
        with self.assertRaises(ValueError):self.apply(head,self.view(head,suffix=[self.completion(head,'protective')]))
        self.assertEqual(self.head(),head)

    def test_frozen_normal_packet_cannot_change_disappear_or_be_cancelled(self):
        head=self.apply(self.head(),action='reserve');event=self.completion(head,'ordinary')
        pending={key:value for key,value in event.items() if key not in {'previous_prefix_sha256','prefix_sha256','record'}}
        frozen=self.apply(head,self.view(head,pending=pending))
        for view in (self.view(frozen),self.view(frozen,pending=dict(pending,input_hash='f'*64))):
            with self.assertRaises(ValueError):self.apply(frozen,view)
        with self.assertRaises(ValueError):self.apply(frozen,self.view(frozen),action='cancel',active_token=pending['reservation_id'])
        self.assertEqual(self.apply(frozen,self.view(frozen,suffix=[event]))['state']['point']['complete'],1)

    def test_partial_protective_packet_keeps_original_hash_and_credit(self):
        head=self.head();event=self.completion(head,'protective')
        pending={key:value for key,value in event.items() if key not in {'previous_prefix_sha256','prefix_sha256','record'}}
        frozen=self.apply(head,self.view(head,pending=pending))
        with self.assertRaises(ValueError):self.apply(frozen,self.view(frozen))
        with self.assertRaises(ValueError):self.apply(frozen,self.view(frozen,suffix=[dict(event,input_hash='f'*64)]))
        done=self.apply(frozen,self.view(frozen,suffix=[event]));self.assertIsNone(done['state']['credit'])
        self.assertIsNone(done['state']['pending_protection'])

    def test_consumed_credit_is_not_reset_and_renewal_has_new_generation(self):
        head=self.head();old=deepcopy(head['state']['credit'])
        head=self.apply(head,self.view(head,suffix=[self.completion(head,'protective')]))
        head=self.apply(head,action='reserve');self.assertEqual(head['state']['credit']['generation'],2)
        head=self.apply(head,self.view(head,suffix=[self.completion(head,'ordinary')]))
        with self.assertRaises(ValueError):self.apply(head,self.view(head,suffix=[self.completion(head,'protective',credit=old)]))
        self.assertEqual(self.head(),head)

    def test_expired_or_foreign_credit_and_boolean_sequence_are_rejected(self):
        head=self.head();event=self.completion(head,'protective')
        for bad in (dict(event,created_at=self.identity['deadline']),dict(event,credit_id='f'*32),
                    dict(event,credit_generation=True),dict(event,sequence=True)):
            with self.assertRaises(ValueError):self.apply(head,self.view(head,suffix=[bad]))
        self.assertEqual(self.head(),head)

    def test_commit_fault_rolls_back_and_lost_reply_exact_retry_does_not_append(self):
        head=self.head();view=self.view(head)
        def before(point):
            if point=='before_commit':raise OSError('synthetic full disk')
        with self.assertRaises(OSError):self.apply(head,view,action='reserve',fault=before)
        self.assertEqual(self.head(),head)
        def after(point):
            if point=='after_commit':raise OSError('synthetic lost reply')
        with self.assertRaises(OSError):self.apply(head,view,action='reserve',operation='b'*32,fault=after)
        committed=self.head()
        self.assertEqual(self.apply(head,view,action='reserve',operation='b'*32),committed)

    def test_old_chain_mutation_and_receiver_budget_fail_closed(self):
        head=self.apply(self.head(),action='reserve')
        head=self.apply(head,self.view(head,suffix=[self.completion(head,'ordinary')]))
        with self.assertRaises((ValueError,sqlite3.Error)):
            witness.read(self.path,self.binding,self.identity,enabled=True,max_bytes=1)
        with self.assertRaises((ValueError,TimeoutError)):
            witness.read(self.path,self.binding,self.identity,enabled=True,timeout_seconds=0)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE protective_events SET sha256=? WHERE sequence=1",('f'*64,))
            db.commit()
        with self.assertRaises(ValueError):self.head()

    def test_late_acknowledgement_does_not_authorize_a_new_post_deadline_collection(self):
        head=self.head();event=self.completion(head,'protective')
        self.now=_time(self.identity['deadline'])+timedelta(seconds=1)
        done=self.apply(head,self.view(head,suffix=[event]))
        self.assertEqual(done['state']['point']['complete'],1)
        with self.assertRaisesRegex(ValueError,'deadline'):self.apply(done,action='reserve')

    def test_single_slot_identity_cannot_initialize_normal_plus_protective_mode(self):
        with self.assertRaisesRegex(ValueError,'two|slot'):
            witness.initialize(self.path.with_name('single.sqlite'),dict(self.identity,max_cycles=1),
                source_root=self.f.root,enabled=True,now=self.now)
        self.assertFalse(self.path.with_name('single.sqlite').exists())

    def test_self_checksummed_malformed_historical_request_is_rejected_as_invalid_evidence(self):
        self.apply(self.head(),action='reserve')
        with closing(sqlite3.connect(self.path)) as db:
            event=json.loads(db.execute('SELECT payload FROM protective_events WHERE sequence=1').fetchone()[0])
            event['request']=[]
            db.execute('UPDATE protective_events SET payload=?,sha256=? WHERE sequence=1',(_json(event),_hash(event)))
            db.commit()
        with self.assertRaises(ValueError):self.head()


if __name__=='__main__':unittest.main()
