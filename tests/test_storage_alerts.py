"""Storage projection alerts of the recette (LAT-122): threshold and aggregation.

Fake clock, ``.test-tmp-recette-alerts-*`` volumes, local recording transport:
no network, no Discord, no secret.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from fakes.notifications import ManualClock, RecordingTransport
from hermes_trading import paper_history_retention as retention
from scripts import recette_notifications as alerts

ROOT = Path(__file__).resolve().parents[1]
BELOW = alerts.ALERT_BELOW_THRESHOLD
UNAVAILABLE = alerts.ALERT_UNAVAILABLE


def projection(status="projected", days=None):
    return {"schema_version": 1, "status": status, "bytes_per_day": 1000 if days is not None else None,
            "days_before_reserve": days, "sample_days": 3}


def report(proj):
    return {"growth_and_projection": proj, "accounts": {"accounts": {}}}


class ThresholdTests(unittest.TestCase):
    def test_strictly_below_seven_days_alerts_and_seven_does_not(self) -> None:
        self.assertIsNone(alerts.storage_alert(projection(days=7)))
        self.assertIsNone(alerts.storage_alert(projection(days=8)))
        for days in (6, 1, 0):
            with self.subTest(days=days):
                raised = alerts.storage_alert(projection(days=days))
                self.assertEqual(raised["type"], BELOW)
                self.assertEqual(raised["days_before_reserve"], days)

    def test_boundary_follows_the_real_projection_formula(self) -> None:
        # headroom / bytes_per_day just below 7 (6.999...) alerts; exactly 7 does not.
        per_day = 86_400
        for headroom, expected in ((7 * per_day - 1, BELOW), (7 * per_day, None)):
            with self.subTest(headroom=headroom):
                days = headroom // per_day
                raised = alerts.storage_alert(projection(days=days))
                self.assertEqual(raised["type"] if raised else None, expected)

    def test_reserve_reached_is_zero_days_and_alerts(self) -> None:
        raised = alerts.storage_alert(projection("reserve_reached", 0))
        self.assertEqual((raised["type"], raised["projection_status"]), (BELOW, "reserve_reached"))

    def test_quiet_statuses_do_not_alert(self) -> None:
        for status in ("insufficient_data", "non_positive_growth"):
            with self.subTest(status=status):
                self.assertIsNone(alerts.storage_alert(projection(status)))

    def test_unavailable_or_malformed_projection_raises_the_unavailable_type(self) -> None:
        cases = (projection("unavailable"), projection("projected", None), projection("projected", -1),
                 projection("projected", 3.5), projection("projected", True), projection("bogus", 2),
                 None, "projected", {})
        for value in cases:
            with self.subTest(value=value):
                raised = alerts.storage_alert(value)
                self.assertEqual(raised["type"], UNAVAILABLE)
                self.assertIn(raised["projection_status"], {"unavailable", "projected", "unrecognized"})

    def test_projection_contract_statuses_are_all_classified(self) -> None:
        # Every status the retention contract can publish is either quiet or alerting.
        statuses = {"projected", "non_positive_growth", "insufficient_data", "reserve_reached", "unavailable"}
        self.assertEqual(alerts.MEASURED_STATUSES | alerts.QUIET_STATUSES | {"unavailable"}, statuses)
        self.assertEqual(retention.PROJECTION_SCHEMA, 1)


class AggregationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.volume = Path(tempfile.mkdtemp(prefix=".test-tmp-recette-alerts-", dir=ROOT))
        self.addCleanup(shutil.rmtree, self.volume, ignore_errors=True)
        self.clock = ManualClock()
        self.transport = RecordingTransport()
        self.notifier = alerts.RecetteNotifier(self.volume, transport=self.transport)

    def alerts_of(self, messages, kind=None):
        return [m for m in messages if m["kind"] == "alert" and (kind is None or m["type"] == kind)]

    def step(self, proj, seconds=0, notifier=None):
        self.clock.advance(seconds)
        return self.alerts_of((notifier or self.notifier).process(report(proj), self.clock()))

    def test_first_occurrence_is_emitted_then_suppressed_until_fifteen_minutes(self) -> None:
        first = self.step(projection(days=5))
        self.assertEqual([(m["type"], m["suppressed"], m["days_before_reserve"]) for m in first],
                         [(BELOW, 0, 5)])
        for _ in range(4):
            self.assertEqual(self.step(projection(days=5), 60), [])
        # 4 + 10*60 = 14 min 59 s after the emission: still inside the delay.
        self.assertEqual(self.step(projection(days=5), 659), [])
        self.assertEqual(self.notifier.state["alerts"][BELOW]["pending_suppressed"], 5)
        # Exactly fifteen minutes after the first emission: the delay is over.
        emitted = self.step(projection(days=4), 1)
        self.assertEqual([(m["suppressed"], m["reason"], m["days_before_reserve"]) for m in emitted],
                         [(5, "occurrence", 4)])
        counters = self.notifier.summary()["alerts"][BELOW]
        self.assertEqual(counters, {"active": True, "occurrences": 7, "emitted": 2, "suppressed_total": 5,
                                    "pending_suppressed": 0, "last_emitted_at": self.clock().isoformat()})

    def test_suppressed_count_is_flushed_once_the_condition_stops(self) -> None:
        self.step(projection(days=2))
        self.step(projection(days=2), 300)
        self.step(projection(days=2), 300)
        self.assertEqual(self.step(projection(days=30), 299), [])
        flushed = self.step(projection(days=30), 1)
        self.assertEqual([(m["type"], m["suppressed"], m["reason"]) for m in flushed],
                         [(BELOW, 2, "suppressed_flush")])
        self.assertEqual(self.step(projection(days=30), 3600), [])
        counters = self.notifier.summary()["alerts"][BELOW]
        self.assertEqual((counters["active"], counters["emitted"], counters["suppressed_total"],
                          counters["pending_suppressed"]), (False, 2, 2, 0))

    def test_types_are_isolated(self) -> None:
        self.step(projection(days=3))
        # The other type is not delayed by the first one.
        other = self.step(projection("unavailable"), 60)
        self.assertEqual([(m["type"], m["suppressed"]) for m in other], [(UNAVAILABLE, 0)])
        self.step(projection("unavailable"), 60)
        self.step(projection(days=3), 60)
        self.step(projection(days=3), 60)
        summary = self.notifier.summary()["alerts"]
        self.assertEqual((summary[BELOW]["emitted"], summary[BELOW]["pending_suppressed"]), (1, 2))
        self.assertEqual((summary[UNAVAILABLE]["emitted"], summary[UNAVAILABLE]["pending_suppressed"]), (1, 1))
        # 15 min after its own emission each type releases its own count only.
        below = self.step(projection(days=3), 660)
        self.assertEqual([(m["type"], m["suppressed"]) for m in below], [(BELOW, 2)])
        unavailable = self.step(projection(days=3), 60)
        self.assertEqual([(m["type"], m["suppressed"], m["reason"]) for m in unavailable],
                         [(UNAVAILABLE, 1, "suppressed_flush")])

    def test_restart_keeps_the_delay_and_the_exact_pending_count(self) -> None:
        self.step(projection(days=1))
        self.step(projection(days=1), 120)
        self.step(projection(days=1), 120)
        restarted = alerts.RecetteNotifier(self.volume, transport=self.transport)
        self.assertEqual(restarted.state_status, "resumed")
        self.assertEqual(self.step(projection(days=1), 120, restarted), [])
        emitted = self.step(projection(days=1), 540, restarted)
        self.assertEqual([m["suppressed"] for m in emitted], [3])
        summary = restarted.summary()
        self.assertEqual(summary["restarts"], 1)
        self.assertEqual(summary["alerts"][BELOW]["suppressed_total"], 3)
        self.assertEqual(summary["alerts"][BELOW]["emitted"], 2)

    def test_corrupt_state_restarts_fresh_and_says_so(self) -> None:
        self.step(projection(days=1))
        (self.volume / alerts.STATE_NAME).write_text("{not json", encoding="utf-8")
        restarted = alerts.RecetteNotifier(self.volume)
        self.assertEqual(restarted.state_status, "reset_invalid")
        # A lost delay can repeat an alert, never silence it.
        self.assertEqual([m["suppressed"] for m in self.step(projection(days=1), 60, restarted)], [0])
        self.assertEqual(restarted.summary()["state"], "reset_invalid")

    def test_clock_moving_backwards_never_silences_an_alert(self) -> None:
        self.step(projection(days=1))
        self.assertEqual([m["type"] for m in self.step(projection(days=1), -3600)], [BELOW])

    def test_state_is_persisted_before_the_transport_and_failures_are_counted(self) -> None:
        seen = []

        class Probe:
            def send(probe, message):
                stored = json.loads((self.volume / alerts.STATE_NAME).read_text(encoding="utf-8"))
                seen.append(message["sequence"] in [m["sequence"] for m in stored["outbox"]])
                raise ConnectionError("down")

        notifier = alerts.RecetteNotifier(self.volume, transport=Probe())
        messages = notifier.process(report(projection(days=2)), self.clock())
        self.assertEqual([m["kind"] for m in messages], ["alert", "balances"])
        self.assertEqual(seen, [True, True])
        self.assertEqual(notifier.summary()["transport_errors"], 2)
        stored = json.loads((self.volume / alerts.STATE_NAME).read_text(encoding="utf-8"))
        self.assertEqual(stored["transport_errors"], 2)

    def test_outbox_is_bounded_and_state_holds_no_private_data(self) -> None:
        for index in range(alerts.OUTBOX_LIMIT + 10):
            self.step(projection(days=1), 900)
        stored = json.loads((self.volume / alerts.STATE_NAME).read_text(encoding="utf-8"))
        self.assertEqual(len(stored["outbox"]), alerts.OUTBOX_LIMIT)
        self.assertNotIn(str(self.volume), json.dumps(stored))
        self.assertEqual(stored["sequence"], stored["outbox"][-1]["sequence"])

    def test_linked_state_is_refused(self) -> None:
        target = self.volume / "elsewhere.json"
        target.write_text("{}", encoding="utf-8")
        (self.volume / alerts.STATE_NAME).symlink_to(target)
        notifier = alerts.RecetteNotifier(self.volume)
        self.assertEqual(notifier.state_status, "reset_invalid")
        with self.assertRaises(ValueError):
            notifier.process(report(projection(days=1)), self.clock())
        self.assertEqual(target.read_text(encoding="utf-8"), "{}")


if __name__ == "__main__":
    unittest.main()
