from datetime import datetime, timedelta, timezone
import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from hermes_trading.reflection_supervision import (
    ReviewBusy, ReviewLease, WriterNotAuthorized, assert_writer_owner,
    initialize_supervisor, writer_allowed, _hash,
)

UTC = timezone.utc
START = datetime(2026, 10, 9, tzinfo=UTC)


def policy(state, owner="external_llm"):
    (state / "reflection_owner.json").write_text(json.dumps({
        "schema_version": 1, "owner": owner, "experiment_id": "trial-1",
        "cadence_closed_trades": 5, "one_variable_only": True,
        "model": {"provider": "local_model", "name": "test-v1"},
    }))


def trades(count, start=1):
    return [{"position_id": str(i), "asset": "BTC/EUR", "status": "closed",
             "closed_at": (START + timedelta(minutes=i)).isoformat()}
            for i in range(start, start + count)]


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        policy(self.state)
        initialize_supervisor(self.state, now=START)

    def tearDown(self):
        self.tmp.cleanup()

    def test_renewal_requirement_flag_cannot_use_truthy_nonboolean(self):
        from hermes_trading.reflection_supervision import load_owner_policy
        original=json.loads((self.state/'reflection_owner.json').read_text())
        for value in ('true',1,None,[]):
            (self.state/'reflection_owner.json').write_text(json.dumps(dict(original,model_access_renewal_required=value)))
            with self.subTest(value=value),self.assertRaises(ValueError):load_owner_policy(self.state)

    def test_five_unique_closes_are_needed_and_deterministic_logs_are_irrelevant(self):
        (self.state / "hypotheses.jsonl").write_text('{"timestamp":"2099-01-01","provider":"deterministic"}\n')
        with ReviewLease(self.state) as lease:
            self.assertFalse(lease.due(trades(4) + trades(1), now=START + timedelta(hours=1))["eligible"])
            self.assertTrue(lease.due(trades(5), now=START + timedelta(hours=1))["eligible"])

    def test_recovery_exit_blocks_learning_review_without_consuming_watermark(self):
        rows=trades(6); rows[0]['evidence_classification']='operational_recovery'
        rows[0]['exit_reason']='entry_contract_recovery_exit'
        with ReviewLease(self.state) as lease:
            due=lease.due(rows,now=START+timedelta(hours=1))
            self.assertFalse(due['eligible'])
            self.assertEqual(due['reason'],'recovery_trade_requires_reconciliation')
            self.assertTrue(lease.due(trades(5),now=START+timedelta(hours=1))['eligible'])

    def test_confirmed_no_change_consumes_only_reviewed_ids(self):
        with ReviewLease(self.state) as lease:
            review = lease.prepare(trades(5), now=START + timedelta(hours=1))
            lease.complete(review["review_id"], outcome="no_change", provider="local_model",
                           model="test-v1", now=START + timedelta(hours=2))
            due = lease.due(trades(6), now=START + timedelta(hours=3))
            self.assertFalse(due["eligible"])
            self.assertEqual(len(due["new_trade_ids"]), 1)
        with ReviewLease(self.state) as lease:
            self.assertFalse(lease.due(trades(5), now=START + timedelta(hours=4))["eligible"])

    def test_failure_does_not_consume_trades_and_enforces_backoff(self):
        with ReviewLease(self.state) as lease:
            now = START + timedelta(hours=1)
            review = lease.prepare(trades(5), now=now)
            lease.fail(review["review_id"], reason="model timeout", now=now)
            self.assertFalse(lease.due(trades(5), now=now + timedelta(minutes=1))["eligible"])
            self.assertTrue(lease.due(trades(5), now=now + timedelta(minutes=31))["eligible"])

    def test_committed_pending_review_blocks_duplicate_call_after_restart(self):
        with ReviewLease(self.state) as lease:
            lease.prepare(trades(5), now=START + timedelta(hours=1))
        with ReviewLease(self.state) as lease:
            result = lease.due(trades(5), now=START + timedelta(hours=2))
            self.assertFalse(result["eligible"])
            self.assertEqual(result["reason"], "pending_review_requires_reconciliation")

    def test_abrupt_process_exit_releases_lease_and_retains_pending_request(self):
        script = (
            "import json,os,sys; from datetime import datetime; "
            "from hermes_trading.reflection_supervision import ReviewLease; "
            "lease=ReviewLease(sys.argv[1]).__enter__(); "
            "lease.prepare(json.loads(sys.argv[2]),now=datetime.fromisoformat(sys.argv[3])); "
            "os._exit(0)"
        )
        result = subprocess.run([sys.executable, "-c", script, str(self.state), json.dumps(trades(5)),
                                 (START + timedelta(hours=1)).isoformat()],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        with ReviewLease(self.state) as lease:
            self.assertEqual(lease.due(trades(5), now=START + timedelta(hours=2))["reason"],
                             "pending_review_requires_reconciliation")

    def test_competing_lease_is_rejected(self):
        with ReviewLease(self.state):
            with self.assertRaises(ReviewBusy):
                with ReviewLease(self.state):
                    pass

    def test_competing_lease_is_rejected_after_pending_commit(self):
        with ReviewLease(self.state) as lease:
            lease.prepare(trades(5), now=START + timedelta(hours=1))
            with self.assertRaises(ReviewBusy):
                with ReviewLease(self.state):
                    pass

    def test_pending_review_retains_exact_input_snapshot_after_source_changes(self):
        rows = trades(5)
        rows[0]["pnl"] = 1.5
        with ReviewLease(self.state) as lease:
            review = lease.prepare(rows, now=START + timedelta(hours=1))
        rows[0]["pnl"] = -9
        with ReviewLease(self.state) as lease:
            recorded = json.loads(lease.db.execute("SELECT record FROM reviews WHERE review_id=?",
                                                  (review["review_id"],)).fetchone()[0])
            self.assertEqual(recorded["trade_snapshot"][0]["pnl"], 1.5)
            self.assertEqual(len(recorded["trade_snapshot"]), 5)

    def test_conflicting_duplicate_payload_is_rejected_in_both_orders(self):
        rows = trades(5)
        conflicting = dict(rows[0], pnl=99)
        with ReviewLease(self.state) as lease:
            for records in (rows + [conflicting], [conflicting] + rows):
                with self.subTest(records=records), self.assertRaises(ValueError):
                    lease.due(records, now=START + timedelta(hours=1))
            self.assertTrue(lease.due(rows + [dict(rows[0])], now=START + timedelta(hours=1))["eligible"])

    def test_reinitialization_cannot_reset_review_progress(self):
        with self.assertRaises(ValueError):
            initialize_supervisor(self.state, now=START + timedelta(days=1))

    def test_pinned_model_mismatch_cannot_confirm_a_review(self):
        with ReviewLease(self.state) as lease:
            review = lease.prepare(trades(5), now=START + timedelta(hours=1))
            with self.assertRaises(ValueError):
                lease.complete(review["review_id"], outcome="changed", provider="deterministic",
                               model=None, now=START + timedelta(hours=2))
            self.assertFalse(lease.due(trades(5), now=START + timedelta(hours=3))["eligible"])

    def test_open_future_and_old_positions_do_not_earn_reviews(self):
        rows = trades(4) + [{"position_id": "old", "asset": "BTC/EUR", "closed_at": START.isoformat()},
                            {"position_id": "future", "asset": "BTC/EUR", "closed_at": "2099-01-01T00:00:00Z"},
                            {"position_id": "open", "asset": "BTC/EUR", "status": "open", "closed_at": "2026-10-09T00:05:00Z"}]
        with ReviewLease(self.state) as lease:
            self.assertFalse(lease.due(rows, now=START + timedelta(hours=1))["eligible"])

    def test_late_arriving_close_is_not_lost_to_a_time_watermark(self):
        with ReviewLease(self.state) as lease:
            review = lease.prepare(trades(5), now=START + timedelta(hours=1))
            lease.complete(review["review_id"], outcome="no_change", provider="local_model",
                           model="test-v1", now=START + timedelta(hours=2))
            late = {"position_id": "late", "asset": "BTC/EUR", "closed_at": "2026-10-09T00:02:30Z"}
            self.assertEqual(len(lease.due(trades(5) + [late], now=START + timedelta(hours=3))["new_trade_ids"]), 1)

    def test_wrong_writer_is_blocked_before_reflection_work(self):
        from hermes_trading.reflect import run_fallback_reflection
        self.assertFalse(writer_allowed(self.state, "deterministic_worker"))
        with patch("hermes_trading.reflect._load_yaml") as load:
            with self.assertRaises(WriterNotAuthorized):
                run_fallback_reflection(self.state)
            load.assert_not_called()

    def test_wrong_writer_is_blocked_before_hermes_reflection_work(self):
        from hermes_trading.reflect import run_hermes_reflection
        policy(self.state, owner="deterministic_worker")
        with patch("hermes_trading.reflect._load_yaml") as load:
            with self.assertRaises(WriterNotAuthorized):
                run_hermes_reflection(self.state)
            load.assert_not_called()

    def test_external_review_is_blocked_when_worker_owns_changes(self):
        policy(self.state, owner="deterministic_worker")
        with self.assertRaises(WriterNotAuthorized):
            assert_writer_owner(self.state, "external_llm")

    def test_failed_completion_write_preserves_pending_review_and_trade_window(self):
        with ReviewLease(self.state) as lease:
            review = lease.prepare(trades(5), now=START + timedelta(hours=1))
            lease.db.execute("CREATE TEMP TRIGGER reject_state BEFORE UPDATE ON supervisor "
                             "BEGIN SELECT RAISE(ABORT, 'injected write failure'); END")
            with self.assertRaises(sqlite3.IntegrityError):
                lease.complete(review["review_id"], outcome="no_change", provider="local_model",
                               model="test-v1", now=START + timedelta(hours=2))
            recorded = json.loads(lease.db.execute("SELECT record FROM reviews WHERE review_id=?",
                                                  (review["review_id"],)).fetchone()[0])
            self.assertEqual(recorded["outcome"], "pending")
            self.assertEqual(lease.due(trades(5), now=START + timedelta(hours=3))["reason"],
                             "pending_review_requires_reconciliation")
            lease.db.execute("DROP TRIGGER reject_state")
            lease.complete(review["review_id"], outcome="no_change", provider="local_model",
                           model="test-v1", now=START + timedelta(hours=3))
        with ReviewLease(self.state) as lease:
            self.assertEqual(lease.due(trades(5), now=START + timedelta(hours=4))["new_trade_ids"], [])

    def test_failed_second_preparation_does_not_leave_orphan_request(self):
        with ReviewLease(self.state) as lease:
            review = lease.prepare(trades(5), now=START + timedelta(hours=1))
            lease.fail(review["review_id"], reason="test failure", now=START + timedelta(hours=1))
            lease.db.execute("CREATE TEMP TRIGGER reject_state BEFORE UPDATE ON supervisor "
                             "BEGIN SELECT RAISE(ABORT, 'injected write failure'); END")
            with self.assertRaises(sqlite3.IntegrityError):
                lease.prepare(trades(5), now=START + timedelta(hours=2))
            self.assertEqual(lease.db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0], 1)
            self.assertTrue(lease.due(trades(5), now=START + timedelta(hours=3))["eligible"])


class WorkerOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_training_worker_dispatches_only_when_it_owns_strategy_changes(self):
        from hermes_trading.loop import CycleResult, run_loop
        from hermes_trading.run import ensure_state_files
        for owner in ("external_llm", "deterministic_worker"):
            with self.subTest(owner=owner), tempfile.TemporaryDirectory() as tmp:
                state = Path(tmp)
                ensure_state_files(state)
                policy(state, owner)
                with patch.dict(os.environ, {"HERMES_TRADING_MODE": "training",
                                             "HERMES_NOTIFY_ENABLED": "false",
                                             "HERMES_REFLECTION_INPROCESS_EVERY_CYCLES": "1",
                                             "HERMES_QUALIFICATION_EVERY_CYCLES": "0"}), \
                        patch("hermes_trading.loop.run_cycle", new=AsyncMock(return_value=CycleResult(results=(), mode="training"))), \
                        patch("hermes_trading.loop._update_profitability_best_effort", new=AsyncMock(return_value=None)), \
                        patch("hermes_trading.loop.run_hermes_reflection", return_value={"version": "01"}) as reflect, \
                        patch("hermes_trading.loop.asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError)):
                    with self.assertRaises(asyncio.CancelledError):
                        await run_loop("BTC/EUR", state, interval_seconds=60)
                self.assertTrue((state / "heartbeat.json").exists())
                heartbeat = json.loads((state / "heartbeat.json").read_text())
                self.assertIn("worker_release", heartbeat)
                self.assertEqual(heartbeat["worker_release"]["policy_hash"],
                                 _hash(json.loads((state / "reflection_owner.json").read_text())))
                self.assertEqual(reflect.call_count, int(owner == "deterministic_worker"))
                if owner == "deterministic_worker":
                    self.assertEqual(reflect.call_args.kwargs["writer"], "deterministic_worker")


if __name__ == "__main__":
    unittest.main()
