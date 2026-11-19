"""Fifteen-minute balance cadence of the recette (LAT-122), apart from alerts.

Fake clock and sleep, ``.test-tmp-recette-cadence-*`` volumes, local transport:
no market, network, Discord or secret.
"""
from __future__ import annotations

import asyncio
import io
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fakes.notifications import ManualClock, RecordingTransport
from hermes_trading import pilot_paper
from scripts import recette_notifications as notifications
from scripts import recette_service as service

ROOT = Path(__file__).resolve().parents[1]
SLOT_START = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
CLEAN_REPORT = {"stop_reason": "max_cycles_reached", "completed_cycles": 1,
                "samples": 1, "entries": 0, "exits": 0,
                "public_proof": "not_eligible_injected_collector"}


def report(days=None, equity="200.00"):
    proj = ({"status": "projected", "days_before_reserve": days} if days is not None
            else {"status": "insufficient_data", "days_before_reserve": None})
    return {"growth_and_projection": proj,
            "accounts": {"accounts": {"adaptive_200": {"net_equity": equity, "currency": "EUR"},
                                      "control_200": {"net_equity": "/etc/passwd", "currency": "eur"}}}}


class BalanceCadenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.volume = Path(tempfile.mkdtemp(prefix=".test-tmp-recette-cadence-", dir=ROOT))
        self.addCleanup(shutil.rmtree, self.volume, ignore_errors=True)
        self.clock = ManualClock(SLOT_START)
        self.transport = RecordingTransport()
        self.notifier = notifications.RecetteNotifier(self.volume, transport=self.transport)

    def balances(self, seconds=0, notifier=None, value=None):
        self.clock.advance(seconds)
        messages = (notifier or self.notifier).process(value or report(), self.clock())
        return [m for m in messages if m["kind"] == "balances"]

    def test_one_proposal_per_quarter_hour_slot_with_exact_boundaries(self) -> None:
        first = self.balances()
        self.assertEqual([m["slot_start"] for m in first], [SLOT_START.isoformat()])
        self.assertEqual(self.balances(60), [])
        self.assertEqual(self.balances(839), [])          # 12:14:59
        second = self.balances(1)                          # 12:15:00
        self.assertEqual([m["slot_start"] for m in second], ["2026-11-20T12:15:00+00:00"])
        self.assertEqual(self.balances(899), [])          # 12:29:59
        self.assertEqual(len(self.balances(1)), 1)        # 12:30:00
        self.assertEqual(self.notifier.summary()["balances"]["proposed"], 3)

    def test_slots_skipped_while_idle_give_a_single_catch_up_proposal(self) -> None:
        self.balances()
        self.assertEqual(len(self.balances(3 * 900 + 10)), 1)
        self.assertEqual(self.notifier.summary()["balances"]["proposed"], 2)

    def test_balances_carry_only_validated_values(self) -> None:
        [message] = self.balances()
        self.assertEqual(message["accounts"], {
            "adaptive_200": {"net_equity": "200.00", "currency": "EUR"},
            "control_200": {"net_equity": None, "currency": None}})
        self.assertEqual(message["slot_seconds"], 900)

    def test_restart_inside_a_slot_does_not_propose_it_again(self) -> None:
        self.balances()
        restarted = notifications.RecetteNotifier(self.volume, transport=self.transport)
        self.assertEqual(self.balances(600, restarted), [])
        self.assertEqual(len(self.balances(300, restarted)), 1)
        self.assertEqual(restarted.summary()["balances"]["proposed"], 2)
        self.assertEqual(self.transport.kinds(), ["balances", "balances"])

    def test_balances_and_alerts_never_share_a_schedule(self) -> None:
        # An alert every minute neither consumes nor advances the balance slot...
        kinds = []
        for minute in range(15):
            kinds.append([m["kind"] for m in self.notifier.process(report(days=2), self.clock())])
            self.clock.advance(60)
        self.assertEqual(kinds[0], ["alert", "balances"])
        self.assertTrue(all(k == [] for k in kinds[1:]))
        # ...and the 12:15 slot proposes balances while the alert releases its own count.
        messages = self.notifier.process(report(days=2), self.clock())
        self.assertEqual(sorted(m["kind"] for m in messages), ["alert", "balances"])
        alert = next(m for m in messages if m["kind"] == "alert")
        self.assertEqual(alert["suppressed"], 14)
        summary = self.notifier.summary()
        self.assertEqual(summary["balances"]["proposed"], 2)
        self.assertEqual(summary["alerts"][notifications.ALERT_BELOW_THRESHOLD]["emitted"], 2)


