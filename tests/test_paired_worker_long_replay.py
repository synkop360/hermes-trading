"""Accelerated CI replay of the real paired worker beyond 30 days (LAT-142).

CI evidence only, never operational recette evidence: the first real
observation of retention and of the audited worker recovery in production is
reserved to Thomas. Nothing here reads production state, a secret, a model,
Discord, Kraken or the network.

The real ``hermes_trading.paired_worker.run_worker`` runs end to end on the
eight synthetic paired accounts of ``recette_incident_fixtures`` (LAT-119):
real paired cycles, real daily backup (``paper_backup``), real 30-day
retention (``paper_history_retention``, LAT-120) and real audited latch
recovery (``paper_safety.recover_worker``, LAT-121). Only the world around the
worker is simulated:

* a fixed logical clock (``T0`` = 2026-10-01T00:00Z) moved by the fake
  ``sleep``: one cycle per logical day, then 60 s cycles around the latch;
* ``FakeDisk`` for free space, ``FlakyBackup`` (synthetic ``ENOSPC``) for the
  daily backup, a public-quote outage on the fake market;
* two fault injections that keep the real code path: a corrupted gzip write
  (the real verification must refuse it) and a tampered member of today's
  backup (the real re-verification must refuse it);
* every root lives in ``<repo>/.test-tmp-lat142-replay-*`` and is removed.

Timeline A (39 logical days, one root, three worker processes):
  days 0-30   one cycle per day, no retention yet (cutoff = day -30 kept);
  day 31      first retention: day 0 archived, gzip verified before deletion,
              restored byte for byte against the pre-retention detail;
  day 32      corrupted archive write: validation fails, nothing deleted;
  day 33      daily backup fails: retention not even attempted;
  day 34      catch-up: days 1-3 archived in one verified archive per table;
  day 35      three failed backups in a row: worker latch at the third, crash;
  day 36      restart: latch kept; refusals by precondition through the real
              worker (accounts not flat, tampered backup, mode not paper), a
              failed cycle resets the series; lift at the end of the third
              consecutive successful cycle with every precondition met;
              crash;
  day 37-38   restart: no new intent/receipt, latch stays lifted, retention
              continues.
Timeline B (33 logical days, one root): 31 normal days with retention, then
  the volume falls under its reserve and three backups fail: storage stop and
  worker latch; restart; the latch is never lifted while storage is not ready.

Deterministic outputs: each timeline returns a transcript of logical facts
(day index, cycle, counts, codes, SHA-256 of archived wallet content on the
fixed clock); the transcript digests are compared with the constants below.
Publication archives are proven byte for byte within each run but their
digest is per run (uuid4 batch id, release instance id and pid). Set
``LAT142_REPLAY_TRANSCRIPT=<file>`` to write both transcripts and the redacted
receipts (paths and request ids removed) as JSON.

Run: ``PYTHONPATH="$PWD/tests:$PWD" uv run python -m unittest test_paired_worker_long_replay``
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from unittest.mock import patch

import test_paired_paper
from recette_incident_fixtures import (ACCOUNTS, RESERVE_BYTES, FakeDisk, FlakyBackup,
                                       IncidentWorld, own_tests)
from hermes_trading import paper_history_retention as retention
from hermes_trading.paired_packet_storage import load_packet
from hermes_trading.paper_safety import WORKER_RECOVERY_SUCCESSES

REPO = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
DAY = timedelta(days=1)
AMPLE = 10 * 1024 ** 3
RETENTION = timedelta(days=retention.RETENTION_DAYS)
MAX_TRANSCRIPT_ROWS = 80


class SimulatedCrash(Exception):
    """Raised from the fake sleep: the worker process dies between cycles."""


class _FixedDatetime(datetime):
    """``datetime.now`` for the synthetic fixture only: the logical T0."""

    @classmethod
    def now(cls, tz=None):
        return T0 + timedelta(seconds=2)


def _canonical(rows):
    """Independent canonical JSON lines (sorted keys, compact), as archived."""
    return b''.join(json.dumps(row, sort_keys=True, separators=(',', ':'), allow_nan=False).encode() + b'\n'
                    for row in rows)


class _NoDatabase:
    """Stand-in for a database that does not exist yet."""

    def __bool__(self):
        return False

    def close(self):
        pass


def _read_only(path, table):
    """Read-only connection (never creates the file), ``None`` before the table exists."""
    if not path.is_file():
        return _NoDatabase()
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is None:
        db.close()
        return _NoDatabase()
    return db


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


class LongReplay(IncidentWorld):

    # --- world -----------------------------------------------------------
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix='.test-tmp-lat142-replay-', dir=REPO))
        self.addCleanup(shutil.rmtree, self.base, True)
        with patch.object(tempfile, 'tempdir', str(self.base)), \
                patch.object(test_paired_paper, 'datetime', _FixedDatetime):
            super().setUp()
        self.assertEqual(self.now, T0)
        self.assertTrue(self.root.resolve().is_relative_to(self.base.resolve()))
        self.assertTrue(self.daily.is_relative_to(self.base.resolve()))
        disk = FakeDisk(AMPLE)
        patcher = disk.patch()
        patcher.start()
        self.addCleanup(patcher.stop)
        self.disk = disk
        self.enable_safety()
        self.enable_backup()
        self.write_limits(max_cycles=1000, max_runtime_seconds=60 * 86400)
        self.snapshot_rows = {}          # (table, key) -> decoded row, as last seen in detail
        self.proofs = []                 # one entry per verified archive
        self.verifications = []          # (archive name, detail rows present at verification)
        self.seen_receipts = set()
        self.corrupt_next_archive = False
        self.timeline = []

    def day(self):
        return (self.at - T0) // DAY

    def next_day(self, *, bid='80', ask='80'):
        self.at = T0 + (self.day() + 1) * DAY
        self.refresh_market(bid=bid, ask=ask)
        self.supervisor_ok()

    def next_minute(self, *, bid='80', ask='80'):
        self.at += timedelta(seconds=60)
        self.refresh_market(bid=bid, ask=ask)
        self.supervisor_ok()

    # --- observation of the real files ------------------------------------
    def wallet_rows(self, account):
        with closing(_read_only(self.root / account / 'wallet_equity.sqlite', 'observations')) as db:
            if not db:
                return []
            rows = db.execute('SELECT at, payload FROM observations ORDER BY at').fetchall()
            return [dict(load_packet(db, payload), at=at) for at, payload in rows]

    def batch_rows(self):
        with closing(_read_only(self.primary / 'paper_publications.sqlite', 'paired_batches')) as db:
            if not db:
                return []
            rows = db.execute('SELECT batch_id, status, payload, input_hash, completed, rowid '
                              'FROM paired_batches ORDER BY rowid').fetchall()
            return [dict(batch_id=batch_id, status=status, packet=load_packet(db, payload),
                         input_hash=input_hash,
                         completed_packet=load_packet(db, completed) if completed else None, rowid=rowid)
                    for batch_id, status, payload, input_hash, completed, rowid in rows]

    def detail_days(self, account):
        return sorted({(datetime.fromisoformat(row['at']) - T0) // DAY for row in self.wallet_rows(account)})

    def remember_detail(self):
        """Snapshot the detail before the next cycle (the next retention input)."""
        for account in ACCOUNTS:
            for row in self.wallet_rows(account):
                self.snapshot_rows[('wallet_equity:' + account, row['at'])] = row
        for row in self.batch_rows():
            self.snapshot_rows[('paper_publications', row['rowid'])] = row

    def archive_dir(self):
        return self.root / retention.HISTORY_DIR

    def prove_new_archives(self):
        """Every new receipt: receipt, gzip and restored bytes vs. the detail snapshot."""
        directory = self.archive_dir()
        if not directory.exists():
            return []
        found = []
        for receipt_path in sorted(directory.glob('*.receipt.json')):
            if receipt_path.name in self.seen_receipts:
                continue
            self.seen_receipts.add(receipt_path.name)
            receipt = json.loads(receipt_path.read_text())
            archive = receipt_path.with_name(receipt_path.name[:-len('.receipt.json')] + '.gz')
            raw = archive.read_bytes()
            restored = retention.restore_wallet_archive(archive)
            kind = receipt['kind']
            archived = [json.loads(line) for line in restored.splitlines()]
            key = (lambda row: (kind, row['at'])) if kind.startswith('wallet_equity:') else \
                (lambda row: (kind, row['rowid']))
            expected = _canonical([self.snapshot_rows[key(row)] for row in archived])
            cutoff = datetime.fromisoformat(receipt['archived_at']) - RETENTION
            with self.subTest('archive receipt verified', archive=archive.name):
                self.assertEqual((receipt['schema_version'], receipt['purpose']), (1, retention.PURPOSE))
                self.assertEqual(receipt['archive_sha256'], _sha(raw))
                self.assertEqual(gzip.decompress(raw), restored)
                self.assertEqual(receipt['original_sha256'], _sha(restored))
                self.assertEqual((receipt['row_count'], receipt['original_bytes']), (len(archived), len(restored)))
            with self.subTest('restored content identical byte for byte', archive=archive.name):
                self.assertEqual(restored, expected)
            with self.subTest('archive holds only detail older than the 30-day cutoff', archive=archive.name):
                stamps = [datetime.fromisoformat(row['at'] if 'at' in row else row['packet']['created_at'])
                          for row in archived]
                self.assertTrue(all(stamp < cutoff for stamp in stamps))
            found.append(dict(kind=kind, rows=len(archived), original_sha256=receipt['original_sha256'],
                              keys=[key(row) for row in archived],
                              days=sorted({(stamp - T0) // DAY for stamp in stamps})))
        self.proofs.extend(found)
        return found

    def receipt_view(self, path):
        """Redacted receipt: request id and root-bound hashes removed."""
        body = json.loads(path.read_text())
        return {key: ('<redacted>' if key in {'request_id', 'request_hash', 'root_hash', 'intent_hash'} else value)
                for key, value in body.items()}

    def recovery_files(self, suffix):
        directory = self.root / 'paper_worker_recovery'
        return sorted(directory.glob('*.' + suffix + '.json')) if directory.exists() else []

    def observe(self, label):
        """One transcript row of logical, deterministic facts after a cycle."""
        beat = self.heartbeat()
        lifecycle = beat['worker_lifecycle']
        worker = self.safety_state()['worker']
        recovery = lifecycle.get('worker_recovery') or {}
        archives = self.prove_new_archives()
        row = dict(
            label=label, day=self.day(),
            batches=lifecycle['completed_batches'],
            backup='ok' if (lifecycle.get('daily_backup') or {}).get('date') == self.at.date().isoformat()
            and not lifecycle.get('daily_backup_error') else 'failed',
            retention_error=lifecycle.get('history_retention_error'),
            archived=[[proof['kind'], proof['days'], _stable_digest(proof)] for proof in archives],
            oldest_detail_day=min(self.detail_days(ACCOUNTS[0]), default=None),
            detail_days=len(self.detail_days(ACCOUNTS[0])),
            halt=worker['entry_halt'], errors=worker['consecutive_errors'],
            reasons=sorted(beat['entry_admission']['reasons']),
            recovery=[recovery.get('status'), recovery.get('reason'), recovery.get('successes')],
            open=sum(len(rows) for rows in self.open_positions().values()),
            intents=len(self.recovery_files('intent')), receipts=len(self.recovery_files('receipt')),
            storage=lifecycle.get('storage_report', {}).get('status'),
            projection=(beat.get('storage_projection') or {}).get('status'))
        self.timeline.append(row)
        if len(self.timeline) > MAX_TRANSCRIPT_ROWS:
            raise AssertionError('replay exceeded its logical bound of cycles')
        return row

    # --- fault injections that keep the real code path --------------------
    def install_archive_spies(self):
        real_gzip, real_verify = retention._gzip_bytes, retention._verify_gzip_archive

        def gzip_bytes(data, path, fault):
            real_gzip(data, path, fault)
            if self.corrupt_next_archive:
                self.corrupt_next_archive = False
                body = bytearray(path.read_bytes())
                body[len(body) // 2] ^= 0xFF
                path.write_bytes(bytes(body))

        def verify(archive_path, original_bytes, original_sha256):
            # Detail rows still present when the real verification runs.
            name = Path(archive_path).name
            if name.startswith('paper_publications'):
                present = len(self.batch_rows())
            else:
                present = len(self.wallet_rows(name.split('_wallet_equity')[0]))
            self.verifications.append((name, present))
            return real_verify(archive_path, original_bytes, original_sha256)
        for name, value in (('_gzip_bytes', gzip_bytes), ('_verify_gzip_archive', verify)):
            patcher = patch.object(retention, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def todays_backup_member(self):
        record = self.heartbeat()['worker_lifecycle']['daily_backup']
        members = sorted(path for path in Path(record['path']).rglob('wallet_equity.sqlite'))
        return members[0]

    # --- timeline A --------------------------------------------------------
    async def daily_phase(self, last_day, *, corrupt_day, failed_backup_day):
        """Days 0..last_day, one cycle per day, retention proofs at each cycle."""
        backup = FlakyBackup(fail=lambda index, now: (now - T0) // DAY == failed_backup_day)
        summaries_before = {}

        async def sleep(seconds):
            row = self.observe('daily')
            if row['day'] == retention.RETENTION_DAYS:
                summaries_before.update(retention.daily_summaries(self.root))
            if row['day'] == last_day:
                raise SimulatedCrash()
            self.remember_detail()
            self.corrupt_next_archive = row['day'] + 1 == corrupt_day
            self.next_day()
        self.remember_detail()
        with backup.patch(), self.assertRaises(SimulatedCrash):
            await self.invoke(sleep=sleep)
        return summaries_before

    async def latch_phase(self):
        """Day 35: three failed backups between successful cycles, then three outages latch."""
        self.next_day()
        steps = ['backup_failure'] * 3 + ['backup_recovered'] + ['outage'] * 3
        backup = FlakyBackup(failures=3)
        index = {'cycle': 0}

        async def sleep(seconds):
            label = steps[index['cycle']]
            self.observe(label)
            index['cycle'] += 1
            if index['cycle'] == len(steps):
                raise SimulatedCrash()
            self.next_minute()
            if steps[index['cycle']] == 'outage':
                self.market.collect.side_effect = RuntimeError('synthetic public quote outage')
        with backup.patch(), self.assertRaises(SimulatedCrash):
            await self.invoke(sleep=sleep)

    async def recovery_phase(self):
        """Day 36 restart: refusals by precondition, interrupted series, audited lift."""
        self.next_day()
        steps = ['open_1', 'open_2', 'open_3', 'series_break', 'stop_exit', 'flat_2', 'tampered_backup',
                 'mode_not_paper', 'lift', 'entries_resume']
        index = {'cycle': 0}
        tamper = {}
        mode = patch.dict(os.environ, {'HERMES_TRADING_MODE': 'backtest'})

        async def sleep(seconds):
            label = steps[index['cycle']]
            if label == 'mode_not_paper':
                mode.stop()
            row = self.observe(label)
            if label == 'tampered_backup':
                tamper['path'].write_bytes(tamper['raw'])
            index['cycle'] += 1
            if index['cycle'] == len(steps):
                raise SimulatedCrash()
            upcoming = steps[index['cycle']]
            self.next_minute(**(dict(bid='77', ask='78') if upcoming == 'stop_exit' else {}))
            if upcoming == 'series_break':
                self.market.collect.side_effect = RuntimeError('synthetic public quote outage')
            if upcoming == 'tampered_backup':
                member = self.todays_backup_member()
                tamper.update(path=member, raw=member.read_bytes())
                member.write_bytes(tamper['raw'] + b'synthetic tamper')
            if upcoming == 'mode_not_paper':
                mode.start()
            return row
        with self.assertRaises(SimulatedCrash):
            await self.invoke(sleep=sleep)

    async def idempotent_restart_phase(self, last_day):
        """Day 37..last_day: restart after the receipt, retention continues."""
        self.next_day()

        async def sleep(seconds):
            row = self.observe('after_receipt')
            if row['day'] == last_day:
                raise SimulatedCrash()
            self.remember_detail()
            self.next_day()
        self.remember_detail()
        with self.assertRaises(SimulatedCrash):
            await self.invoke(sleep=sleep)

    async def test_timeline_a_retention_and_audited_recovery_over_39_days(self):
        self.install_archive_spies()
        summaries_before = await self.daily_phase(34, corrupt_day=32, failed_backup_day=33)
        await self.latch_phase()
        await self.recovery_phase()
        audit_before = {path.name: path.read_bytes()
                        for path in self.recovery_files('intent') + self.recovery_files('receipt')}
        await self.idempotent_restart_phase(38)
        by_label = {}
        for row in self.timeline:
            by_label.setdefault(row['label'], []).append(row)
        daily = {row['day']: row for row in by_label['daily']}

        with self.subTest('logical bounds: 39 days, one worker per run, fake disk only'):
            self.assertEqual(self.timeline[-1]['day'], 38)
            self.assertGreater(self.timeline[-1]['day'], retention.RETENTION_DAYS)
            self.assertEqual(sorted(daily), list(range(35)))
            self.assertGreater(self.disk.calls, 0)
        with self.subTest('retention boundary: day -30 kept, nothing archived before day 31'):
            for day in range(31):
                self.assertEqual((daily[day]['archived'], daily[day]['oldest_detail_day']), ([], 0), day)
            self.assertEqual(daily[30]['detail_days'], 31)
        with self.subTest('day 31: day 0 archived for the eight accounts and publications'):
            kinds = sorted(kind for kind, days, digest in daily[31]['archived'])
            self.assertEqual(kinds, sorted(['paper_publications'] + ['wallet_equity:' + name for name in ACCOUNTS]))
            self.assertTrue(all(days == [0] for kind, days, digest in daily[31]['archived']))
            self.assertEqual((daily[31]['oldest_detail_day'], daily[31]['detail_days']), (1, 31))
        with self.subTest('gzip verified before deletion (detail still present at verification)'):
            first = [present for name, present in self.verifications if name.startswith(ACCOUNTS[0] + '_')]
            self.assertEqual(first[0], 32, 'day 31 verification saw the 32 detail rows')
        with self.subTest('day 32: corrupted archive refused, nothing deleted'):
            self.assertEqual(daily[32]['retention_error'], 'ValueError')
            self.assertEqual((daily[32]['archived'], daily[32]['oldest_detail_day']), ([], 1))
            self.assertIn('history_retention_failed', [row['event'] for row in self.diagnostics()])
            orphan = [path.name for path in self.archive_dir().glob('*.gz')
                      if not path.with_name(path.name[:-3] + '.receipt.json').exists()]
            self.assertEqual(len(orphan), 1, 'the corrupted archive keeps no receipt')
        with self.subTest('day 33: failed backup of the day, retention not attempted'):
            self.assertEqual(daily[33]['backup'], 'failed')
            self.assertEqual((daily[33]['archived'], daily[33]['oldest_detail_day']), ([], 1))
            self.assertIn('daily_backup_failed', daily[33]['reasons'])
        with self.subTest('day 34: catch-up archives days 1-3 once'):
            self.assertTrue(all(days == [1, 2, 3] for kind, days, digest in daily[34]['archived']))
            self.assertEqual(len(daily[34]['archived']), len(ACCOUNTS) + 1)
            self.assertEqual(daily[34]['oldest_detail_day'], 4)
            self.assertEqual(daily[34]['retention_error'], None)
        with self.subTest('no archive loss: archived + detail = every row ever seen, once'):
            self.remember_detail()
            archived = [key for proof in self.proofs for key in proof['keys']]
            self.assertEqual(len(archived), len(set(archived)), 'a row was archived twice')
            detail = {('wallet_equity:' + name, row['at']) for name in ACCOUNTS for row in self.wallet_rows(name)}
            detail |= {('paper_publications', row['rowid']) for row in self.batch_rows()}
            self.assertEqual(set(archived) | detail, set(self.snapshot_rows))
            self.assertEqual(set(archived) & detail, set())
        with self.subTest('permanent daily summary preserved across retention'):
            after = retention.daily_summaries(self.root)
            self.assertEqual(set(after), set(ACCOUNTS))
            for account in ACCOUNTS:
                dates = [row['date'] for row in after[account]]
                self.assertEqual(dates, sorted(set(dates)))
                self.assertEqual(dates[0], T0.date().isoformat())
                kept = {row['date']: row for row in after[account]}
                for row in summaries_before[account]:
                    self.assertEqual(kept[row['date']], row, (account, row['date']))
        with self.subTest('failed backups between successful cycles block entries, never accumulate'):
            failed = by_label['backup_failure']
            self.assertEqual([row['errors'] for row in failed], [1, 1, 1])
            self.assertEqual([row['halt'] for row in failed], [False] * 3)
            self.assertTrue(all('daily_backup_failed' in row['reasons'] for row in failed))
            self.assertEqual((by_label['backup_recovered'][0]['errors'], by_label['backup_recovered'][0]['backup']),
                             (0, 'ok'))
        with self.subTest('latch exactly at the third consecutive failed cycle'):
            latch = by_label['outage']
            self.assertEqual([row['errors'] for row in latch], [1, 2, 3])
            self.assertEqual([row['halt'] for row in latch], [False, False, True])
            self.assertIn('worker_error_halt', latch[-1]['reasons'])
            self.assertEqual((latch[-1]['intents'], latch[-1]['receipts']), (0, 0))
        rows = {row['label']: row for row in self.timeline if row['day'] == 36}
        with self.subTest('restart keeps the latch; refused while accounts are not flat'):
            for label in ('open_1', 'open_2', 'open_3'):
                self.assertTrue(rows[label]['halt'], label)
                self.assertGreater(rows[label]['open'], 0, label)
                self.assertIn('worker_error_halt', rows[label]['reasons'])
            self.assertEqual(rows['open_3']['recovery'][:2], ['refused', 'accounts_not_flat'])
        with self.subTest('failed cycle resets the series'):
            self.assertTrue(rows['series_break']['halt'])
            self.assertEqual(rows['series_break']['errors'], 3, 'the latch keeps its error count')
            self.assertEqual(rows['series_break']['recovery'][:2], ['refused', 'accounts_not_flat'])
            self.assertEqual(rows['stop_exit']['open'], 0, 'stop exit runs while latched')
            self.assertEqual(rows['stop_exit']['recovery'], ['counting', None, 1])
            self.assertEqual(rows['flat_2']['recovery'], ['counting', None, 2])
        with self.subTest('refused when the backup of the day no longer verifies'):
            self.assertTrue(rows['tampered_backup']['halt'])
            self.assertEqual(rows['tampered_backup']['recovery'][:2], ['refused', 'daily_backup_missing'])
        with self.subTest('refused outside paper mode'):
            self.assertTrue(rows['mode_not_paper']['halt'])
            self.assertEqual(rows['mode_not_paper']['recovery'][:2], ['refused', 'mode_not_paper'])
        with self.subTest('lift at the end of the third successful cycle, all preconditions met'):
            self.assertFalse(rows['lift']['halt'])
            self.assertEqual(rows['lift']['errors'], 0)
            self.assertEqual(rows['lift']['recovery'][:2], ['completed', None])
            self.assertEqual((rows['lift']['intents'], rows['lift']['receipts']), (1, 1))
            self.assertNotIn('worker_error_halt', rows['lift']['reasons'])
            self.assertEqual(rows['lift']['backup'], 'ok')
            self.assertEqual(rows['lift']['storage'], 'ready')
            self.assertGreater(rows['entries_resume']['open'], 0, 'entries resume after the lift')
        with self.subTest('no premature unlock: every row before the lift is latched'):
            first_latched = self.timeline.index(by_label['outage'][-1])
            lifted = self.timeline.index(rows['lift'])
            self.assertTrue(all(row['halt'] for row in self.timeline[first_latched:lifted]))
        with self.subTest('durable receipt, idempotent restart'):
            audit_after = {path.name: path.read_bytes()
                           for path in self.recovery_files('intent') + self.recovery_files('receipt')}
            self.assertEqual(audit_after, audit_before)
            receipt = json.loads(self.recovery_files('receipt')[0].read_bytes())
            intent = json.loads(self.recovery_files('intent')[0].read_bytes())
            self.assertEqual(receipt['outcome'], 'completed')
            self.assertEqual(receipt['after_worker'], dict(receipt['before_worker'], entry_halt=False,
                                                           consecutive_errors=0))
            self.assertEqual(receipt['intent_hash'], _sha(json.dumps(intent, sort_keys=True, separators=(',', ':'),
                                                                      allow_nan=False).encode()))
            self.assertEqual(intent['request']['mode'], 'paper')
            self.assertEqual(len(intent['request']['successes']), WORKER_RECOVERY_SUCCESSES)
            self.assertEqual(intent['request']['bindings']['accounts'], dict.fromkeys(ACCOUNTS, 0))
            for row in by_label['after_receipt']:
                self.assertFalse(row['halt'])
                self.assertEqual((row['intents'], row['receipts']), (1, 1))
        with self.subTest('retention continues after the restarts'):
            self.assertEqual(by_label['after_receipt'][-1]['oldest_detail_day'], 38 - 30)
        with self.subTest('deterministic transcript'):
            self.assertEqual(_digest(self.timeline), TIMELINE_A_SHA256, json.dumps(self.timeline, indent=1))
        _export('timeline_a', self.timeline, receipt=self.receipt_view(self.recovery_files('receipt')[0]),
                retention_receipt=_retention_view(self.archive_dir(), ACCOUNTS[0]))

    # --- timeline B --------------------------------------------------------
    async def test_timeline_b_reserve_and_failed_backups_never_unlock(self):
        self.install_archive_spies()
        plan = {'reserve_day': 31}
        backup = FlakyBackup(fail=lambda index, now: (now - T0) // DAY >= plan['reserve_day'])

        async def sleep(seconds):
            row = self.observe('reserve' if row_day() >= plan['reserve_day'] else 'daily')
            if row['label'] == 'reserve' and len([r for r in self.timeline if r['label'] == 'reserve']) == 4:
                raise SimulatedCrash()
            self.remember_detail()
            if row['day'] < plan['reserve_day']:
                self.next_day()
                if self.day() == plan['reserve_day']:
                    self.disk.free_bytes = RESERVE_BYTES - 1
            else:
                self.next_minute()

        def row_day():
            return self.day()
        self.remember_detail()
        with backup.patch(), self.assertRaises(SimulatedCrash):
            await self.invoke(sleep=sleep)
        # Restart under the reserve, then days pass: never a lift.
        restart = []

        async def later(seconds):
            row = self.observe('restart')
            restart.append(row)
            if len(restart) == 3:
                raise SimulatedCrash()
            self.next_day()
        self.next_day()
        with FlakyBackup(failures=0).patch(), self.assertRaises(SimulatedCrash):
            await self.invoke(sleep=later)
        daily = [row for row in self.timeline if row['label'] == 'daily']
        reserve = [row for row in self.timeline if row['label'] == 'reserve']
        with self.subTest('logical bounds: 31 normal days then the reserve, beyond 30 days'):
            self.assertEqual([row['day'] for row in daily], list(range(31)))
            self.assertGreater(self.timeline[-1]['day'], retention.RETENTION_DAYS)
        with self.subTest('failed backups under the reserve: retention not attempted, nothing deleted'):
            self.assertEqual(daily[30]['oldest_detail_day'], 0)
            self.assertTrue(all(row['archived'] == [] and row['oldest_detail_day'] == 0 for row in reserve))
        with self.subTest('backup verified again after restart: retention resumes, 30-day cutoff only'):
            self.assertEqual([[days for kind, days, digest in row['archived']][0] for row in restart],
                             [[0, 1], [2], [3]])
            self.assertEqual([row['oldest_detail_day'] for row in restart], [2, 3, 4])
        with self.subTest('volume under reserve: storage stop, monitor alive'):
            for row in reserve + restart:
                self.assertEqual(row['storage'], 'blocked')
                self.assertIn('storage_stop', row['reasons'])
                self.assertEqual(row['projection'], 'reserve_reached')
        with self.subTest('three failed backups latch, restart keeps it, never lifted'):
            self.assertEqual([row['errors'] for row in reserve[:3]], [1, 2, 3])
            self.assertEqual([row['halt'] for row in reserve], [False, False, True, True])
            for row in restart:
                self.assertTrue(row['halt'])
                self.assertIn('worker_error_halt', row['reasons'])
                self.assertEqual(row['backup'], 'ok', 'backup recovered but storage is not ready')
            self.assertEqual((self.timeline[-1]['intents'], self.timeline[-1]['receipts']), (0, 0))
            self.assertTrue(all(row['recovery'][0] is None for row in reserve + restart))
        with self.subTest('deterministic transcript'):
            self.assertEqual(_digest(self.timeline), TIMELINE_B_SHA256, json.dumps(self.timeline, indent=1))
        _export('timeline_b', self.timeline)


def _stable_digest(proof):
    """Archived-content digest; publication packets carry per-process identities.

    A paired batch packet holds its uuid4 ``batch_id`` and the release
    ``instance_id``/``pid`` of the worker process that wrote it: its archive is
    still proven byte for byte within the run, but its digest is per run.
    """
    return 'per-run' if proof['kind'] == 'paper_publications' else proof['original_sha256'][:16]


def _retention_view(directory, account):
    path = sorted(directory.glob(account + '_wallet_equity_*.receipt.json'))[0]
    return dict(name=path.name, **json.loads(path.read_text()))


def _digest(timeline):
    return _sha(json.dumps(timeline, sort_keys=True, separators=(',', ':')).encode())


def _export(name, timeline, **extra):
    target = os.environ.get('LAT142_REPLAY_TRANSCRIPT')
    if not target:
        return
    path = Path(target)
    body = json.loads(path.read_text()) if path.exists() else {}
    body[name] = dict(timeline=timeline, **extra)
    path.write_text(json.dumps(body, indent=1, sort_keys=True) + '\n')


# Expected transcripts on the fixed logical clock (see ``observe``).
TIMELINE_A_SHA256 = 'e5bbd6cb32f7ad91c39792082f4363ccb6e937673d8dedafbcdfa41073fbd511'
TIMELINE_B_SHA256 = '16246dfbbde1d0d29519779a91250ea5ff720c0cffa28ea4720311d1462ad499'

load_tests = own_tests(globals())
