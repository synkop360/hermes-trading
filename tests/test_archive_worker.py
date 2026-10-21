from contextlib import closing
from datetime import timedelta
import json,secrets,sqlite3,unittest
import tempfile
from pathlib import Path

import test_paired_worker as fixtures
from hermes_trading import archive_journal as archive
from hermes_trading.control_auth import sign_command
from hermes_trading.paired_worker import _stats
from hermes_trading.reflection_supervision import _json
from hermes_trading.archive_snapshot import snapshot


class ArchiveWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f=fixtures.WorkerTests('test_bound_survives_restart_and_no_new_collection');self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.f.limits['max_cycles']=3
        (self.f.root/'worker_limits.json').write_text(json.dumps(self.f.limits))

    async def pause(self):
        async def pause_after_two(seconds):
            await self.f.sleep(seconds)
            if self.f.market.collect.await_count==2:raise RuntimeError('paused before third collection')
        with self.assertRaisesRegex(RuntimeError,'paused before third'):
            await self.f.invoke(sleep=pause_after_two)

    def retire_first(self,through=1):
        path=self.f.primary/'paper_publications.sqlite'
        request=archive.prepare(path,enabled=True,experiment_id='paired-test',release_sha256='a'*64,
            archive_sha256='b'*64,account_snapshots={name:'c'*64 for name in archive.ACCOUNTS},
            copies={'pc':'d'*64,'backup':'e'*64},through_sequence=through)
        keys={name:secrets.token_hex(32) for name in ('pc','backup')};at=self.f.at
        receipts={name:{'body':_json(request),'nonce':request['operation_id'],'timestamp':at.isoformat(),
            'signature':sign_command(key,request['operation_id'],at,_json(request))} for name,key in keys.items()}
        archive.acknowledge(path,request['operation_id'],receipts,keys,enabled=True,now=at)
        return archive.retire(path,request['operation_id'],enabled=True)

    async def test_native_worker_uses_lifetime_bound_after_retirement_and_restart(self):
        await self.pause();self.retire_first()
        stats=_stats(self.f.primary)
        self.assertEqual(stats['complete'],2)
        result=await self.f.invoke()
        self.assertEqual(result['completed_batches'],3)
        self.assertEqual(result['stop_reason'],'cycle_bound')
        self.assertEqual(self.f.market.collect.await_count,3)
        again=await self.f.invoke()
        self.assertEqual(again['completed_batches'],3)
        self.assertEqual(self.f.market.collect.await_count,3)

    async def test_removed_uncheckpointed_history_cannot_reset_lifecycle(self):
        await self.pause()
        with closing(sqlite3.connect(self.f.primary/'paper_publications.sqlite')) as db:
            db.execute('DELETE FROM paired_batches WHERE rowid=1');db.commit()
        with self.assertRaisesRegex(ValueError,'regressed'):
            await self.f.invoke()
        self.assertEqual(self.f.market.collect.await_count,2)

    async def test_corrupt_checkpoint_blocks_before_new_collection(self):
        await self.pause();self.retire_first()
        with closing(sqlite3.connect(self.f.primary/'paper_publications.sqlite')) as db:
            db.execute("UPDATE paired_archive_checkpoint SET sha256=?",('0'*64,));db.commit()
        with self.assertRaisesRegex(ValueError,'checkpoint'):
            await self.f.invoke()
        self.assertEqual(self.f.market.collect.await_count,2)

    async def test_repeated_native_retirements_preserve_original_four_cycle_limit(self):
        self.f.limits['max_cycles']=4
        (self.f.root/'worker_limits.json').write_text(json.dumps(self.f.limits))
        await self.pause();self.retire_first()
        async def pause_third(seconds):
            await self.f.sleep(seconds)
            if self.f.market.collect.await_count==3:raise RuntimeError('paused after third')
        with self.assertRaisesRegex(RuntimeError,'paused after third'):
            await self.f.invoke(sleep=pause_third)
        self.retire_first(through=2)
        result=await self.f.invoke()
        self.assertEqual(result['completed_batches'],4)
        self.assertEqual(result['archive_revision'],2)
        self.assertEqual(result['limits']['max_cycles'],4)
        await self.f.invoke()
        self.assertEqual(self.f.market.collect.await_count,4)

    async def test_snapshot_contains_exact_logical_state_for_all_eight_accounts(self):
        await self.pause()
        with tempfile.TemporaryDirectory() as name:
            target=Path(name)/'eight-accounts'
            result=snapshot(self.f.root,target,enabled=True,max_bytes=128*1024*1024,timeout_seconds=30)
            self.assertGreater(result['files'],30)
            for profile in self.f.manifest['profiles']:
                directory=profile['directory']
                with closing(sqlite3.connect(self.f.root/directory/'trading.db')) as source,closing(sqlite3.connect(target/directory/'trading.db')) as copied:
                    tables=source.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
                    for (table,) in tables:
                        # Table names are schema identifiers from the real fixture, not user text.
                        self.assertEqual(source.execute('SELECT * FROM "'+table+'" ORDER BY rowid').fetchall(),
                            copied.execute('SELECT * FROM "'+table+'" ORDER BY rowid').fetchall(),(directory,table))
            self.assertEqual(_stats(target/'adaptive_100')['complete'],2)


if __name__=='__main__':unittest.main()
