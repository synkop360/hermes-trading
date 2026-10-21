from datetime import timedelta
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_global_reflection as fixtures
from hermes_trading.supervisor_sync import capture_startup_release, make_snapshot, validate_snapshot

NOW = fixtures.NOW


class SnapshotTests(unittest.TestCase):
    setUp = fixtures.GlobalReviewTests.setUp
    tearDown = fixtures.GlobalReviewTests.tearDown
    write_trades = fixtures.GlobalReviewTests.write_trades

    def heartbeat(self):
        heartbeat = {'timestamp': NOW.isoformat(), 'mode': 'training', 'execution': 'simulated',
                     'status': 'ok', 'strategy_version': '01',
                     'worker_release': capture_startup_release(self.state, now=fixtures.START)}
        (self.state / 'heartbeat.json').write_text(json.dumps(heartbeat))
        return heartbeat

    def snapshot(self):
        return make_snapshot(self.state, now=NOW)

    def test_unfinished_paired_batch_blocks_supervisor_snapshot(self):
        import sqlite3
        self.heartbeat()
        db=sqlite3.connect(self.state/'paper_publications.sqlite')
        db.execute('CREATE TABLE paired_batches (batch_id TEXT PRIMARY KEY,status TEXT)')
        db.execute("INSERT INTO paired_batches VALUES ('test','pending')");db.commit();db.close()
        with self.assertRaisesRegex(ValueError,'paired batch'):self.snapshot()

    def test_fresh_snapshot_preserves_exact_bytes_and_checks_loaded_release(self):
        self.heartbeat()
        policy = json.loads((self.state / 'reflection_owner.json').read_text())
        files = validate_snapshot(self.snapshot(), policy, now=NOW)
        self.assertEqual(files['strategy.yaml'], (self.state / 'strategy.yaml').read_bytes())
        self.assertEqual(files['trades.jsonl'], (self.state / 'trades.jsonl').read_bytes())

    def test_stale_degraded_live_and_old_release_heartbeats_are_rejected(self):
        good = self.heartbeat()
        cases = [dict(good, timestamp=(NOW-timedelta(hours=1)).isoformat()),
                 dict(good, status='degraded'), dict(good, execution='live'),
                 dict(good, worker_release=None)]
        for heartbeat in cases:
            (self.state / 'heartbeat.json').write_text(json.dumps(heartbeat))
            with self.subTest(heartbeat=heartbeat), self.assertRaises(ValueError): self.snapshot()

    def test_unfinished_trade_line_and_wrong_policy_block_snapshot(self):
        self.heartbeat()
        (self.state / 'trades.jsonl').write_text('{"position_id":')
        with self.assertRaises(ValueError): self.snapshot()
        self.write_trades()
        policy = json.loads((self.state / 'reflection_owner.json').read_text())
        policy['experiment_id'] = 'different'
        with self.assertRaises(ValueError): validate_snapshot(self.snapshot(), policy, now=NOW)

    def test_configuration_change_during_read_refuses_mixed_snapshot(self):
        self.heartbeat()
        original=Path.read_bytes
        def read(path):
            if path == self.state/'trades.jsonl':
                (self.state/'goal.yaml').write_text('changed: true\n')
            return original(path)
        with patch.object(Path,'read_bytes',read), self.assertRaisesRegex(ValueError,'changed during snapshot'):
            self.snapshot()

    def test_changed_position_code_fingerprint_blocks_loaded_release(self):
        heartbeat=self.heartbeat()
        self.assertIn('positions.py',heartbeat['worker_release']['hashes'])
        heartbeat['worker_release']['hashes']['positions.py']='0'*64
        (self.state/'heartbeat.json').write_text(json.dumps(heartbeat))
        with self.assertRaisesRegex(ValueError,'startup release'): self.snapshot()

    def test_account_report_must_match_worker_heartbeat_hash(self):
        from hermes_trading.reflection_supervision import _hash
        report={'schema_version':1,'status':'observed','observed_at':NOW.isoformat(),'experiment_id':'trial-1'}
        policy=json.loads((self.state/'reflection_owner.json').read_text())
        report['experiment_id']=policy['experiment_id']
        heartbeat=self.heartbeat(); heartbeat['wallet_account']={'status':'observed'}
        heartbeat['wallet_report_hash']=_hash(report)
        (self.state/'heartbeat.json').write_text(json.dumps(heartbeat))
        (self.state/'wallet_metrics.json').write_text(json.dumps(report))
        payload=self.snapshot()
        files=validate_snapshot(payload,policy,now=NOW)
        self.assertEqual(json.loads(files['wallet_metrics.json']),report)
        report['status']='different'
        (self.state/'wallet_metrics.json').write_text(json.dumps(report))
        with self.assertRaises(ValueError): self.snapshot()


if __name__ == '__main__': unittest.main()
