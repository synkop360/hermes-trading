"""Synthetic notification isolation against actual native paired settlement."""
import asyncio
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import inspect
import json
import sqlite3
import unittest
import hashlib
import yaml
from unittest.mock import AsyncMock, patch

import test_paired_paper as fixtures
from hermes_trading import paired_paper, paired_worker, paper_boot, notifications
from hermes_trading.paired_packet_storage import load_packet
from hermes_trading.reflection_supervision import _hash


class NotificationCaptureTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.PairedPaperTests.setUp
    tearDown = fixtures.PairedPaperTests.tearDown
    positions = fixtures.PairedPaperTests.positions

    async def cycle(self, **kwargs):
        self.assertIn('notification_capture', inspect.signature(paired_paper.run_paired_cycle).parameters)
        return await paired_paper.run_paired_cycle(self.root, self.market, now=self.now,
                                                  notification_capture=True, **kwargs)

    async def test_final_observations_and_orders_are_ephemeral_and_bound_to_all_eight_accounts(self):
        result = await self.cycle()
        capture = result['notification']
        self.assertEqual(capture['batch_id'], result['batch_id'])
        self.assertEqual(capture['input_hash'], result['input_hash'])
        self.assertEqual(set(capture['accounts']), {p['directory'] for p in self.manifest['profiles']})
        for name, row in capture['accounts'].items():
            account = json.loads((self.root / name / 'wallet_account.json').read_text())
            self.assertEqual(row['account_hash'], _hash(account))
            self.assertTrue(row['finalized'])
            self.assertEqual(row['observed_at'], capture['observed_at'])
            self.assertEqual(row['observation']['account_config_hash'], row['account_hash'])
            self.assertEqual(row['event_source_status'], 'observed')
            self.assertEqual(len(row['orders']), 1)
        path = self.root / 'adaptive_100/paper_publications.sqlite'
        with closing(sqlite3.connect(path)) as db:
            raw = db.execute('SELECT completed FROM paired_batches WHERE batch_id=?', (result['batch_id'],)).fetchone()[0]
            stored = load_packet(db, raw)
        for row in stored.values():
            self.assertNotIn('observation', row)
            self.assertNotIn('closes', row)
            self.assertNotIn('orders', row)
            self.assertNotIn('notification', row)

    async def test_missing_cost_remains_unknown_in_final_capture(self):
        path = self.root / 'adaptive_50/wallet_costs.json'
        cost = json.loads(path.read_text())
        cost['totals']['ai'].update(amount=None, quality='missing')
        path.write_text(json.dumps(cost))
        capture = (await self.cycle())['notification']['accounts']['adaptive_50']
        self.assertIsNone(capture['observation']['net_equity'])
        self.assertIn('cost:ai', capture['observation']['missing'])

    async def test_notification_source_failure_cannot_undo_settlement(self):
        self.assertTrue(hasattr(paired_paper, '_notification_events'))
        with patch.object(paired_paper, '_notification_events', side_effect=OSError('synthetic read failure')):
            result = await self.cycle()
        self.assertEqual(result['status'], 'complete')
        self.assertTrue(all(row['finalized'] for row in result['accounts']))
        self.assertTrue(all(row['event_source_status'] == 'unavailable'
                            for row in result['notification']['accounts'].values()))
        self.assertTrue(all(row['observation']['net_equity'] is not None
                            for row in result['notification']['accounts'].values()))

    async def test_native_final_risk_sweep_closes_are_captured_with_eur_pnl(self):
        await self.cycle()
        self.now += timedelta(seconds=1)
        for profile in self.manifest['profiles']:
            path = self.root / profile['directory'] / 'wallet_costs.json'
            costs = json.loads(path.read_text()); costs['as_of'] = self.now.isoformat()
            if profile['directory'] == 'control_100':
                costs['totals']['hosting']['amount'] = '4'
            path.write_text(json.dumps(costs))
        result = await self.cycle()
        self.assertEqual(result['status'], 'halted')
        for name, row in result['notification']['accounts'].items():
            self.assertTrue(row['halted'])
            self.assertEqual(len(row['closes']), 1)
            native = self.positions(name)[0]
            event = row['closes'][0]
            self.assertEqual(event['position_id'], native.position_id)
            self.assertEqual(event['closed_at'], self.now.isoformat())
            self.assertEqual(Decimal(event['realized_pnl_eur']), native.realized_pnl)
            self.assertEqual(Decimal(event['quantity']), native.quantity)

    async def test_partial_leg_is_reported_once_with_its_native_size_and_remainder_open(self):
        for profile in self.manifest['profiles']:
            path = self.root / profile['directory'] / 'strategy.yaml'
            strategy = yaml.safe_load(path.read_text())
            strategy['exit'] = dict(indicator='rsi', threshold='50', trailing_atr='1.5', partial_close_pct='0.5')
            path.write_text(yaml.safe_dump(strategy))
            profile['strategy_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        (self.root / 'manifest.json').write_text(json.dumps(self.manifest))
        await self.cycle()
        original = {p['directory']: self.positions(p['directory'])[0] for p in self.manifest['profiles']}
        self.now += timedelta(hours=1)
        from venue_fixtures import venue_record
        candle = replace(self.snapshot.candle, open_timestamp=self.snapshot.candle.close_timestamp,
                         close_timestamp=self.now, open=Decimal('94'), high=Decimal('94'),
                         low=Decimal('94'), close=Decimal('94'))
        self.market.collect.return_value = {'ETH/EUR': replace(self.snapshot, candle=candle,
            price=dict(self.snapshot.price, closes=tuple(Decimal(n) for n in range(80,95)),
                       bid='94', ask='94', received_at=self.now.isoformat()),
            venue_rules=venue_record('ETH/EUR', self.now))}
        for profile in self.manifest['profiles']:
            path = self.root / profile['directory'] / 'wallet_costs.json'
            cost = json.loads(path.read_text()); cost['as_of'] = self.now.isoformat(); path.write_text(json.dumps(cost))
        result = await self.cycle()
        for name, row in result['notification']['accounts'].items():
            positions = self.positions(name)
            closed = [p for p in positions if p.status.value == 'closed']
            active = [p for p in positions if p.status.value == 'open']
            self.assertEqual(len(closed), 1); self.assertEqual(len(active), 1)
            self.assertEqual(len(row['closes']), 1)
            self.assertEqual(row['closes'][0]['position_id'], closed[0].position_id)
            self.assertEqual(Decimal(row['closes'][0]['quantity']), original[name].quantity / 2)
            self.assertEqual(Decimal(row['closes'][0]['realized_pnl_eur']), closed[0].realized_pnl)
            self.assertGreater(closed[0].realized_pnl, 0)

    async def test_pending_batch_recovery_uses_original_event_timestamp_without_duplicate_orders(self):
        native = paired_paper.process_paper_account
        async def crash(*args, **kwargs):
            await native(*args, **kwargs)
            raise RuntimeError('synthetic crash after account commit')
        with self.assertRaises(RuntimeError):await self.cycle(process_account=crash)
        original = self.now; self.now += timedelta(seconds=3)
        result = await self.cycle()
        self.assertEqual(result['notification']['observed_at'], original.isoformat())
        self.assertEqual(self.market.collect.await_count, 1)
        for row in result['notification']['accounts'].values():
            self.assertEqual(len(row['orders']), 1)


class NotificationBootTests(unittest.IsolatedAsyncioTestCase):
    def ready_fixture(self):
        from test_paper_boot import PaperBootTests
        self.module = lambda: paper_boot
        return PaperBootTests.ready_fixture(self)

    async def test_background_reporter_runs_but_does_not_start_a_legacy_writer(self):
        module, fixture = self.ready_fixture(); offers = []; started = asyncio.Event(); cancelled = []
        class Reporter:
            enabled = True
            def __init__(self, root, identity, settings):
                self.identity = identity
            def offer(self, value): offers.append(value)
            async def run(self):
                started.set()
                try: await asyncio.Event().wait()
                finally: cancelled.append(True)
        async def worker(root, *, observer=None):
            self.assertIsNotNone(observer)
            observer({'kind': 'operational'})
            await asyncio.wait_for(started.wait(), 1)
            return {'status': 'synthetic'}
        env = dict(HERMES_TRADING_MODE='paper', HERMES_TRADING_ALLOW_REAL_ORDERS='false',
                   HERMES_NOTIFY_ENABLED='true', DISCORD_WEBHOOK_URL='https://example.invalid/fake')
        with patch.object(notifications, 'PaperDiscordReporter', Reporter, create=True), \
                patch.object(module, 'run_worker', side_effect=worker):
            result = await module.run_paper(fixture.root, env=env)
        self.assertEqual(result['status'], 'synthetic')
        self.assertEqual(len(offers), 1); self.assertTrue(cancelled)

    async def test_bad_notification_configuration_leaves_original_worker_dispatch_unchanged(self):
        module, fixture = self.ready_fixture()
        env = dict(HERMES_TRADING_MODE='paper', HERMES_TRADING_ALLOW_REAL_ORDERS='false',
                   HERMES_NOTIFY_ENABLED='true', HERMES_NOTIFY_MODE='invalid')
        with patch.object(module, 'run_worker', new_callable=AsyncMock, return_value={'status': 'synthetic'}) as worker:
            result = await module.run_paper(fixture.root, env=env)
        self.assertEqual(result['status'], 'synthetic')
        worker.assert_awaited_once_with(fixture.root.resolve())

    async def test_failed_background_sender_does_not_replace_worker_result(self):
        module, fixture = self.ready_fixture()
        class Reporter:
            enabled = True
            def __init__(self, *args):pass
            def offer(self, value):pass
            async def run(self):raise OSError('synthetic sender failure')
        async def worker(root, **kwargs):
            await asyncio.sleep(0); await asyncio.sleep(0)
            return {'status': 'worker-retained'}
        env = dict(HERMES_TRADING_MODE='paper', HERMES_TRADING_ALLOW_REAL_ORDERS='false',
                   HERMES_NOTIFY_ENABLED='true', DISCORD_WEBHOOK_URL='https://example.invalid/fake')
        with patch.object(notifications, 'PaperDiscordReporter', Reporter), patch.object(module, 'run_worker', side_effect=worker):
            self.assertEqual((await module.run_paper(fixture.root, env=env))['status'], 'worker-retained')


class NotificationWorkerIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_observer_exception_does_not_become_worker_failure(self):
        from test_paired_worker import WorkerTests
        self.assertIn('observer', inspect.signature(paired_worker.run_worker).parameters)
        fixture = WorkerTests(); fixture.setUp(); self.addCleanup(fixture.tearDown)
        calls = []
        def broken(envelope):
            calls.append(envelope)
            raise RuntimeError('synthetic observer failure')
        result = await fixture.invoke(observer=broken)
        self.assertEqual(result['completed_batches'], 1)
        settled = [item for item in calls if item['kind'] == 'settled']
        self.assertEqual(len(settled), 1)
        self.assertEqual(len(settled[0]['accounts']), 8)

    async def test_real_sender_stall_allows_next_batch_to_protect_positions_and_retains_opening(self):
        from test_paper_boot import PaperBootTests
        from test_paper_notifications import settings, eventually
        from hermes_trading.paired_inception import initialize_paired_inception, read_paired_inception
        from hermes_trading.paper_safety import record_supervisor_result
        self.module = lambda: paper_boot
        _, fixture = PaperBootTests.ready_fixture(self)
        root = fixture.root; native_fixture = fixture.fixture
        limits = json.loads((root / 'worker_limits.json').read_text()); limits['max_cycles'] = 2
        (root / 'worker_limits.json').write_text(json.dumps(limits))
        opening = initialize_paired_inception(root)
        record_supervisor_result(root.resolve(), success=True, event_id='synthetic', now=datetime.now(timezone.utc))
        identity = dict(experiment_id='paired-test', opening_at=opening['at'], opening_hash=_hash(opening),
            manifest_hash=opening['manifest_hash'], primary_wallet_eur=200,
            account_hashes={name: row['account_config_hash'] for name,row in opening['accounts'].items()})
        blocked = asyncio.Event(); release = asyncio.Event(); sent = []; offers = []
        async def poster(url, payload):
            sent.append(payload); blocked.set(); await release.wait()
        reporter = notifications.PaperDiscordReporter(root, identity, settings(), post_json=poster)
        task = asyncio.create_task(reporter.run())
        self.assertTrue(reporter.enabled)
        def offer(envelope):offers.append(envelope); reporter.offer(envelope)
        sleeps = []
        async def step(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 1:
                await asyncio.wait_for(blocked.wait(), 3)
                access = root / 'adaptive_200/model_access.json'; access.write_text('null')
                snapshot = replace(native_fixture.snapshot, price=dict(native_fixture.snapshot.price,
                    bid='77', ask='78', received_at=datetime.now(timezone.utc).isoformat()))
                native_fixture.market.collect.return_value = {'ETH/EUR': snapshot}
            await asyncio.sleep(0)
        try:
            result = await paired_worker.run_worker(root, collector_factory=lambda:native_fixture.market,
                                                   sleep=step, observer=offer)
            self.assertEqual(result['completed_batches'], 2)
            self.assertFalse(release.is_set())
            self.assertEqual(len([e for e in offers if e['kind'] == 'settled']), 2)
            settled = [e for e in offers if e['kind'] == 'settled']
            self.assertTrue(all(len(row['closes']) == 1 for row in settled[-1]['accounts'].values()))
            self.assertTrue(all(native_fixture.positions(name)[0].status.value == 'closed' for name in opening['accounts']))
            self.assertEqual(read_paired_inception(root), opening)
            self.assertEqual(reporter.counters['invalid'], 0)
            self.assertEqual(reporter._snapshot['batch_id'], settled[0]['batch_id'])
            parts = reporter._parts('synthetic-native-shape', reporter._summary(['synthetic shape']))
            rendered = '\n'.join(content for _,content in parts)
            self.assertLessEqual(len(parts), 4)
            self.assertTrue(all(len(content) <= 1900 for _,content in parts))
            for name in opening['accounts']:self.assertIn(name + ':', rendered)
            for size in (50,100,150,200):self.assertIn('adaptive minus control '+str(size)+':', rendered)
            release.set()
            await eventually(lambda: any('Close adaptive_200' in p['content'] for p in sent), timeout=3)
        finally:
            task.cancel()
            try:await asyncio.wait_for(task, 1)
            except asyncio.CancelledError:pass


if __name__ == '__main__': unittest.main()
