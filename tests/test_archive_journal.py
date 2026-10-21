from contextlib import closing
from datetime import UTC,datetime,timedelta
import json,secrets,sqlite3,tempfile,unittest
from pathlib import Path

from hermes_trading import archive_journal as archive
from hermes_trading.control_auth import sign_command
from hermes_trading.reflection_supervision import _hash,_json

NOW=datetime(2026,10,16,tzinfo=UTC)
ACCOUNTS={f'{arm}_{wallet}' for arm in ('adaptive','control') for wallet in (50,100,150,200)}


class ArchiveJournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'journal.sqlite'
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript('''CREATE TABLE paired_identity(id INTEGER PRIMARY KEY,manifest_hash TEXT);
                CREATE TABLE paired_batches(batch_id TEXT PRIMARY KEY,status TEXT,payload TEXT,input_hash TEXT,completed TEXT);
                CREATE TABLE paired_settlements(batch_id TEXT PRIMARY KEY,payload TEXT);''')
            db.execute('INSERT INTO paired_identity VALUES(1,?)',('a'*64,))
            for i in range(1,4):self.append(db,i)
            db.commit()
        self.keys={name:secrets.token_hex(32) for name in ('pc','backup')}
        self.options=dict(enabled=True,experiment_id='synthetic-only',release_sha256='b'*64,
            archive_sha256='c'*64,account_snapshots={name:'d'*64 for name in ACCOUNTS},
            copies={'pc':'e'*64,'backup':'f'*64},through_sequence=2)

    def append(self,db,index):
        packet={'index':index};accounts={name:{'index':index} for name in ACCOUNTS}
        db.execute('INSERT INTO paired_batches VALUES(?,?,?,?,?)',
            (f'batch-{index}','complete',_json(packet),_hash(packet),_json(accounts)))
        db.execute('INSERT INTO paired_settlements VALUES(?,?)',(f'batch-{index}',_json(accounts)))

    def prepare(self,**overrides):
        return archive.prepare(self.path,**dict(self.options,**overrides))

    def receipts(self,request,at=NOW):
        body=_json(request)
        return {name:{'body':body,'nonce':request['operation_id'],'timestamp':at.isoformat(),
            'signature':sign_command(key,request['operation_id'],at,body)} for name,key in self.keys.items()}

    def acknowledge(self,request,receipts=None,now=NOW,**kwargs):
        return archive.acknowledge(self.path,request['operation_id'],
            self.receipts(request) if receipts is None else receipts,self.keys,now=now,enabled=True,**kwargs)

    def retire(self,request,**kwargs):
        return archive.retire(self.path,request['operation_id'],enabled=True,**kwargs)

    def test_disabled_api_cannot_prepare_acknowledge_or_retire(self):
        with self.assertRaisesRegex(ValueError,'disabled'):self.prepare(enabled=False)
        request=self.prepare()
        with self.assertRaisesRegex(ValueError,'disabled'):
            archive.acknowledge(self.path,request['operation_id'],self.receipts(request),self.keys,now=NOW)
        with self.assertRaisesRegex(ValueError,'disabled'):archive.retire(self.path,request['operation_id'])
        self.assertEqual(archive.lifetime(self.path)['complete'],3)

    def test_two_verified_receipts_retirement_and_lost_ack_are_durable(self):
        request=self.prepare();receipts=self.receipts(request)
        first=self.acknowledge(request,receipts)
        self.assertEqual(first,self.acknowledge(request,receipts,now=NOW+timedelta(days=1)))
        def lost_ack(point):
            if point=='after_commit':raise RuntimeError('lost acknowledgement')
        with self.assertRaisesRegex(RuntimeError,'lost acknowledgement'):self.retire(request,fault=lost_ack)
        state=self.retire(request)
        self.assertEqual(state['retired_complete'],2)
        self.assertEqual(archive.lifetime(self.path)['complete'],3)
        self.assertEqual(archive.lifetime(self.path)['active_complete'],1)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT rowid FROM paired_batches').fetchall(),[(3,)])
            self.append(db,4);db.commit()
        self.assertEqual(archive.lifetime(self.path)['complete'],4)
        self.assertEqual(self.retire(request),state)

    def test_freshly_signed_same_operation_never_reauthorizes(self):
        request=self.prepare();self.acknowledge(request)
        with self.assertRaisesRegex(ValueError,'receipt differs'):
            self.acknowledge(request,self.receipts(request,NOW+timedelta(days=1)),now=NOW+timedelta(days=1))
        self.assertEqual(archive.lifetime(self.path)['complete'],3)

    def test_wrong_binding_key_or_expiry_cannot_consume_operation(self):
        request=self.prepare()
        wrong=self.receipts(request);wrong['backup']['signature']='0'*64
        with self.assertRaisesRegex(ValueError,'authentication'):self.acknowledge(request,wrong)
        wrong=self.receipts(request);wrong['pc']['body']=wrong['pc']['body']+' '
        with self.assertRaisesRegex(ValueError,'binding'):self.acknowledge(request,wrong)
        for seconds in (300,-1):
            with self.assertRaisesRegex(ValueError,'validity'):
                self.acknowledge(request,now=NOW+timedelta(seconds=seconds))
        self.assertEqual(self.acknowledge(request)['status'],'acknowledged')

    def test_every_precommit_crash_rolls_back_receipt_and_retirement(self):
        request=self.prepare()
        def fail(point):raise RuntimeError(point)
        with self.assertRaisesRegex(RuntimeError,'before_ack_commit'):self.acknowledge(request,fault=fail)
        with self.assertRaisesRegex(ValueError,'acknowledged'):self.retire(request)
        self.acknowledge(request)
        for point in ('before_delete','after_delete','after_checkpoint'):
            def crash(current):
                if current==point:raise RuntimeError(point)
            with self.assertRaisesRegex(RuntimeError,point):self.retire(request,fault=crash)
            self.assertEqual(archive.lifetime(self.path)['active_complete'],3)
        self.assertEqual(self.retire(request)['retired_complete'],2)

    def test_pending_gap_and_modified_archived_prefix_are_rejected(self):
        request=self.prepare();self.acknowledge(request)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE paired_batches SET status='pending' WHERE rowid=3");db.commit()
        with self.assertRaisesRegex(ValueError,'pending'):self.retire(request)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE paired_batches SET status='complete' WHERE rowid=3")
            db.execute("UPDATE paired_settlements SET payload=? WHERE batch_id='batch-1'",(_json({name:{} for name in ACCOUNTS}),));db.commit()
        with self.assertRaisesRegex(ValueError,'prefix'):self.retire(request)

    def test_retirement_requires_all_eight_accounts_two_distinct_copy_proofs_and_kept_tail(self):
        bad=[{'account_snapshots':{}},{'copies':{'pc':'e'*64}},
            {'copies':{'pc':'e'*64,'backup':'e'*64}}, {'through_sequence':3},
            {'through_sequence':True},{'release_sha256':'invalid'}]
        for update in bad:
            with self.subTest(update=update),self.assertRaises(ValueError):self.prepare(**update)

    def test_checkpoint_corruption_gaps_and_trusted_highwater_regression_block_reads(self):
        request=self.prepare();self.acknowledge(request);self.retire(request)
        with self.assertRaisesRegex(ValueError,'regressed'):archive.lifetime(self.path,minimum_complete=4)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('UPDATE paired_batches SET rowid=4 WHERE rowid=3');db.commit()
        with self.assertRaisesRegex(ValueError,'sequence'):archive.lifetime(self.path)

    def test_two_successive_retirements_extend_chain_without_resetting_count(self):
        first=self.prepare();self.acknowledge(first);state=self.retire(first)
        with closing(sqlite3.connect(self.path)) as db:self.append(db,4);db.commit()
        second=self.prepare(through_sequence=3)
        self.assertEqual(second['previous_checkpoint_sha256'],_hash(state))
        self.acknowledge(second);next_state=self.retire(second)
        self.assertEqual(next_state['revision'],2)
        self.assertEqual(archive.lifetime(self.path)['complete'],4)
        self.assertEqual(self.retire(first),state)

    def test_lost_prepare_response_recovers_same_request_after_normal_head_growth(self):
        identity='1'*32
        request=self.prepare(operation_id=identity)
        with closing(sqlite3.connect(self.path)) as db:self.append(db,4);db.commit()
        self.assertEqual(self.prepare(operation_id=identity),request)
        with self.assertRaisesRegex(ValueError,'request differs'):
            self.prepare(operation_id=identity,archive_sha256='0'*64)
        self.acknowledge(request)
        self.assertEqual(self.retire(request)['observed_complete'],3)
        self.assertEqual(archive.lifetime(self.path)['complete'],4)

    def test_corrupted_predecessor_result_blocks_lifetime_and_later_retirement(self):
        first=self.prepare();self.acknowledge(first);self.retire(first)
        with closing(sqlite3.connect(self.path)) as db:self.append(db,4);db.commit()
        second=self.prepare(through_sequence=3);self.acknowledge(second);self.retire(second)
        with closing(sqlite3.connect(self.path)) as db:
            raw=db.execute('SELECT result FROM paired_archive_operations WHERE operation_id=?',(first['operation_id'],)).fetchone()[0]
            altered=json.loads(raw);altered['prefix_sha256']='0'*64
            db.execute('UPDATE paired_archive_operations SET result=? WHERE operation_id=?',(_json(altered),first['operation_id']));db.commit()
        with self.assertRaisesRegex(ValueError,'checksum|predecessor'):archive.lifetime(self.path)

    def test_dropped_checkpoint_table_does_not_erase_committed_retirement(self):
        request=self.prepare();self.acknowledge(request);self.retire(request)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('DROP TABLE paired_archive_checkpoint');db.execute('DELETE FROM paired_batches');db.commit()
        with self.assertRaisesRegex(ValueError,'checkpoint'):archive.lifetime(self.path)


if __name__=='__main__':unittest.main()
