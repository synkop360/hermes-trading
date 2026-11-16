"""Replay of the 13 November 2026 paper incident, on synthetic state only (LAT-119).

Four scenarios, each driven by a controlled clock, disk and backup
(``recette_incident_fixtures``) over the eight synthetic paired accounts:

S1  thirty days of normal growth        -> contract for LAT-120 (ticket 5)
S2  volume under its reserve            -> guards + contract for LAT-120
S3  three failed daily backups          -> guards (latch source, no early unlock)
S4  restart while the worker is latched -> guards + contract for LAT-121 (ticket 6)

Each assertion is a ``subTest`` labelled ``garde`` (current behaviour that must
never regress) or ``contrat-5`` / ``contrat-6`` (behaviour that tickets 5/6
must deliver; red on master and on integration until they are merged). No
assertion is skipped or disabled: this module is not merged while red.

Contract for ticket 5 (LAT-120), as checked here
-------------------------------------------------
* Retention runs inside the worker: at a cycle with ``now``, once that day's
  daily backup is verified, detail older than ``now - 30 days`` is archived
  then removed; a row exactly at ``now - 30 days`` is kept. Checked on the
  primary ``paper_publications.sqlite`` ``paired_batches`` (by decoded packet
  ``created_at``) and on each account's ``wallet_equity.sqlite``
  ``observations``. The lifetime batch counter (and so ``max_cycles``)
  survives retention.
* No deletion when that day's backup failed (S1 guard).
* ``hermes_trading.paper_history_retention.daily_summaries(root)`` returns
  ``{account_directory: [{'date': 'YYYY-MM-DD', 'balance': str, 'decisions':
  int, 'costs': str, ...}, ...]}`` with one row per UTC day of activity,
  archived days included; amounts are decimal strings.
* ``heartbeat['storage_projection']`` = ``{'schema_version': 1, 'status',
  'bytes_per_day', 'days_before_reserve'}``; status is one of
  ``projected`` / ``non_positive_growth`` / ``insufficient_data`` /
  ``reserve_reached``. When projected, ``days_before_reserve ==
  max(0, min(free_bytes - reserve_bytes, max_state_bytes - reserve_bytes -
  state_bytes)) // bytes_per_day`` with the figures of the same heartbeat's
  storage report. Under the reserve: ``reserve_reached`` and 0 days.

Contract for ticket 6 (LAT-121), as checked here
-------------------------------------------------
* The worker lifts its own error latch, in paper mode only, through
  ``hermes_trading.paper_safety.recover_worker``, at the end of the third
  consecutive successful cycle (N = 3) after the latch when all of these hold:
  that day's daily backup succeeded, storage is ready without storage stop,
  the eight accounts are flat. A failed cycle restarts the count.
* Each lift leaves ``<root>/paper_worker_recovery/<32 hex>.intent.json`` and
  ``.receipt.json`` (``outcome == 'completed'``); a restart after the receipt
  writes nothing new and keeps the latch lifted.
* Without a precondition (open position, failed backup of the day, storage
  stop) the latch stays, whatever the number of successful cycles.

Run: ``PYTHONPATH=$PWD/tests uv run python -m unittest test_recette_incident_replay``
"""
from __future__ import annotations

from contextlib import closing
from datetime import timedelta
from decimal import Decimal
import importlib
import json
import sqlite3

from recette_incident_fixtures import (ACCOUNTS, MAX_STATE_BYTES, RESERVE_BYTES, FakeDisk,
                                       FlakyBackup, IncidentWorld, own_tests)
from hermes_trading.paper_safety import record_worker_result

RETENTION = timedelta(days=30)
RECOVERY_SUCCESSES = 3
AMPLE = 10 * 1024 ** 3


class SimulatedCrash(Exception):
    """Raised from the fake sleep: the worker process dies between cycles."""


