"""Checkpoint identity and captured terminal-lineage replay invariants."""
from contextlib import closing
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class QuoteCheckpointStateTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('hermes_trading.archive_quote_checkpoint_state'),
                             'bounded quote-checkpoint state is not implemented')
        from hermes_trading import archive_quote_checkpoint_state
        return archive_quote_checkpoint_state

    def positions(self):
        db = self.enterContext(closing(sqlite3.connect(':memory:')))
        db.execute('''CREATE TABLE positions(position_id TEXT PRIMARY KEY,asset TEXT,status TEXT,
            opened_at TEXT,closed_at TEXT,realized_pnl TEXT)''')
        db.executemany('INSERT INTO positions VALUES(?,?,?,?,?,?)', [
            ('old', 'BTC/EUR', 'closed', '2026-01-01T00:00:00+00:00', '2026-01-01T01:00:00+00:00', '1'),
            ('later', 'ETH/EUR', 'open', '2026-01-01T00:00:00+00:00', None, '0')])
        return db

    def test_request_identity_is_purpose_cohort_and_ordinal_bound(self):
        m = self.module()
        first = m.operation_id('a'*64, 1)
        self.assertEqual(first, m.operation_id('a'*64, 1))
        self.assertEqual(len(first), 32)
        self.assertNotEqual(first, m.operation_id('a'*64, 2))
        self.assertNotEqual(first, m.operation_id('b'*64, 1))
        for value in (0, -1, True, 1.0, 2**63):
            with self.subTest(value=value), self.assertRaises(ValueError):
                m.operation_id('a'*64, value)

    def test_empty_detailed_table_cannot_reset_compacted_ordinal(self):
        m = self.module()
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute('CREATE TABLE paired_quote_retention(operation_id TEXT PRIMARY KEY)')
            self.assertEqual(m.next_ordinal(db, None), 1)
            self.assertEqual(m.next_ordinal(db, {'through_ordinal': 40}), 41)
            db.execute('INSERT INTO paired_quote_retention(rowid,operation_id) VALUES(45,?)', ('next',))
            self.assertEqual(m.next_ordinal(db, {'through_ordinal': 40}), 46)

    def test_later_close_at_same_timestamp_does_not_become_captured_terminal(self):
        m = self.module(); db = self.positions()
        summary = m.terminal_summary(db, ['BTC/EUR', 'ETH/EUR'])
        at = '2026-01-01T01:00:00+00:00'
        self.assertTrue(m.terminal_quote_retired(db, 'BTC/EUR', 'paper-position:old', at, summary))
        db.execute("UPDATE positions SET status='closed',closed_at=?,realized_pnl='2' WHERE position_id='later'", (at,))
        m.assert_terminal_summary(db, summary)
        self.assertFalse(m.terminal_quote_retired(db, 'ETH/EUR', 'paper-position:later', at, summary))
        self.assertFalse(m.terminal_quote_retired(db, 'ETH/EUR', 'paper-position:old', at, summary))
        self.assertFalse(m.terminal_quote_retired(db, 'BTC/EUR', 'paper-position:old', '2026-01-01T02:00:00+00:00', summary))

    def test_captured_closed_finance_cannot_change_or_disappear(self):
        m = self.module(); db = self.positions()
        summary = m.terminal_summary(db, ['BTC/EUR', 'ETH/EUR'])
        db.execute("UPDATE positions SET realized_pnl='999' WHERE position_id='old'")
        with self.assertRaises(ValueError): m.assert_terminal_summary(db, summary)
        db.execute("DELETE FROM positions WHERE position_id='old'")
        with self.assertRaises(ValueError): m.assert_terminal_summary(db, summary)

    def test_new_finance_above_captured_rowid_is_preserved_and_not_covered(self):
        m = self.module(); db = self.positions()
        summary = m.terminal_summary(db, ['BTC/EUR', 'ETH/EUR'])
        db.execute('INSERT INTO positions VALUES(?,?,?,?,?,?)',
                   ('new', 'BTC/EUR', 'closed', '2026-01-01T02:00:00+00:00', '2026-01-01T03:00:00+00:00', '3'))
        m.assert_terminal_summary(db, summary)
        self.assertFalse(m.terminal_quote_retired(db, 'BTC/EUR', 'paper-position:new',
                                                '2026-01-01T03:00:00+00:00', summary))

    def test_nonterminal_summary_has_a_finite_asset_bound(self):
        m = self.module(); db = self.positions()
        db.execute('INSERT INTO positions VALUES(?,?,?,?,?,?)',
                   ('extra', 'BTC/EUR', 'open', '2026-01-01T02:00:00+00:00', None, '0'))
        with self.assertRaises(ValueError): m.terminal_summary(db, ['ETH/EUR'])

    def test_parser_refuses_noncanonical_original_archive_identity(self):
        from hermes_trading import archive_quote_state as state
        from hermes_trading.archive_journal import ACCOUNTS
        plan = dict(schema_version=1, purpose=state.PURPOSE, operation_id='a'*32,
            archive_operation_id='b'*32, archive_sha256='c'*64, cohort_sha256='d'*64,
            cut_at='2026-10-01T00:00:00+00:00',
            accounts={name: dict(directory=name, items=[]) for name in ACCOUNTS})
        progress = dict(status='complete', accounts={name: dict(done=True, stage=None, orphans=[])
                                                   for name in ACCOUNTS})
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute('CREATE TABLE paired_quote_retention(operation_id TEXT PRIMARY KEY,plan TEXT,progress TEXT)')
            db.execute('INSERT INTO paired_quote_retention VALUES(?,?,?)',
                       (plan['operation_id'], state.envelope(plan), state.envelope(progress)))
            self.assertEqual(len(state.operations(db)), 1)
            for bad in (None, True, 7, [], 'x'*32, 'B'*32, '../outside', 'b'*32+'/../../outside'):
                with self.subTest(reference=bad):
                    plan['archive_operation_id'] = bad
                    db.execute('UPDATE paired_quote_retention SET plan=?', (state.envelope(plan),))
                    before = db.execute('SELECT rowid,* FROM paired_quote_retention').fetchall()
                    with self.assertRaises(ValueError):
                        state.operations(db)
                    self.assertEqual(before, db.execute('SELECT rowid,* FROM paired_quote_retention').fetchall())

    def test_coordinator_cannot_create_a_directory_outside_its_temporary_cache(self):
        from hermes_trading import archive_quote_checkpoint as coordinator
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root/'cache'
            cache.mkdir()
            identity = 'a'*32
            (cache/('original-'+identity)).mkdir()
            plans = [dict(archive_operation_id=identity+'/../../outside')]
            with patch.object(coordinator, '_verify_archive', side_effect=ValueError('unknown original archive')):
                with self.assertRaises(ValueError):
                    coordinator._original_quote_evidence(None, None, None, plans, None, cache,
                                                        SimpleNamespace(check=lambda: None), None)
            self.assertFalse((root/'outside').exists())
