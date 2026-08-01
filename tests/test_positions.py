from datetime import UTC, datetime, timedelta
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest

from hermes_trading.market_data import ClosedCandle, MarketSnapshot
from hermes_trading.notifications import _build_periodic_summary_message
from hermes_trading.persistence import StateRepository
from hermes_trading.persistence.artifacts import ArtifactProjector
from hermes_trading.positions import PositionEngine
from hermes_trading.strategy import evaluate_strategy


def candle(close_at: datetime, *, open: str, low: str, close: str, high: str | None = None) -> MarketSnapshot:
    return MarketSnapshot(
        asset="BTC/USDT",
        timeframe="1h",
        availability="available",
        decision_action="evaluate",
        reason="closed_candle_available",
        candle=ClosedCandle(
            open_timestamp=close_at - timedelta(hours=1),
            close_timestamp=close_at,
            open=Decimal(open),
            high=Decimal(high) if high is not None else max(Decimal(open), Decimal(close)),
            low=Decimal(low),
            close=Decimal(close),
            volume=Decimal("1"),
        ),
        price={"closes": tuple(Decimal("100") for _ in range(15)) + (Decimal("80"),)},
    )


def entry_strategy() -> dict[str, object]:
    return {
        "version": "test",
        "entry": {"indicator": "rsi", "threshold": "30", "direction": "long"},
        "stop_loss_pct": "0.5",
    }


class PositionEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.state = StateRepository.open(Path(self.temporary_directory.name) / "trading.db")
        self.state.migrate()
        self.engine = PositionEngine(self.state)
        self.entry_candle = candle(
            datetime(2026, 7, 30, 20, tzinfo=UTC), open="100", low="79", close="80"
        )

    def tearDown(self) -> None:
        self.state.close()
        self.temporary_directory.cleanup()

    def test_entry_does_not_close_on_same_candle(self) -> None:
        result = self.engine.process_candle(self.entry_candle, entry_strategy())

        self.assertTrue(result.processed)
        self.assertEqual(result.position.status.value, "open")
        self.assertEqual(result.closed_positions, ())

    def test_rsi_recovery_requires_a_cross_above_the_threshold_and_price_recovery(self) -> None:
        closes = tuple(Decimal(value) for value in range(100, 85, -1)) + (Decimal("91"),)
        snapshot = MarketSnapshot(
            asset="BTC/USDT", timeframe="1h", availability="available",
            decision_action="evaluate", reason="closed_candle_available",
            candle=ClosedCandle(
                self.entry_candle.candle.open_timestamp, self.entry_candle.candle.close_timestamp,
                Decimal("86"), Decimal("91"), Decimal("86"), Decimal("91"), Decimal("1"),
            ),
            price={"closes": closes},
        )

        signal = evaluate_strategy(
            snapshot,
            {"entry": {"indicator": "rsi_recovery", "threshold": "25", "direction": "long"}},
            None,
        )

        self.assertEqual(signal.action, "enter")
        self.assertEqual(signal.reason, "rsi_recovery")

    def test_stop_exit_uses_low_and_charges_fees(self) -> None:
        self.engine.process_candle(self.entry_candle, entry_strategy())
        result = self.engine.process_candle(
            candle(datetime(2026, 7, 30, 21, tzinfo=UTC), open="100", low="70", close="90"),
            entry_strategy(),
        )

        closed = result.closed_positions[0]
        self.assertEqual(closed.status.value, "closed")
        self.assertLess(closed.realized_pnl, Decimal("0"))
        self.assertGreater(closed.fees, Decimal("0"))

    def test_take_profit_closes_at_the_target_price(self) -> None:
        strategy = entry_strategy()
        strategy["exit"] = {"take_profit_pct": "1"}
        self.engine.process_candle(self.entry_candle, strategy)

        result = self.engine.process_candle(
            candle(datetime(2026, 7, 30, 21, tzinfo=UTC), open="80", low="80", high="82", close="81"),
            strategy,
        )

        self.assertEqual(result.reason, "take_profit")
        self.assertEqual(result.closed_positions[0].status.value, "closed")
        self.assertGreater(result.closed_positions[0].realized_pnl, Decimal("0"))

    def test_max_bars_closes_an_open_position(self) -> None:
        strategy = entry_strategy()
        strategy["exit"] = {"max_bars": "2"}
        self.engine.process_candle(self.entry_candle, strategy)

        first = self.engine.process_candle(
            candle(datetime(2026, 7, 30, 21, tzinfo=UTC), open="80", low="80", close="80"), strategy
        )
        second = self.engine.process_candle(
            candle(datetime(2026, 7, 30, 22, tzinfo=UTC), open="80", low="80", close="80"), strategy
        )

        self.assertEqual(first.position.status.value, "open")
        self.assertEqual(second.reason, "max_bars")
        self.assertEqual(second.closed_positions[0].status.value, "closed")

    def test_same_candle_is_not_processed_twice(self) -> None:
        first = self.engine.process_candle(self.entry_candle, entry_strategy())
        second = self.engine.process_candle(self.entry_candle, entry_strategy())

        self.assertTrue(first.processed)
        self.assertFalse(second.processed)
        self.assertEqual(second.reason, "duplicate_candle")

    def test_open_position_survives_restart(self) -> None:
        self.engine.process_candle(self.entry_candle, entry_strategy())
        database_path = Path(self.temporary_directory.name) / "trading.db"
        self.state.close()
        self.state = StateRepository.open(database_path)
        self.engine = PositionEngine(self.state)

        result = self.engine.process_candle(
            candle(datetime(2026, 7, 30, 21, tzinfo=UTC), open="100", low="90", close="95"),
            entry_strategy(),
        )

        self.assertTrue(result.processed)
        self.assertEqual(result.position.status.value, "open")

    def test_projection_records_fills_and_positions_once_per_committed_candle(self) -> None:
        artifact_directory = Path(self.temporary_directory.name) / "artifacts"
        engine = PositionEngine(self.state, ArtifactProjector(artifact_directory))
        engine.process_candle(self.entry_candle, entry_strategy())
        engine.process_candle(self.entry_candle, entry_strategy())
        engine.process_candle(
            candle(datetime(2026, 7, 30, 21, tzinfo=UTC), open="100", low="70", close="90"),
            entry_strategy(),
        )

        self.assertEqual(_jsonl_count(artifact_directory / "fills.jsonl"), 2)
        self.assertEqual(_jsonl_count(artifact_directory / "positions.jsonl"), 2)
        self.assertEqual(_jsonl_count(artifact_directory / "trades.jsonl"), 1)

    def test_stale_closed_candle_persists_one_no_trade_decision(self) -> None:
        stale = MarketSnapshot(**{**self.entry_candle.__dict__, "availability": "stale", "decision_action": "no_trade", "reason": "stale_closed_candle"})

        first = self.engine.process_candle(stale, entry_strategy())
        second = self.engine.process_candle(stale, entry_strategy())

        self.assertTrue(first.processed)
        self.assertEqual(first.decision.action, "no_trade")
        self.assertEqual(first.orders, ())
        self.assertFalse(second.processed)

    def test_older_candle_cannot_close_open_position(self) -> None:
        self.engine.process_candle(self.entry_candle, entry_strategy())
        older = candle(datetime(2026, 7, 30, 19, tzinfo=UTC), open="80", low="60", close="70")

        result = self.engine.process_candle(older, entry_strategy())

        self.assertTrue(result.processed)
        self.assertEqual(result.reason, "out_of_order_candle")
        self.assertEqual(result.position.status.value, "open")
        self.assertEqual(result.orders, ())

    def test_simulation_rates_must_be_less_than_one(self) -> None:
        for setting in ("slippage_rate", "taker_fee_rate", "maker_fee_rate"):
            strategy = entry_strategy()
            strategy["simulation"] = {setting: "1"}
            with self.subTest(setting=setting), self.assertRaises(ValueError):
                self.engine.process_candle(self.entry_candle, strategy)

    def test_stop_always_uses_taker_fee_even_with_maker_explicit_exit(self) -> None:
        strategy = entry_strategy()
        strategy["exit"] = {"indicator": "rsi", "threshold": "70", "fee": "maker"}
        strategy["simulation"] = {"taker_fee_rate": "0.01", "maker_fee_rate": "0.5"}
        self.engine.process_candle(self.entry_candle, strategy)

        result = self.engine.process_candle(
            candle(datetime(2026, 7, 30, 21, tzinfo=UTC), open="100", low="70", close="90"),
            strategy,
        )

        exit_order = result.orders[0]
        self.assertEqual(exit_order.fees, exit_order.filled_quantity * exit_order.average_fill_price * Decimal("0.01"))

    def test_projection_resumes_idempotently_after_each_output_step(self) -> None:
        for fail_at in range(1, 9):
            with self.subTest(fail_at=fail_at), tempfile.TemporaryDirectory() as directory:
                repository = StateRepository.open(Path(directory) / "trading.db")
                repository.migrate()
                artifact_directory = Path(directory) / "artifacts"
                PositionEngine(repository, ArtifactProjector(artifact_directory)).process_candle(self.entry_candle, entry_strategy())
                projector = _FailingProjector(artifact_directory, fail_at)
                close_result = PositionEngine(repository, projector).process_candle(
                    candle(datetime(2026, 7, 30, 21, tzinfo=UTC), open="100", low="70", close="90"),
                    entry_strategy(),
                )
                self.assertIsNotNone(close_result.projection_error)

                PositionEngine(repository, ArtifactProjector(projector.state_dir)).process_candle(
                    candle(datetime(2026, 7, 30, 21, tzinfo=UTC), open="100", low="70", close="90"),
                    entry_strategy(),
                )

                self.assertEqual(_jsonl_count(projector.state_dir / "decisions.jsonl"), 2)
                self.assertEqual(_jsonl_count(projector.state_dir / "orders.jsonl"), 2)
                self.assertEqual(_jsonl_count(projector.state_dir / "fills.jsonl"), 2)
                self.assertEqual(_jsonl_count(projector.state_dir / "positions.jsonl"), 2)
                self.assertEqual(_jsonl_count(projector.state_dir / "trades.jsonl"), 1)
                for snapshot_name in ArtifactProjector.SNAPSHOT_NAMES:
                    self.assertTrue((projector.state_dir / snapshot_name).exists())
                repository.close()

    def test_healthy_duplicate_does_not_reconcile(self) -> None:
        projector = _CountingProjector(Path(self.temporary_directory.name) / "counting")
        engine = PositionEngine(self.state, projector)
        engine.process_candle(self.entry_candle, entry_strategy())

        duplicate = engine.process_candle(self.entry_candle, entry_strategy())

        self.assertIsNone(duplicate.projection_error)
        self.assertEqual(projector.reconcile_calls, 0)

    def test_each_committed_outcome_has_its_own_projection_acknowledgement(self) -> None:
        directory = Path(self.temporary_directory.name) / "concurrent-outbox"
        failing = _PersistentFailProjector(directory)
        first = PositionEngine(self.state, failing).process_candle(self.entry_candle, entry_strategy())
        second_snapshot = MarketSnapshot(**{
            **self.entry_candle.__dict__,
            "asset": "ETH/USDT",
            "candle": ClosedCandle(
                self.entry_candle.candle.open_timestamp,
                self.entry_candle.candle.close_timestamp,
                self.entry_candle.candle.open,
                self.entry_candle.candle.high,
                self.entry_candle.candle.low,
                self.entry_candle.candle.close,
                self.entry_candle.candle.volume,
            ),
        })
        second = PositionEngine(self.state, failing).process_candle(second_snapshot, entry_strategy())

        self.assertIsNotNone(first.projection_error)
        self.assertIsNotNone(second.projection_error)
        pending = self.state.pending_projection_items()
        self.assertEqual(len(pending), 2)

        database_path = Path(self.temporary_directory.name) / "trading.db"
        def acknowledge(item_id: str) -> bool:
            repository = StateRepository.open(database_path)
            try:
                return repository.acknowledge_projection_item(item_id)
            finally:
                repository.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            acknowledgements = list(executor.map(acknowledge, [item.item_id for item in pending]))
        self.assertEqual(acknowledgements, [True, True])
        remaining = self.state.pending_projection_items()
        self.assertEqual(remaining, ())

    def test_pending_projection_is_retried_when_snapshot_has_no_candle(self) -> None:
        directory = Path(self.temporary_directory.name) / "no-candle-recovery"
        failing = _FailingProjector(directory, 1)
        first = PositionEngine(self.state, failing).process_candle(self.entry_candle, entry_strategy())
        self.assertIsNotNone(first.projection_error)

        unavailable = MarketSnapshot(**{
            **self.entry_candle.__dict__,
            "availability": "unavailable",
            "decision_action": "no_trade",
            "reason": "price_unavailable",
            "candle": None,
        })
        retry = PositionEngine(self.state, _PersistentFailProjector(directory)).process_candle(
            unavailable, entry_strategy()
        )

        self.assertIsNotNone(retry.projection_error)
        self.assertTrue(self.state.pending_projection_items())

    def test_missing_projector_never_acknowledges_canonical_projection(self) -> None:
        result = self.engine.process_candle(self.entry_candle, entry_strategy())

        self.assertEqual(result.projection_error, "no artifact projector is configured")
        self.assertEqual(len(self.state.pending_projection_items()), 1)

    def test_restart_projects_one_pending_item_without_duplicate_events(self) -> None:
        directory = Path(self.temporary_directory.name) / "restart-outbox"
        failed = PositionEngine(self.state, _FailingProjector(directory, 2))
        failed.process_candle(self.entry_candle, entry_strategy())
        self.assertEqual(len(self.state.pending_projection_items()), 1)

        resumed = PositionEngine(self.state, ArtifactProjector(directory))
        retry = resumed.process_candle(self.entry_candle, entry_strategy())
        duplicate = resumed.process_candle(self.entry_candle, entry_strategy())

        self.assertIsNone(retry.projection_error)
        self.assertIsNone(duplicate.projection_error)
        self.assertFalse(self.state.pending_projection_items())
        self.assertEqual(_jsonl_count(directory / "decisions.jsonl"), 1)

    def test_recovery_projects_each_pending_position_transition_as_committed(self) -> None:
        """A later close must not rewrite an earlier pending open transition."""
        directory = Path(self.temporary_directory.name) / "immutable-outbox"
        failed = PositionEngine(self.state, _PersistentFailProjector(directory))
        entry = failed.process_candle(self.entry_candle, entry_strategy())
        exit_result = failed.process_candle(
            candle(datetime(2026, 7, 30, 21, tzinfo=UTC), open="100", low="70", close="90"),
            entry_strategy(),
        )
        self.assertIsNotNone(entry.projection_error)
        self.assertIsNotNone(exit_result.projection_error)
        self.assertEqual(len(self.state.pending_projection_items()), 2)

        resumed = PositionEngine(self.state, ArtifactProjector(directory))
        self.assertIsNone(resumed.recover_pending_projections())

        positions = [
            json.loads(line)
            for line in (directory / "positions.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        trades = [
            json.loads(line)
            for line in (directory / "trades.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual([position["status"] for position in positions], ["open", "closed"])
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["status"], "closed")
        self.assertFalse(self.state.pending_projection_items())

    def test_migration_converts_legacy_global_projection_marker_to_items(self) -> None:
        self.engine.process_candle(self.entry_candle, entry_strategy())
        with self.state._connection:
            self.state._connection.execute(
                """INSERT INTO incidents (incident_id, status, reason, severity, revision, opened_at, resolved_at)
                VALUES ('projection:pending', 'open', 'legacy', 'warning', 1, '2026-07-30T20:00:00+00:00', NULL)"""
            )

        self.state.migrate()

        self.assertFalse(self.state._connection.execute(
            "SELECT 1 FROM incidents WHERE incident_id = 'projection:pending' AND status = 'open'"
        ).fetchone())
        self.assertTrue(self.state.pending_projection_items())

    def test_failed_projection_retries_only_while_pending_and_surfaces_error(self) -> None:
        directory = Path(self.temporary_directory.name) / "pending"
        failed = _FailingProjector(directory, 1)
        first = PositionEngine(self.state, failed).process_candle(self.entry_candle, entry_strategy())
        self.assertIsNotNone(first.projection_error)

        retrying = _PersistentFailProjector(directory)
        second = PositionEngine(self.state, retrying).process_candle(self.entry_candle, entry_strategy())
        third = PositionEngine(self.state, retrying).process_candle(self.entry_candle, entry_strategy())

        self.assertIsNotNone(second.projection_error)
        self.assertIsNotNone(third.projection_error)
        self.assertEqual(retrying.reconcile_calls, 2)

    def test_canonical_jsonl_events_feed_periodic_summary(self) -> None:
        artifact_directory = Path(self.temporary_directory.name) / "summary-artifacts"
        engine = PositionEngine(self.state, ArtifactProjector(artifact_directory))
        engine.process_candle(self.entry_candle, entry_strategy())
        engine.process_candle(
            candle(datetime(2026, 7, 30, 21, tzinfo=UTC), open="100", low="70", close="90"),
            entry_strategy(),
        )

        summary = _build_periodic_summary_message(
            artifact_directory,
            "1h",
            datetime(2026, 7, 30, 19, tzinfo=UTC),
            datetime(2026, 7, 30, 22, tzinfo=UTC),
        )

        self.assertIn("Decisions: total=2", summary)
        self.assertIn("Trades: total=1", summary)
        self.assertIn("Orders: total=2", summary)
        self.assertNotIn("total_pnl=0 |", summary)


def _jsonl_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


class _FailingProjector(ArtifactProjector):
    def __init__(self, state_dir: Path, fail_at: int) -> None:
        super().__init__(state_dir)
        self.fail_at = fail_at
        self.operations = 0

    def _maybe_fail(self) -> None:
        self.operations += 1
        if self.operations == self.fail_at:
            raise OSError("injected projection failure")

    def append_event(self, name: str, payload: object) -> None:
        self._maybe_fail()
        super().append_event(name, payload)

    def write_snapshot(self, name: str, payload: object) -> None:
        self._maybe_fail()
        super().write_snapshot(name, payload)


class _CountingProjector(ArtifactProjector):
    def __init__(self, state_dir: Path) -> None:
        super().__init__(state_dir)
        self.reconcile_calls = 0

    def reconcile(self, repository: StateRepository) -> None:
        self.reconcile_calls += 1
        super().reconcile(repository)


class _PersistentFailProjector(_CountingProjector):
    def project_pending_item(self, repository: StateRepository, item: object) -> None:
        self.reconcile_calls += 1
        raise OSError("projection storage still unavailable")
