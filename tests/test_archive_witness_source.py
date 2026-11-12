from contextlib import closing
from datetime import timedelta
from pathlib import Path
import json,secrets,shutil,sqlite3,tempfile,unittest

import test_paired_worker as fixtures
from hermes_trading import archive_journal as journal
from hermes_trading.archive_witness import initialize,read,apply,identity_for,source_view
from hermes_trading.paired_paper import run_paired_cycle,process_paper_account
from hermes_trading.control_auth import sign_command
from hermes_trading.reflection_supervision import _json


class NativeWitnessSourceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f=fixtures.WorkerTests();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.f.limits['max_cycles']=3;(self.f.root/'worker_limits.json').write_text(json.dumps(self.f.limits))
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'receiver.sqlite';self.identity=identity_for(self.f.root)
        self.binding=initialize(self.path,self.identity,source_root=self.f.root,enabled=True,now=self.f.at)

    def head(self):return read(self.path,self.binding,self.identity,enabled=True)

    def act(self,action='check'):
        head=self.head();view=source_view(self.f.root,self.identity,prior_complete=head['state']['point']['complete'],enabled=True)
        return apply(self.path,self.binding,self.identity,view,action=action,
            expected_head_sha256=head['head_sha256'],enabled=True,now=self.f.at)

    async def cycle(self):
        token=self.act('reserve')['state']['reservation']['reservation_id']
        await run_paired_cycle(self.f.root,self.f.market,now=self.f.at,reservation_id=token)
        self.act();self.f.at+=timedelta(seconds=60)

    async def test_real_eight_account_completed_prefix_and_full_source_rollback(self):
        old=Path(self.tmp.name)/'old-worker';shutil.copytree(self.f.root,old)
        await self.cycle()
        self.assertEqual(self.head()['state']['point']['complete'],1)
        self.assertEqual(self.f.market.collect.await_count,1)
        for profile in self.f.manifest['profiles']:
            self.assertEqual(len(self.f.positions(profile['directory'])),1)
        with self.assertRaisesRegex(ValueError,'regress|missing'):
            source_view(old,self.identity,prior_complete=1,enabled=True)

    async def test_pending_original_packet_replays_same_reservation_and_no_new_collection(self):
        token=self.act('reserve')['state']['reservation']['reservation_id']
        async def crash(*args,**kwargs):
            await process_paper_account(*args,**kwargs);raise RuntimeError('after original account')
        with self.assertRaisesRegex(RuntimeError,'original account'):
            await run_paired_cycle(self.f.root,self.f.market,now=self.f.at,process_account=crash,reservation_id=token)
        self.assertEqual(self.act('reserve')['state']['reservation']['reservation_id'],token)
        with self.assertRaisesRegex(ValueError,'reservation'):
            await run_paired_cycle(self.f.root,self.f.market,now=self.f.at)
        await run_paired_cycle(self.f.root,self.f.market,now=self.f.at,reservation_id=token)
        self.act()
        self.assertEqual(self.head()['state']['point']['complete'],1)
        self.assertEqual(self.f.market.collect.await_count,1)

    async def test_checkpoint_advance_retains_exact_lifetime_prefix_and_witness_floor(self):
        await self.cycle();await self.cycle();before=self.head()['state']['point']['prefix_sha256']
        path=self.f.primary/'paper_publications.sqlite'
        request=journal.prepare(path,enabled=True,experiment_id='paired-test',
            release_sha256=self.identity['release_sha256'],archive_sha256='b'*64,
            account_snapshots={name:'c'*64 for name in journal.ACCOUNTS},copies={'pc':'d'*64,'backup':'e'*64},through_sequence=1)
        keys={name:secrets.token_hex(32) for name in ('pc','backup')}
        receipts={name:{'body':_json(request),'nonce':request['operation_id'],'timestamp':self.f.at.isoformat(),
            'signature':sign_command(key,request['operation_id'],self.f.at,_json(request))} for name,key in keys.items()}
        journal.acknowledge(path,request['operation_id'],receipts,keys,enabled=True,now=self.f.at)
        journal.retire(path,request['operation_id'],enabled=True)
        state=self.act()['state']['point']
        self.assertEqual(state['complete'],2);self.assertEqual(state['archive_revision'],1)
        self.assertEqual(state['prefix_sha256'],before)
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM paired_batches').fetchone()[0],1)
        await self.cycle();self.assertEqual(self.head()['state']['point']['complete'],3)
        with self.assertRaisesRegex(ValueError,'cycle'):self.act('reserve')


if __name__=='__main__':unittest.main()