class TickingSleepTests(unittest.TestCase):
    def test_long_waits_are_sliced_without_changing_their_total(self) -> None:
        slept, ticks = [], []

        async def sleep(seconds):
            slept.append(seconds)

        asyncio.run(service.ticking_sleep(sleep, 3600, lambda: ticks.append(sum(slept))))
        self.assertEqual(slept, [900.0, 900.0, 900.0, 900.0])
        self.assertEqual(ticks, [900.0, 1800.0, 2700.0])
        slept.clear()
        ticks.clear()
        asyncio.run(service.ticking_sleep(sleep, 60, lambda: ticks.append(1)))
        self.assertEqual((slept, ticks), ([60.0], []))
        asyncio.run(service.ticking_sleep(sleep, 1000, lambda: ticks.append(1)))
        self.assertEqual((slept[1:], ticks), ([900.0, 100.0], [1]))

    def test_cycle_sleep_reports_the_cycle_then_ticks(self) -> None:
        events = []

        async def sleep(seconds):
            events.append(("sleep", seconds))

        wrapped = service.cycle_sleep(sleep, lambda: events.append("cycle"), lambda: events.append("tick"))
        asyncio.run(wrapped(1800))
        self.assertEqual(events, ["cycle", ("sleep", 900.0), "tick", ("sleep", 900.0)])
        # Without on_tick the historical contract is unchanged.
        events.clear()
        asyncio.run(service.cycle_sleep(sleep, lambda: events.append("cycle"))(1800))
        self.assertEqual(events, ["cycle", ("sleep", 1800)])


class ServiceCadenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix=".test-tmp-recette-cadence-serve-", dir=ROOT))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root = self.base / "volume"
        self.environ = {"HERMES_TRADING_MODE": "paper",
                        "HERMES_TRADING_ALLOW_REAL_ORDERS": "false",
                        "HERMES_NOTIFY_ENABLED": "false"}
        self.clock = ManualClock(SLOT_START)
        self.transport = RecordingTransport()

    def serve(self, passes):
        calls = []

        async def runner(root, *, environ, bounds, on_cycle=None, on_tick=None):
            calls.append(root)
            # One cycle followed by a one-hour wait inside the session.
            await service.cycle_sleep(self.clock.sleep, on_cycle, on_tick)(3600)
            if len(calls) >= passes:
                (Path(root) / pilot_paper.STOP_FILE).write_text("stop")
            return dict(CLEAN_REPORT)

        return asyncio.run(service.serve(
            self.root, environ=self.environ, session_runner=runner, max_cycles=2,
            max_runtime_seconds=7200, interval_seconds=3600, reflect_every=1,
            sleep=self.clock.sleep, stream=io.StringIO(), report_clock=self.clock,
            notification_transport=self.transport))

    def test_hourly_cycles_still_propose_balances_every_fifteen_minutes(self) -> None:
        summary = self.serve(passes=2)
        self.assertEqual(summary["stop_reason"], "stop_requested")
        balances = [m for m in self.transport.messages if m["kind"] == "balances"]
        starts = [m["slot_start"] for m in balances]
        # 12:00 -> 15:00: two in-session hours and one between-pass hour.
        self.assertEqual(len(starts), len(set(starts)))
        self.assertEqual(starts[0], SLOT_START.isoformat())
        self.assertEqual(starts[-1], "2026-11-20T15:00:00+00:00")
        self.assertEqual(len(starts), 13)
        self.assertFalse([m for m in self.transport.messages if m["kind"] == "alert"])

    def test_a_restarted_service_resumes_the_same_cadence(self) -> None:
        self.serve(passes=1)
        first = len(self.transport.messages)
        (self.root / service.PILOT_DIRNAME / pilot_paper.STOP_FILE).unlink()
        self.serve(passes=1)
        starts = [m["slot_start"] for m in self.transport.messages if m["kind"] == "balances"]
        self.assertGreater(len(self.transport.messages), first)
        self.assertEqual(len(starts), len(set(starts)))
        stored = notifications.RecetteNotifier(self.root).summary()
        self.assertEqual(stored["restarts"], 2)
        self.assertEqual(stored["balances"]["proposed"], len(starts))

    def test_legacy_runner_without_on_tick_keeps_working(self) -> None:
        async def runner(root, *, environ, bounds, on_cycle=None):
            (Path(root) / pilot_paper.STOP_FILE).write_text("stop")
            return dict(CLEAN_REPORT)

        summary = asyncio.run(service.serve(
            self.root, environ=self.environ, session_runner=runner, max_cycles=1,
            max_runtime_seconds=3600, interval_seconds=3600, reflect_every=1,
            sleep=self.clock.sleep, stream=io.StringIO(), report_clock=self.clock))
        self.assertEqual(summary["stop_reason"], "stop_requested")


if __name__ == "__main__":
    unittest.main()