class IncidentReplay(IncidentWorld):

    # --- helpers --------------------------------------------------------
    def equity_times(self, account):
        with closing(sqlite3.connect(self.root / account / 'wallet_equity.sqlite')) as db:
            return sorted(row[0] for row in db.execute('SELECT at FROM observations'))

    def batch_times(self):
        from hermes_trading.paired_packet_storage import load_packet
        with closing(sqlite3.connect(self.primary / 'paper_publications.sqlite')) as db:
            return sorted(load_packet(db, row[0])['created_at']
                          for row in db.execute('SELECT payload FROM paired_batches'))

    def recovery_files(self, suffix):
        directory = self.root / 'paper_worker_recovery'
        return sorted(directory.glob('*.' + suffix + '.json')) if directory.exists() else []

    def latch(self):
        """Three daily-backup failures, as on 13/11 at 02:45 UTC (synthetic times)."""
        for index in range(1, 4):
            record_worker_result(self.root, success=False, error_kind='daily_backup_failed',
                                 now=self.at + timedelta(milliseconds=index))
        self.at += timedelta(seconds=60)
        self.refresh_market()
        self.supervisor_ok()

    async def flat_then_crash(self):
        """Run 1: open on all eight accounts, close at the stop, then crash."""
        steps = []

        async def sleep(seconds):
            steps.append(True)
            if len(steps) == 2:
                raise SimulatedCrash()
            self.at += timedelta(seconds=seconds)
            self.refresh_market(bid='77', ask='78')
            self.supervisor_ok()
        with self.assertRaises(SimulatedCrash):
            await self.invoke(sleep=sleep)
        self.assertEqual({name: len(rows) for name, rows in self.open_positions().items()},
                         dict.fromkeys(ACCOUNTS, 0), 'run 1 must leave the eight accounts flat')

    async def latched_run(self, cycles, *, failures=(), backup=None):
        """Restart; observe each cycle; crash after ``cycles`` cycles."""
        seen = []

        async def sleep(seconds):
            worker = self.safety_state()['worker']
            seen.append(dict(halt=worker['entry_halt'], errors=worker['consecutive_errors'],
                             reasons=list(self.heartbeat()['entry_admission']['reasons']),
                             open=sum(map(len, self.open_positions().values())),
                             receipts=len(self.recovery_files('receipt'))))
            if len(seen) == cycles:
                raise SimulatedCrash()
            self.at += timedelta(seconds=seconds)
            self.refresh_market()
            self.supervisor_ok()
            if len(seen) + 1 in failures:
                self.market.collect.side_effect = RuntimeError('synthetic public quote outage')
        if backup is None:
            with self.assertRaises(SimulatedCrash):
                await self.invoke(sleep=sleep)
        else:
            with backup.patch(), self.assertRaises(SimulatedCrash):
                await self.invoke(sleep=sleep)
        return seen

    def prepare(self, **limits):
        self.enable_safety()
        self.enable_backup()
        self.write_limits(**dict(dict(max_cycles=40, max_runtime_seconds=60 * 86400), **limits))

    # --- S1: thirty days of normal growth -------------------------------
    async def test_s1_thirty_day_growth_keeps_thirty_days_of_detail_and_projection(self):
        days = 33
        self.prepare(max_cycles=days)
        cycles = []

        async def next_day(seconds):
            cycles.append(self.at)
            self.at += timedelta(days=1)
            self.refresh_market()
            self.supervisor_ok()
        with FakeDisk(AMPLE).patch():
            result = await self.invoke(sleep=next_day)
        # The fake sleep also follows the last cycle: ``cycles`` holds each cycle time.
        last = cycles[-1]
        cutoff = last - RETENTION
        with self.subTest('garde: one cycle per day, bound and lifetime counter survive retention'):
            self.assertEqual(result['stop_reason'], 'cycle_bound')
            self.assertEqual(result['completed_batches'], days)
            self.assertEqual(len(cycles), days)
        expected = [at.isoformat() for at in cycles if at >= cutoff]
        self.assertEqual(len(expected), 31, 'synthetic window: day -30 inclusive to today')
        for account in ACCOUNTS:
            with self.subTest('contrat-5: equity detail limited to 30 days', account=account):
                self.assertEqual(self.equity_times(account), expected)
        with self.subTest('contrat-5: publication detail limited to 30 days'):
            kept = self.batch_times()
            self.assertEqual(len(kept), 31)
            self.assertTrue(all(_after(at, cutoff) for at in kept), kept[:2])
        with self.subTest('contrat-5: permanent daily summary for each of the eight accounts'):
            summaries = _summaries(self.root)
            dates = {at.date().isoformat() for at in cycles}
            self.assertEqual(set(summaries), set(ACCOUNTS))
            for account, rows in summaries.items():
                self.assertEqual([row['date'] for row in rows], sorted(dates), account)
                for row in rows:
                    self.assertIsInstance(row['balance'], str)
                    self.assertIsInstance(row['costs'], str)
                    Decimal(row['balance']), Decimal(row['costs'])
                    self.assertIs(type(row['decisions']), int)
        with self.subTest('contrat-5: heartbeat growth projection after thirty days'):
            heartbeat = self.heartbeat()
            projection = heartbeat.get('storage_projection')
            self.assertIsInstance(projection, dict, 'heartbeat has no storage_projection')
            self.assertEqual(projection['schema_version'], 1)
            self.assertIn(projection['status'], {'projected', 'non_positive_growth'})
            if projection['status'] == 'projected':
                report = heartbeat['worker_lifecycle']['storage_report']
                headroom = max(0, min(report['free_bytes'] - RESERVE_BYTES,
                                      MAX_STATE_BYTES - RESERVE_BYTES - report['state_bytes']))
                self.assertIs(type(projection['bytes_per_day']), int)
                self.assertGreater(projection['bytes_per_day'], 0)
                self.assertEqual(projection['days_before_reserve'], headroom // projection['bytes_per_day'])

    async def test_s1_failed_backup_of_the_day_refuses_any_deletion(self):
        self.prepare(max_cycles=2)
        start = self.at
        backup = FlakyBackup(fail=lambda index, now: now >= start + timedelta(days=31))

        cycles = []

        async def jump(seconds):
            cycles.append(self.at)
            self.at += timedelta(days=31)
            self.refresh_market()
            self.supervisor_ok()
        with FakeDisk(AMPLE).patch(), backup.patch():
            result = await self.invoke(sleep=jump)
        self.assertEqual(result['completed_batches'], 2)
        self.assertEqual(cycles, [start, start + timedelta(days=31)])
        self.assertEqual(len(backup.calls), 2, 'the day-31 backup was attempted and failed')
        for account in ACCOUNTS:
            with self.subTest('garde: unverified archive keeps the older detail', account=account):
                self.assertEqual(self.equity_times(account), [at.isoformat() for at in cycles])
        with self.subTest('garde: unverified archive keeps the older publication'):
            self.assertEqual(len(self.batch_times()), 2)

    # --- S2: volume under its reserve ----------------------------------
    async def test_s2_volume_under_reserve_halts_entries_and_reports_zero_days(self):
        self.prepare()
        disk = FakeDisk(AMPLE)
        beats = []

        async def fill(seconds):
            beats.append(self.heartbeat())
            if len(beats) == 3:
                (self.root / 'paper_stop.json').write_text(json.dumps({'experiment_id': 'paired-test'}))
            self.at += timedelta(seconds=seconds)
            self.refresh_market()
            self.supervisor_ok()
            disk.free_bytes = RESERVE_BYTES - 1
        with disk.patch():
            result = await self.invoke(sleep=fill)
        ready, halted = beats[0], beats[1:]
        opened = {name: len(self.positions(name)) for name in ACCOUNTS}
        with self.subTest('garde: reserve crossing latches storage stop with its reason'):
            self.assertEqual(result['stop_reason'], 'operator_stop')
            report = self.heartbeat()['worker_lifecycle']['storage_report']
            self.assertEqual((report['status'], report['reason']), ('blocked', 'filesystem_reserve'))
            marker = json.loads((self.primary / 'experiment_halt.json').read_text())
            self.assertEqual(marker['reason'], 'storage_stop')
        with self.subTest('garde: monitor stays alive, entries blocked, no new collection'):
            self.assertEqual(self.market.collect.await_count, 1)
            for beat in halted:
                self.assertEqual(beat['worker_lifecycle']['status'], 'running')
                self.assertEqual((beat['status'], beat['error']), ('degraded', 'storage_entry_halt'))
                self.assertTrue(beat['entry_admission']['blocked'])
                self.assertIn('storage_stop', beat['entry_admission']['reasons'])
            self.assertLess(_parse(halted[0]['timestamp']), _parse(halted[-1]['timestamp']))
            self.assertEqual(opened, {name: len(self.positions(name)) for name in ACCOUNTS})
        with self.subTest('contrat-5: projection present while storage is ready'):
            projection = ready.get('storage_projection')
            self.assertIsInstance(projection, dict, 'heartbeat has no storage_projection')
            self.assertIn(projection['status'], {'projected', 'non_positive_growth', 'insufficient_data'})
        with self.subTest('contrat-5: projection reports the reserve reached, zero days'):
            projection = halted[-1].get('storage_projection')
            self.assertIsInstance(projection, dict, 'heartbeat has no storage_projection')
            self.assertEqual((projection['status'], projection['days_before_reserve']), ('reserve_reached', 0))

    # --- S3: three failed daily backups --------------------------------
    async def test_s3_three_failed_backups_latch_worker_without_premature_unlock(self):
        self.prepare()
        backup = FlakyBackup(failures=3)
        seen = []

        async def monitor(seconds):
            worker = self.safety_state()['worker']
            beat = self.heartbeat()
            seen.append(dict(errors=worker['consecutive_errors'], halt=worker['entry_halt'],
                             reasons=list(beat['entry_admission']['reasons']),
                             backup=beat['worker_lifecycle'].get('daily_backup')))
            if len(seen) == 5:
                (self.root / 'paper_stop.json').write_text(json.dumps({'experiment_id': 'paired-test'}))
            self.at += timedelta(seconds=seconds)
            self.refresh_market()
            self.supervisor_ok()
        with FakeDisk(RESERVE_BYTES - 1).patch(), backup.patch():
            result = await self.invoke(sleep=monitor)
        with self.subTest('garde: storage halt retries the daily backup at each monitor pass'):
            self.assertEqual(result['stop_reason'], 'operator_stop')
            self.assertEqual(len(backup.calls), 4)
            self.assertEqual(self.market.collect.await_count, 0)
        with self.subTest('garde: latch exactly at the third consecutive failure'):
            self.assertEqual([row['errors'] for row in seen[:4]], [1, 2, 3, 3])
            self.assertEqual([row['halt'] for row in seen[:4]], [False, False, True, True])
            self.assertNotIn('worker_error_halt', seen[1]['reasons'])
            self.assertIn('worker_error_halt', seen[2]['reasons'])
        with self.subTest('garde: successful backup clears its own reason, never the latch'):
            for row in seen[3:]:
                self.assertNotIn('daily_backup_failed', row['reasons'])
                self.assertEqual(row['backup']['date'], self.at.date().isoformat())
                self.assertTrue(row['halt'])
                self.assertIn('worker_error_halt', row['reasons'])
                self.assertIn('storage_stop', row['reasons'])
            self.assertEqual(self.recovery_files('receipt'), [])
        with self.subTest('garde: code-only diagnostics, no exception text'):
            events = [row['event'] for row in self.diagnostics()]
            self.assertEqual(events.count('daily_backup_failed'), 3)
            self.assertNotIn('synthetic no space', (self.root / 'paper_runtime.log').read_text())

    # --- S4: restart while the worker is latched -----------------------
    async def test_s4_restart_with_latch_lifts_after_three_successes_with_receipt(self):
        self.prepare()
        await self.flat_then_crash()
        self.latch()
        seen = await self.latched_run(5)
        with self.subTest('garde: restart keeps the latch and blocks entries'):
            for row in seen[:RECOVERY_SUCCESSES - 1]:
                self.assertTrue(row['halt'])
                self.assertIn('worker_error_halt', row['reasons'])
                self.assertEqual((row['open'], row['receipts']), (0, 0))
        with self.subTest('contrat-6: lifted at the end of the third successful cycle'):
            row = seen[RECOVERY_SUCCESSES - 1]
            self.assertFalse(row['halt'])
            self.assertEqual(row['errors'], 0)
            self.assertNotIn('worker_error_halt', row['reasons'])
        with self.subTest('contrat-6: one durable intent and receipt'):
            intents, receipts = self.recovery_files('intent'), self.recovery_files('receipt')
            self.assertEqual(len(intents), 1)
            self.assertEqual([path.name.split('.')[0] for path in receipts], [intents[0].name.split('.')[0]])
            self.assertEqual(json.loads(receipts[0].read_text())['outcome'], 'completed')
        with self.subTest('contrat-6: entries resume after the audited lift'):
            self.assertGreater(seen[-1]['open'], 0)
        before = {path.name: path.read_bytes() for path in self.recovery_files('intent') + self.recovery_files('receipt')}
        self.at += timedelta(seconds=60)
        self.refresh_market()
        self.supervisor_ok()
        again = await self.latched_run(1)
        with self.subTest('contrat-6: restart after the receipt is idempotent'):
            after = {path.name: path.read_bytes() for path in self.recovery_files('intent') + self.recovery_files('receipt')}
            self.assertEqual(after, before)
            self.assertTrue(before)
            self.assertFalse(again[0]['halt'])

    async def test_s4_interrupted_series_restarts_the_count(self):
        self.prepare()
        await self.flat_then_crash()
        self.latch()
        seen = await self.latched_run(6, failures={3})
        with self.subTest('garde: success, success, failure, success, success keeps the latch'):
            self.assertEqual([row['halt'] for row in seen[:5]], [True] * 5)
            self.assertEqual([row['receipts'] for row in seen[:5]], [0] * 5)
            self.assertEqual(sum(row['open'] for row in seen[:5]), 0)
        with self.subTest('contrat-6: third success after the failure lifts the latch'):
            self.assertFalse(seen[5]['halt'])
            self.assertEqual(seen[5]['receipts'], 1)

    async def test_s4_open_position_keeps_latch_after_restart(self):
        self.prepare()
        steps = []

        async def crash(seconds):
            steps.append(True)
            raise SimulatedCrash()
        with self.assertRaises(SimulatedCrash):
            await self.invoke(sleep=crash)
        self.latch()
        seen = await self.latched_run(4)
        with self.subTest('garde: accounts not flat, latch stays after four successes'):
            self.assertEqual([row['halt'] for row in seen], [True] * 4)
            self.assertEqual([row['open'] for row in seen], [len(ACCOUNTS)] * 4)
            self.assertEqual(self.recovery_files('intent') + self.recovery_files('receipt'), [])

    async def test_s4_failed_backup_of_the_day_keeps_latch_after_restart(self):
        self.prepare()
        await self.flat_then_crash()
        self.at += timedelta(days=1)
        self.latch()
        backup = FlakyBackup(failures=10 ** 6)
        seen = await self.latched_run(4, backup=backup)
        with self.subTest('garde: no backup of the day, latch stays after four cycles'):
            self.assertGreaterEqual(len(backup.calls), 4)
            self.assertEqual([row['halt'] for row in seen], [True] * 4)
            self.assertTrue(all('daily_backup_failed' in row['reasons'] for row in seen))
            self.assertEqual(self.recovery_files('intent') + self.recovery_files('receipt'), [])


def _parse(value):
    from datetime import datetime
    return datetime.fromisoformat(value)


def _after(value, cutoff):
    return _parse(value) >= cutoff


def _summaries(root):
    import importlib.util
    if importlib.util.find_spec('hermes_trading.paper_history_retention') is None:
        raise AssertionError('hermes_trading.paper_history_retention is not implemented (LAT-120)')
    module = importlib.import_module('hermes_trading.paper_history_retention')
    return module.daily_summaries(root)


load_tests = own_tests(globals())
