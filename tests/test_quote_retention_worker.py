"""Native frozen-batch replay while an invented retention cohort is incomplete."""
from contextlib import closing
import json
import sqlite3
import unittest
from unittest.mock import patch

import test_storage_recovery as native
from hermes_trading import archive_quote_state
from hermes_trading.paired_paper import _liquidate_halted_account, process_paper_account, run_paired_cycle
from hermes_trading.paired_worker import _stats
from hermes_trading.reflection_supervision import _hash


class RetentionWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.recovery = native.StorageRecoveryTests()
        self.recovery.setUp()
        self.f = self.recovery.f
        self.addCleanup(self.f.tearDown)

    def pending_retention(self):
        identity = 'a' * 32
        plan = dict(schema_version=1, purpose=archive_quote_state.PURPOSE,
                    operation_id=identity, archive_operation_id='b' * 32,
                    archive_sha256='c' * 64, cohort_sha256=_hash(self.f.manifest),
                    cut_at=self.f.now.isoformat(), accounts={
                        p['directory']: dict(directory=p['directory'], items=[])
                        for p in self.f.manifest['profiles']})
        progress = dict(status='pending', accounts={
            name: dict(done=False, stage=None, orphans=[]) for name in plan['accounts']})
        with closing(sqlite3.connect(self.f.primary / 'paper_publications.sqlite')) as db:
            db.execute('CREATE TABLE paired_quote_retention(operation_id TEXT PRIMARY KEY,plan TEXT NOT NULL,progress TEXT NOT NULL)')
            db.execute('INSERT INTO paired_quote_retention VALUES(?,?,?)',
                       (identity, archive_quote_state.envelope(plan), archive_quote_state.envelope(progress)))
            db.commit()

    def lifecycle(self):
        with closing(sqlite3.connect(self.f.root / 'worker_runtime.sqlite')) as db:
            return json.loads(db.execute('SELECT state FROM lifecycle WHERE id=1').fetchone()[0])

    async def test_pending_protective_packet_replays_without_new_collection_or_reset(self):
        def crash(*args, **kwargs):
            _liquidate_halted_account(*args, **kwargs)
            raise RuntimeError('invented interruption after protective account commit')

        with patch('hermes_trading.paired_paper._liquidate_halted_account', side_effect=crash):
            with self.assertRaisesRegex(RuntimeError, 'protective account commit'):
                await self.recovery.invoke()
        self.assertTrue(_stats(self.f.primary)['pending'])
        initial = self.lifecycle()
        self.pending_retention()
        result = await self.recovery.invoke()

        self.assertFalse(_stats(self.f.primary)['pending'])
        self.assertEqual(result['completed_batches'], 2)
        self.assertEqual(result['deadline'], initial['deadline'])
        self.assertEqual(result['limits'], initial['limits'])
        self.assertEqual(self.f.market.collect.await_count, 2)
        self.recovery.assert_closed_once()
        again = await self.recovery.invoke()
        self.assertEqual(again['completed_batches'], 2)
        self.assertEqual(again['deadline'], initial['deadline'])
        self.assertEqual(self.f.market.collect.await_count, 2)

    async def test_ordinary_pending_packet_stays_blocked_without_collecting_or_advancing(self):
        async def crash(*args, **kwargs):
            await process_paper_account(*args, **kwargs)
            raise RuntimeError('invented interruption after ordinary account commit')

        async def cycle(*args, **kwargs):
            return await run_paired_cycle(*args, **kwargs, process_account=crash)

        with patch('hermes_trading.paired_worker.run_paired_cycle', side_effect=cycle):
            with self.assertRaisesRegex(RuntimeError, 'ordinary account commit'):
                await self.recovery.invoke()
        initial = self.lifecycle()
        self.pending_retention()
        result = await self.recovery.invoke()

        self.assertEqual(result['status'], 'waiting_for_retention')
        self.assertTrue(_stats(self.f.primary)['pending'])
        self.assertEqual(result['completed_batches'], 0)
        self.assertEqual(result['deadline'], initial['deadline'])
        self.assertEqual(result['limits'], initial['limits'])
        self.assertEqual(self.f.market.collect.await_count, 1)


if __name__ == '__main__':
    unittest.main()
