"""Paper reporting checks: all transports and observations are synthetic."""
import asyncio
import copy
import json
import tempfile
import threading
import time
import subprocess
import sys
import textwrap
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import localcontext
from pathlib import Path
from unittest.mock import patch

import httpx

from hermes_trading import notifications


NOW = datetime(2026, 10, 24, 10, tzinfo=timezone.utc)
NAMES = tuple(f"{arm}_{size}" for size in (50, 100, 150, 200) for arm in ("adaptive", "control"))
LABELS = {
    "adaptive_50": "Adaptive €50", "control_50": "Control €50",
    "adaptive_100": "Adaptive €100", "control_100": "Control €100",
    "adaptive_150": "Adaptive €150", "control_150": "Control €150",
    "adaptive_200": "Adaptive €200 · PRIMARY", "control_200": "Control €200",
}


def identity():
    return {"experiment_id": "synthetic-paper", "opening_at": NOW.isoformat(),
            "opening_hash": "opening", "manifest_hash": "manifest", "primary_wallet_eur": 200,
            "account_hashes": {name: "hash-" + name for name in NAMES}}


def settings(**kwargs):
    base = notifications.NotificationSettings(True, "discord", "https://discord.com/api/webhooks/1/fake",
        "Synthetic", "pnl_only", True, True, True, True, 5, ("12h",), ("1d", "1w"))
    return replace(base, **kwargs)


def envelope(number=1, at=NOW):
    stamp = at.isoformat()
    result = {key: identity()[key] for key in ("experiment_id", "opening_at", "opening_hash", "manifest_hash")}
    result.update(schema_version=1, kind="settled", at=stamp, observed_at=stamp,
        batch_id=f"batch-{number}", input_hash=f"input-{number}", completed_batches=number,
        status="healthy", entry_admission="enabled", model_ready={"success": True, "status": "not_due"},
        strategy_version="v1", error=None, accounts={})
    for name in NAMES:
        size = int(name.split("_")[1])
        result["accounts"][name] = {"account_hash": "hash-" + name, "finalized": True,
            "observed_at": stamp, "observation": {"currency": "EUR", "at": stamp, "account_config_hash": "hash-" + name,
                "net_equity": str(size + (2 if name.startswith("adaptive") else 0)),
                "closed_net_pnl": "1.25", "open_basis": "20", "open_liquidation_value": "20.75",
                "open_entry_fees": "0.10", "known_accrued_costs": "0.20",
                "cost_provenance": "actual", "missing": []},
            "decisions": [{"decision_id": "d-" + name, "asset": "BTC/EUR", "action": "no_trade", "reason": "threshold"}],
            "closes": [], "orders": [], "halted": False, "liquidation_pending": False,
            "event_source_status": "observed"}
    return result


def close(position="position-1", pnl="3.40"):
    return {"account": "adaptive_200", "asset": "BTC/EUR", "position_id": position,
            "closed_at": NOW.isoformat(), "quantity": "0.002", "realized_pnl_eur": pnl}


async def eventually(predicate, timeout=1):
    async def spin():
        while not predicate():
            await asyncio.sleep(0.001)
    await asyncio.wait_for(spin(), timeout)


class PaperNotificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sent = []
        self.now = NOW
        # A mistaken production transport must fail before any network access.
        self.guard = patch.object(httpx.HTTPTransport, "handle_request", side_effect=AssertionError("real HTTP forbidden"))
        self.guard.start()
        self.addCleanup(self.guard.stop)
        self.async_guard = patch.object(httpx.AsyncHTTPTransport, "handle_async_request", side_effect=AssertionError("real HTTP forbidden"))
        self.async_guard.start()
        self.addCleanup(self.async_guard.stop)

    async def poster(self, url, payload):
        self.sent.append((url, copy.deepcopy(payload)))

    def reporter(self, config=None, **kwargs):
        cls = getattr(notifications, "PaperDiscordReporter", None)
        self.assertIsNotNone(cls, "native paper reporter is missing")
        return cls(self.root, identity(), config or settings(), post_json=kwargs.pop("post_json", self.poster),
                   clock=lambda: self.now, **kwargs)

    async def start(self, reporter):
        task = asyncio.create_task(reporter.run())
        async def stop():
            task.cancel()
            try:
                await asyncio.wait_for(task, .5)
            except asyncio.CancelledError:
                pass
        self.addAsyncCleanup(stop)
        await asyncio.sleep(0)
        return task

    def text(self):
        return "\n".join(payload["content"] for _, payload in self.sent)

    async def test_offer_has_no_io_and_disabled_or_invalid_config_has_no_delivery(self):
        for cfg in (settings(enabled=False), settings(webhook_url=None), settings(service="invalid"), settings(mode="invalid")):
            reporter = self.reporter(cfg)
            with patch.object(Path, "read_text", side_effect=AssertionError("offer read")), patch.object(Path, "write_text", side_effect=AssertionError("offer write")):
                reporter.offer(envelope())
            await asyncio.wait_for(reporter.run(), .1)
        self.assertEqual(self.sent, [])
        self.assertEqual(list(self.root.iterdir()), [])

    async def test_startup_uses_eight_native_eur_rows_and_all_four_cohort_differences(self):
        reporter = self.reporter()
        reporter.offer(envelope())
        await self.start(reporter)
        await eventually(lambda: reporter._state.get("startup") is True)
        text = self.text()
        for name in NAMES:
            self.assertIn(LABELS[name], text)
        for size in (50, 100, 150, 200):
            self.assertIn(f"- **adaptive minus control {size}:** EUR 2.00", text)
        self.assertIn("**Wallet:** EUR 202.00", text)
        self.assertIn("**Closed P&L:** EUR 1.25", text)
        self.assertIn("primary EUR200", text)
        self.assertIn("paper/simulated", text)
        self.assertIn("not_due", text)
        self.assertIn("review audit unavailable", text)
        self.assertNotIn("Sharpe=0", text)

    async def test_bullets_put_primary_wallet_pnl_and_costs_before_other_wallets(self):
        value = envelope()
        primary = value["accounts"]["adaptive_200"]["observation"]
        primary.update(closed_net_pnl="-1.375", known_accrued_costs="0.00072")
        reporter = self.reporter(); reporter.offer(value); await self.start(reporter)
        await eventually(lambda: reporter.status == "delivered")
        text = self.text()
        wallet_lines = [line for line in text.splitlines() if line.startswith("- **") and "**Wallet:**" in line]
        self.assertEqual(len(wallet_lines), 8)
        self.assertIn("Adaptive €200 · PRIMARY:", wallet_lines[0])
        self.assertIn("PRIMARY", wallet_lines[0])
        self.assertIn("**Wallet:** EUR 202.00", wallet_lines[0])
        self.assertIn("**Closed P&L:** EUR -1.38", wallet_lines[0])
        self.assertIn("**Known costs:** EUR +<0.01", wallet_lines[0])
        self.assertEqual({label for label in LABELS.values() if any(label + ":" in line for line in wallet_lines)}, set(LABELS.values()))
        self.assertFalse(any(name in line for name in NAMES for line in wallet_lines))
        self.assertLess(text.index(wallet_lines[-1]), text.index("**Open:**"))
        self.assertLess(text.index(wallet_lines[0]), text.index("**Last settled:**"))
        self.assertIn("**Open:** cost EUR 20", text)
        self.assertIn("net sell value EUR 20.75", text)
        self.assertIn("entry fees EUR 0.10", text)
        self.assertIn("Closed P&L is cumulative closed-trade P&L", text)
        self.assertIn("current financial snapshot", text)
        self.assertIn("not a daily/30-day return", text)
        for _, payload in self.sent:
            self.assertEqual(payload["content"].count("- **Observed at:** " + NOW.isoformat()), 1)

    async def test_operational_bullets_keep_entry_pause_model_context_and_actual_time(self):
        reporter = self.reporter(); reporter.offer(envelope()); await self.start(reporter)
        await eventually(lambda: reporter.status == "delivered")
        self.sent.clear()
        self.now += timedelta(minutes=1)
        value = envelope()
        value.update(kind="operational", at=self.now.isoformat(), status="running", accounts={},
                     entry_admission={"enabled": True, "blocked": True, "reasons": ["supervisor_error_halt"]})
        reporter.offer(value)
        await eventually(lambda: bool(self.sent))
        text = self.text()
        self.assertIn("**Operational state change**", text)
        self.assertIn("**Worker:** running", text)
        self.assertIn("**New entries:** paused", text)
        self.assertIn("supervisor_error_halt", text)
        self.assertIn("not_due", text)
        self.assertIn("review audit unavailable", text)
        self.assertIn("- **Observed at:** " + self.now.isoformat(), text)
        self.assertIn("- **Last settled:** " + NOW.isoformat(), text)
        self.assertNotIn("- **Observed at:** unavailable", text)
        self.assertEqual(text.count("**New entries:**"), 1)
        self.assertEqual(text.count("**Worker:**"), 1)
        self.assertEqual(text.count("**Model access:**"), 1)
        self.assertNotIn('{"success":', text)
        self.assertNotIn("entry_admission=", text)

    def test_model_access_boolean_and_missing_status_are_plain_and_not_review_outcomes(self):
        reporter = self.reporter()
        for ready, display in ((True, "available"), (False, "unavailable"), (None, "unknown")):
            with self.subTest(ready=ready):
                rendered = "\n".join(reporter._status_lines({"model_ready": ready}))
                self.assertIn("**Model access:** " + display, rendered)
                self.assertIn("review audit unavailable", rendered)
                self.assertNotIn("True", rendered)
                self.assertNotIn("False", rendered)

    async def test_close_bullets_show_canonical_money_quantity_and_distinct_timestamps(self):
        reporter = self.reporter(); reporter.offer(envelope()); await self.start(reporter)
        await eventually(lambda: reporter._state.get("startup") is True)
        self.sent.clear()
        at = NOW + timedelta(seconds=3)
        value = envelope(2, at)
        value["accounts"]["adaptive_200"]["closes"] = [close(pnl="-0.00072")]
        self.now = at
        reporter.offer(value)
        await eventually(lambda: bool(reporter._state.get("events")))
        text = self.text()
        close_line = next(line for line in text.splitlines() if line.startswith("- **Close"))
        self.assertIn("Adaptive €200 · PRIMARY", close_line)
        self.assertIn("BTC/EUR", close_line)
        self.assertIn("**Realized P&L:** EUR -<0.01", close_line)
        self.assertNotIn("**Quantity:**", close_line)
        self.assertNotIn("adaptive_200", close_line)
        self.assertIn("**Realized P&L:** EUR -<0.01", text)
        self.assertIn("  - **Quantity:** 0.002", text)
        self.assertIn("**Closed at:** " + NOW.isoformat(), text)
        self.assertIn("- **Observed at:** " + at.isoformat(), text)
        self.assertIn("**Position:** position-1", text)
        self.assertIn("[event:", text)
        self.assertIn("Display rounded to cents", text)

    async def test_money_display_rounds_precision_without_hiding_tiny_values_or_mutating_input(self):
        value = envelope()
        value["accounts"]["adaptive_200"]["observation"].update(
            net_equity="199.1234567890123456789", closed_net_pnl="-0.0042",
            open_basis="1E+40", open_liquidation_value="20.755555555555555555",
            open_entry_fees="0.0000000000000000000001", known_accrued_costs=None)
        original = copy.deepcopy(value)
        reporter = self.reporter(); reporter.offer(value); await self.start(reporter)
        await eventually(lambda: reporter._state.get("startup") is True)
        text = self.text()
        line = next(line for line in text.splitlines() if "**Wallet:**" in line)
        self.assertIn("**Wallet:** EUR 199.12 |", line)
        self.assertIn("**Closed P&L:** EUR -<0.01", line)
        self.assertIn("**Known costs:** unknown", line)
        self.assertIn("net sell value EUR 20.76", text)
        self.assertIn("entry fees EUR +<0.01", text)
        self.assertIn("Display rounded to cents", text)
        self.assertNotIn("199.1234567890123456789", text)
        self.assertEqual(value, original)
        with localcontext() as context:
            context.prec = 2
            self.assertEqual(notifications._paper_money("1E+40"),
                             "EUR 10000000000000000000000000000000000000000.00")
            self.assertEqual(notifications._paper_money("-0.009999999999999999999999999999"), "EUR -<0.01")
            self.assertEqual(notifications._paper_money("-0"), "EUR 0.00")
            self.assertEqual(notifications._paper_money(None), "unknown")

    def test_entry_pause_reasons_have_plain_labels_without_losing_native_codes(self):
        reporter = self.reporter()
        for code, label in (("supervisor_error_halt", "repeated supervisor errors"),
                            ("supervisor_health_stale", "supervision out of date"),
                            ("daily_backup_failed", "daily backup failed")):
            with self.subTest(code=code):
                rendered = "\n".join(reporter._status_lines({"status": "running", "model_ready": True,
                    "entry_admission": {"blocked": True, "reasons": [code]}}))
                self.assertIn("**New entries:** paused", rendered)
                self.assertIn(label + " (" + code + ")", rendered)
        unknown = "unrecognized_@everyone" * 30
        rendered = "\n".join(reporter._status_lines({"status": "running",
            "entry_admission": {"blocked": True, "reasons": [unknown]}}))
        self.assertIn("unrecognized_", rendered)
        self.assertNotIn("@everyone", rendered)
        self.assertLess(len(rendered), 400)

    async def test_identity_and_row_mismatches_never_present_unrelated_money(self):
        reporter = self.reporter()
        invalid = envelope(); invalid["opening_hash"] = "other"
        reporter.offer(invalid)
        await self.start(reporter)
        await asyncio.sleep(.015)
        self.assertEqual(self.sent, [])
        valid = envelope()
        valid["accounts"]["adaptive_200"]["account_hash"] = "other"
        valid["accounts"]["control_50"]["observed_at"] = "old"
        reporter.offer(valid)
        await eventually(lambda: reporter._state.get("startup") is True)
        self.assertIn("Adaptive €200 · PRIMARY:** unavailable", self.text())
        self.assertIn("Control €50:** unavailable", self.text())
        self.assertIn("- **adaptive minus control 200:** unavailable", self.text())

    async def test_missing_costs_and_null_observation_remain_unknown(self):
        value = envelope()
        obs = value["accounts"]["adaptive_200"]["observation"]
        obs.update(known_accrued_costs=None, cost_provenance="missing", missing=["exit_fee"])
        value["accounts"]["control_50"]["observation"].update(
            net_equity=None, known_accrued_costs="0", missing=["cost:hosting"],
            cost_provenance={"hosting": {"quality": "missing", "amount": None}})
        value["accounts"]["control_100"]["observation"] = None
        value["accounts"]["control_150"]["event_source_status"] = "unavailable"
        reporter = self.reporter(); reporter.offer(value); await self.start(reporter)
        await eventually(lambda: reporter._state.get("startup") is True)
        self.assertIn("**Known costs:** unknown", self.text())
        subtotal = next(line for line in self.text().splitlines() if "Control €50:" in line and "**Wallet:**" in line)
        self.assertIn("**Wallet:** unknown", subtotal)
        self.assertIn("**Known costs:** EUR 0.00", subtotal)
        self.assertNotIn("**Costs:**", subtotal)
        self.assertIn("Known costs are a recorded subtotal", self.text())
        self.assertIn("hosting:missing", self.text())
        self.assertIn("exit_fee", self.text())
        self.assertIn("Control €100:** unavailable", self.text())
        self.assertIn("events: unavailable", self.text())

    async def test_modes_use_canonical_close_money_and_suppress_duplicate_events(self):
        for mode, want_close, want_order in (("pnl_only", True, False), ("orders_only", False, True), ("verbose", True, True)):
            with self.subTest(mode=mode):
                self.root = Path(self.tmp.name) / mode
                self.root.mkdir()
                self.sent.clear()
                reporter = self.reporter(settings(mode=mode))
                value = envelope()
                value["accounts"]["adaptive_200"]["closes"] = [close(), close()]
                value["accounts"]["adaptive_200"]["orders"] = [{"canonical_id": "order-1", "asset": "BTC/EUR", "side": "buy", "quantity": "0.002", "status": "simulated"}]
                reporter.offer(value); task = await self.start(reporter)
                await eventually(lambda: reporter._state.get("startup") is True)
                await asyncio.sleep(.025)
                self.assertEqual("**Realized P&L:** EUR 3.40" in self.text(), want_close)
                self.assertEqual("Order simulated" in self.text(), want_order)
                if want_order:
                    self.assertIn("Order simulated** · Adaptive €200 · PRIMARY", self.text())
                if want_close:
                    self.assertEqual(self.text().count("**Realized P&L:** EUR 3.40"), 1)
                count = len(self.sent)
                reporter.offer(value); await asyncio.sleep(.025)
                self.assertEqual(len(self.sent), count)
                task.cancel()

    async def test_five_new_batches_only_and_utc_schedules_are_current_snapshots(self):
        reporter = self.reporter(); await self.start(reporter)
        reporter.offer(envelope()); await eventually(lambda: reporter._state.get("startup") is True)
        for i in (2, 2, 3, 4):
            reporter.offer(envelope(i)); await asyncio.sleep(.01)
        self.assertNotIn("5 batches", self.text())
        reporter.offer(envelope(5)); await eventually(lambda: "5 batches" in self.text())
        self.now = NOW.replace(hour=12)
        op = envelope(5); op.update(kind="operational", at=self.now.isoformat(), accounts={})
        reporter.offer(op)
        await eventually(lambda: "12h" in self.text())
        self.now = datetime(2026, 10, 26, 0, tzinfo=timezone.utc)
        op["at"] = self.now.isoformat(); reporter.offer(op)
        await eventually(lambda: "1w" in self.text())
        self.assertIn("1d", self.text())
        self.assertIn("current financial snapshot", self.text())
        self.assertIn("period-end valuation unavailable", self.text())

    async def test_heartbeat_fallback_and_state_changes_do_not_count_financial_batches(self):
        reporter = self.reporter(settings(summary_every_cycles=0, summary_frequencies=(), profitability_frequencies=()))
        reporter.offer(envelope()); await self.start(reporter)
        await eventually(lambda: reporter._state.get("startup") is True); count = len(self.sent)
        self.now += timedelta(minutes=1)
        op = envelope(); op.update(kind="operational", at=self.now.isoformat(), accounts={})
        reporter.offer(op); await asyncio.sleep(.015)
        self.assertEqual(len(self.sent), count)
        self.now += timedelta(minutes=14); op["at"] = self.now.isoformat(); reporter.offer(op)
        await eventually(lambda: "15-minute liveness" in self.text())
        op["entry_admission"] = "halted"; reporter.offer(op)
        await eventually(lambda: "**New entries:** halted" in self.text())
        self.assertNotIn("5 batches", self.text())

    async def test_default_http_confirmation_preserves_thread_and_disables_mentions(self):
        requests = []
        async def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"id": "message-confirmed"})
        original = httpx.AsyncClient
        def client(**kwargs):
            self.assertFalse(kwargs.get("follow_redirects", False))
            return original(transport=httpx.MockTransport(handler), **kwargs)
        reporter = self.reporter(settings(webhook_url="https://discord.com/api/webhooks/1/fake?thread_id=42&wait=false"))
        reporter._post_json = None
        with patch.object(httpx, "AsyncClient", side_effect=client):
            reporter.offer(envelope()); task = await self.start(reporter)
            await eventually(lambda: len(requests) >= 2)
            await eventually(lambda: list(self.root.glob("paper_discord_reporter.json")))
            task.cancel()
        self.assertEqual(requests[0].url.params["wait"], "true")
        self.assertEqual(requests[0].url.params["thread_id"], "42")
        self.assertEqual(json.loads(requests[0].content)["allowed_mentions"], {"parse": []})

    async def test_delivery_markers_require_confirmation_and_restart_suppresses_replay(self):
        reporter = self.reporter(); reporter.offer(envelope()); task = await self.start(reporter)
        await eventually(lambda: reporter._state.get("startup") is True)
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass
        metadata = self.root / "paper_discord_reporter.json"
        self.assertLessEqual(metadata.stat().st_size, 65536)
        self.assertNotIn("fake", metadata.read_text())
        self.assertTrue(json.loads(metadata.read_text())["delivered"])
        self.sent.clear()
        replay = self.reporter(); replay.offer(envelope()); await self.start(replay)
        await asyncio.sleep(.03)
        self.assertEqual(self.sent, [])

    async def test_invalid_or_oversized_input_is_isolated(self):
        reporter = self.reporter(); await self.start(reporter)
        for value in ({}, envelope() | {"accounts": {"bad": {}}}, envelope() | {"error": "x" * 140000}):
            reporter.offer(value)
        await asyncio.sleep(.02)
        self.assertEqual(self.sent, [])
        reporter.offer(envelope()); await eventually(lambda: reporter._state.get("startup") is True)

    async def test_timeout_is_bounded_uncertain_and_never_marks_success(self):
        attempts = []
        async def stalled(url, payload):
            attempts.append(payload["content"])
            await asyncio.Event().wait()
        async def no_wait(delay):
            await asyncio.sleep(0)
        reporter = self.reporter(post_json=stalled, sleep=no_wait, attempt_timeout=.15)
        reporter.offer(envelope()); await self.start(reporter)
        await eventually(lambda: len(attempts) == 3)
        await eventually(lambda: reporter.counters["delivery_unknown"] == 3)
        data = json.loads((self.root / "paper_discord_reporter.json").read_text())
        self.assertEqual(data["delivered"], [])
        self.assertEqual(len(set(attempts)), 1, "retry tags/content must be stable")
        self.assertEqual(reporter.status, "delivery_unknown")
        await asyncio.sleep(.03)
        self.assertEqual(len(attempts), 3)

    async def test_rate_limit_honors_delay_and_partial_success_is_not_resent(self):
        calls, delays = [], []
        async def limited(url, payload):
            calls.append(payload["content"])
            if len(calls) == 2:
                response = httpx.Response(429, headers={"Retry-After": "0.125"}, json={"retry_after": .25}, request=httpx.Request("POST", url))
                raise httpx.HTTPStatusError("synthetic", request=response.request, response=response)
        async def pause(delay):
            delays.append(delay); await asyncio.sleep(0)
        reporter = self.reporter(post_json=limited, sleep=pause)
        reporter.offer(envelope()); await self.start(reporter)
        await eventually(lambda: reporter.status == "delivered")
        self.assertEqual(delays, [.125])
        self.assertEqual(calls.count(calls[0]), 1)
        self.assertEqual(calls[1], calls[2])
        self.assertEqual(reporter.counters["delivery_unknown"], 0)

    async def test_permanent_http_error_disables_delivery_with_no_retry(self):
        calls = []
        async def denied(url, payload):
            calls.append(payload)
            response = httpx.Response(401, request=httpx.Request("POST", url))
            raise httpx.HTTPStatusError("must not expose webhook", request=response.request, response=response)
        reporter = self.reporter(post_json=denied)
        reporter.offer(envelope()); task = await self.start(reporter)
        await eventually(task.done)
        self.assertFalse(reporter.enabled)
        self.assertEqual(len(calls), 1)
        data = json.loads((self.root / "paper_discord_reporter.json").read_text())
        self.assertEqual(data["delivered"], [])
        self.assertIsNone(data["pending"])
        self.assertNotIn("fake", reporter.status)

    async def test_metadata_corruption_or_write_failure_isolated_before_dispatch(self):
        metadata = self.root / "paper_discord_reporter.json"
        metadata.write_text("{corrupt", encoding="utf-8")
        reporter = self.reporter(); reporter.offer(envelope()); await reporter.run()
        self.assertEqual(self.sent, [])
        self.assertEqual(reporter.status, "metadata_invalid")
        metadata.unlink()
        reporter = self.reporter(); reporter.offer(envelope())
        with patch.object(Path, "replace", side_effect=PermissionError("synthetic")):
            await asyncio.wait_for(reporter.run(), .2)
        self.assertEqual(self.sent, [])
        self.assertEqual(reporter.status, "metadata_unavailable")

    async def test_cancellation_after_dispatch_retains_uncertainty_without_success(self):
        entered = asyncio.Event()
        async def stalled(url, payload):
            entered.set(); await asyncio.Event().wait()
        reporter = self.reporter(post_json=stalled)
        reporter.offer(envelope()); task = await self.start(reporter)
        await asyncio.wait_for(entered.wait(), .5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, .1)
        data = json.loads((self.root / "paper_discord_reporter.json").read_text())
        self.assertEqual(data["delivered"], [])
        self.assertIsNotNone(data["pending"])
        self.assertGreater(data["counters"]["delivery_unknown"], 0)

    async def test_queue_overflow_and_coalescing_are_disclosed_while_offer_keeps_progressing(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def stalled(url, payload):
            entered.set(); await release.wait(); await self.poster(url, payload)
        reporter = self.reporter(post_json=stalled)
        reporter.offer(envelope()); await self.start(reporter)
        await asyncio.wait_for(entered.wait(), .5)
        for number in range(2, 42):
            value = envelope(number)
            value["accounts"]["adaptive_200"]["closes"] = [close(f"p{number}")]
            reporter.offer(value)
        self.assertEqual(len(reporter._events), 16)
        self.assertEqual(reporter.counters["dropped"], 24)
        release.set()
        await eventually(lambda: "dropped=24" in self.text())
        self.assertGreater(reporter.counters["coalesced"], 0)

    async def test_bounded_parts_have_simulation_timestamp_and_mention_suppression(self):
        value = envelope()
        value["accounts"]["adaptive_200"]["closes"] = [close(f"p{i}@everyone") for i in range(120)]
        reporter = self.reporter(); reporter.offer(value); await self.start(reporter)
        await eventually(lambda: reporter.status == 'delivered')
        groups = {}
        for _, payload in self.sent:
            content = payload["content"]
            self.assertLessEqual(len(content), 1900)
            self.assertIn("paper/simulated", content)
            self.assertIn(NOW.isoformat(), content)
            self.assertNotIn("@everyone", content)
            self.assertEqual(payload["allowed_mentions"], {"parse": []})
            tag = content.split("[paper:")[-1].split(" part:")[0]
            groups[tag] = groups.get(tag, 0) + 1
        self.assertTrue(all(count <= 4 for count in groups.values()))
        self.assertIn("Omitted", self.text())

    async def test_unconfirmed_default_response_and_redirect_never_mark_delivery(self):
        original = httpx.AsyncClient
        async def no_wait(delay): await asyncio.sleep(0)
        for code, body in ((204, None), (200, {}), (302, None)):
            with self.subTest(code=code):
                self.root = Path(self.tmp.name) / str(code); self.root.mkdir()
                async def handler(request): return httpx.Response(code, json=body)
                def client(**kwargs): return original(transport=httpx.MockTransport(handler), **kwargs)
                reporter = self.reporter(sleep=no_wait); reporter._post_json = None
                with patch.object(httpx, "AsyncClient", side_effect=client):
                    reporter.offer(envelope()); task = await self.start(reporter)
                    await eventually(lambda: bool(reporter._state.get("abandoned")))
                    task.cancel()
                data = json.loads((self.root / "paper_discord_reporter.json").read_text())
                self.assertEqual(data["delivered"], [])

    async def test_batch_counter_survives_restart_between_summaries(self):
        reporter = self.reporter(); reporter.offer(envelope()); task = await self.start(reporter)
        await eventually(lambda: reporter._state.get("startup") is True)
        for number in (2, 3, 4):
            reporter.offer(envelope(number)); await asyncio.sleep(.01)
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass
        self.sent.clear()
        resumed = self.reporter(); resumed.offer(envelope(5)); await self.start(resumed)
        await eventually(lambda: resumed._state.get('cadences', {}).get('cycle') == '1')
        self.assertIn("5 batches", self.text())
        self.assertIn("coverage unknown", self.text())
        self.assertIn("**Delivery:** gaps=", self.text())

    async def test_stalled_metadata_write_leaves_the_event_loop_available(self):
        entered, release = threading.Event(), threading.Event()
        original = Path.write_text
        def delayed_write(path, *args, **kwargs):
            if path.name == "paper_discord_reporter.tmp":
                entered.set()
                release.wait(.3)
            return original(path, *args, **kwargs)
        reporter = self.reporter(); reporter.offer(envelope())
        try:
            with patch.object(Path, "write_text", delayed_write):
                task = await self.start(reporter)
                await eventually(entered.is_set)
                start = time.monotonic()
                await asyncio.sleep(.02)
                reporter.offer(envelope(2))
                self.assertLess(time.monotonic() - start, .15)
                self.assertEqual(self.sent, [])
                release.set()
                await eventually(lambda: reporter._state.get("startup") is True)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, .5)
        finally:
            release.set()

    async def test_emission_after_settlement_and_native_order_id_are_supported(self):
        value = envelope(); value["at"] = (NOW + timedelta(seconds=2)).isoformat()
        value["accounts"]["adaptive_200"]["orders"] = [{"order_id": "native-1", "client_order_id": "native-client",
            "asset": "BTC/EUR", "side": "sell", "quantity": "0.002", "status": "simulated"}]
        reporter = self.reporter(settings(mode="orders_only")); reporter.offer(value); await self.start(reporter)
        await eventually(lambda: "Order simulated" in self.text())
        await eventually(lambda: reporter._state.get("startup") is True)
        self.assertIn(NOW.isoformat(), self.text())
        self.assertIn((NOW + timedelta(seconds=2)).isoformat(), self.text())

    async def test_error_details_are_never_stored_or_sent_and_interval_is_explicit(self):
        value = envelope(); value["error"] = "secret-token-DO-NOT-RETAIN"
        reporter = self.reporter(); reporter.offer(value); await self.start(reporter)
        await eventually(lambda: reporter.status == "delivered")
        await asyncio.sleep(.025)
        data = (self.root / "paper_discord_reporter.json").read_text()
        self.assertNotIn("secret-token", data)
        self.assertNotIn("secret-token", self.text())
        self.assertIn("Observed interval", self.text())

    async def test_conflicting_replay_is_rejected_without_replacing_snapshot(self):
        reporter = self.reporter(); reporter.offer(envelope()); await self.start(reporter)
        await eventually(lambda: reporter._state.get("startup") is True)
        self.sent.clear()
        conflict = envelope(); conflict["input_hash"] = "other-input"
        conflict["accounts"]["adaptive_200"]["observation"]["net_equity"] = "999999"
        reporter.offer(conflict)
        self.now = NOW.replace(hour=12)
        op = envelope(); op.update(kind="operational", at=self.now.isoformat(), accounts={})
        reporter.offer(op)
        await eventually(lambda: "Control €200" in self.text())
        self.assertNotIn("999999", self.text())
        self.assertIn("**Wallet:** EUR 202", self.text())

    async def test_corrupt_metadata_schedule_type_disables_instead_of_sending(self):
        reporter = self.reporter(); reporter.offer(envelope()); task = await self.start(reporter)
        await eventually(lambda: reporter.status == "delivered")
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass
        metadata = self.root / "paper_discord_reporter.json"
        data = json.loads(metadata.read_text()); data["cadences"]["cycle"] = "corrupt"
        metadata.write_text(json.dumps(data))
        self.sent.clear()
        resumed = self.reporter(); resumed.offer(envelope(5)); task = await self.start(resumed)
        await eventually(task.done)
        self.assertEqual(resumed.status, "metadata_invalid")
        self.assertEqual(self.sent, [])

    async def test_native_observation_identity_and_timestamp_must_match_row(self):
        value = envelope()
        value["accounts"]["adaptive_200"]["observation"]["account_config_hash"] = "other"
        value["accounts"]["control_200"]["observation"]["at"] = "old"
        reporter = self.reporter(); reporter.offer(value); await self.start(reporter)
        await eventually(lambda: reporter._state.get("startup") is True)
        self.assertIn("Adaptive €200 · PRIMARY:** unavailable", self.text())
        self.assertIn("Control €200:** unavailable", self.text())
        self.assertNotIn("**Wallet:** EUR 202", self.text())

    async def test_long_untrusted_cost_details_cannot_remove_any_of_the_eight_rows(self):
        value = envelope()
        for row in value["accounts"].values():
            obs = row["observation"]
            for key in ("net_equity", "closed_net_pnl", "open_basis", "open_liquidation_value", "open_entry_fees", "known_accrued_costs"):
                obs[key] = "7" * 70
            obs["cost_provenance"] = "untrusted" * 100
            obs["missing"] = ["missing" * 30] * 4
            row["decisions"][0]["reason"] = "untrusted" * 100
        reporter = self.reporter(); reporter.offer(value); await self.start(reporter)
        await eventually(lambda: reporter.status == "delivered")
        for name in NAMES:
            self.assertIn(LABELS[name] + ":", self.text())
        self.assertLessEqual(len(self.sent), 4)

    async def test_abandoned_startup_does_not_disable_future_distinct_snapshots(self):
        attempts = []
        async def transient(url, payload):
            attempts.append(payload["content"])
            if len(attempts) <= 3:
                raise OSError("synthetic transient")
            await self.poster(url, payload)
        async def no_wait(delay): await asyncio.sleep(0)
        reporter = self.reporter(post_json=transient, sleep=no_wait)
        reporter.offer(envelope()); await self.start(reporter)
        await eventually(lambda: bool(reporter._state.get("abandoned")))
        reporter.offer(envelope()); await asyncio.sleep(.03)
        self.assertEqual(len(attempts), 3)
        reporter.offer(envelope(2, NOW + timedelta(minutes=1)))
        await eventually(lambda: "Control €200" in self.text())
        self.assertTrue(reporter.enabled)
        self.assertGreaterEqual(reporter.counters["gaps"], 3)

    async def test_all_cost_categories_keep_individual_actual_estimated_missing_quality(self):
        value = envelope()
        for row in value['accounts'].values():
            row['observation']['cost_provenance'] = {
                name: {'source': 'native-' + name, 'quality': quality, 'amount': '0.2'}
                for name,quality in (('ai','actual'),('hosting','estimated'),('tools','actual'),('funding','missing'))}
            row['observation'].update(net_equity=None, missing=['cost:funding'])
        reporter = self.reporter(); reporter.offer(value); await self.start(reporter)
        await eventually(lambda: reporter.status == 'delivered')
        text = self.text()
        for name in NAMES:self.assertIn(LABELS[name] + ':', text)
        self.assertEqual(text.count('hosting:estimated'), 8)
        self.assertEqual(text.count('ai:actual'), 8)
        self.assertEqual(text.count('funding:missing'), 8)
        self.assertLessEqual(len(self.sent), 4)

    async def test_eight_closes_and_due_five_batch_summary_share_one_four_part_envelope(self):
        reporter = self.reporter(); await self.start(reporter); reporter.offer(envelope())
        await eventually(lambda: reporter._state.get('startup') is True)
        for number in (2,3,4):
            reporter.offer(envelope(number))
            await eventually(lambda: reporter._state.get('last_completed') == number)
        self.sent.clear(); value = envelope(5)
        for name,row in value['accounts'].items():
            event = close('native-risk-close-' + name); event['account'] = name
            row['closes'] = [event]
        reporter.offer(value)
        await eventually(lambda: reporter._state['cadences'].get('cycle') == '1')
        self.assertLessEqual(len(self.sent), 4)
        self.assertTrue(all(len(payload['content']) <= 1900 for _,payload in self.sent))
        for name in NAMES:
            self.assertIn(LABELS[name] + ':', self.text())
            self.assertIn('native-risk-close-' + name, self.text())

    async def test_strategy_version_remains_in_first_part_of_full_summary(self):
        reporter = self.reporter(); value = envelope()
        value['strategy_version'] = '83'
        for name,row in value['accounts'].items():
            event = close('native-risk-close-' + name); event['account'] = name
            row['closes'] = [event]
        reporter.offer(value); await self.start(reporter)
        await eventually(lambda: reporter._state.get('startup') is True)
        self.assertIn('**Strategy version:** 83', self.sent[0][1]['content'])
        self.assertLessEqual(len(self.sent), 4)

    async def test_new_envelopes_arriving_during_delivery_keep_their_own_combined_part_bound(self):
        entered = asyncio.Event(); release = asyncio.Event()
        async def blocked(url,payload):
            await self.poster(url,payload)
            if len(self.sent) == 1:entered.set(); await release.wait()
        reporter = self.reporter(post_json=blocked); await self.start(reporter)
        stamps = {}
        def offer(number):
            at = NOW + timedelta(minutes=number); stamps[number] = at.isoformat()
            value = envelope(number,at)
            for name,row in value['accounts'].items():
                event = close(f'position-{number}-{name}'); event.update(account=name,closed_at=at.isoformat())
                row['closes'] = [event]
            reporter.offer(value)
        offer(1); await asyncio.wait_for(entered.wait(), 1)
        for number in (2,3,4,5):offer(number)
        release.set()
        await eventually(lambda: reporter._state['cadences'].get('cycle') == '1')
        for stamp in stamps.values():
            parts = [payload['content'] for _,payload in self.sent if '- **Observed at:** ' + stamp in payload['content']]
            self.assertGreater(len(parts), 0)
            self.assertLessEqual(len(parts), 4)
        self.assertIn('position-5-adaptive_200', self.text())

    async def test_abandoned_combined_envelope_never_falls_back_to_a_second_summary_intent(self):
        async def poster(url,payload):
            if '[event:' in payload['content']:
                response = httpx.Response(500,request=httpx.Request('POST',url))
                response.raise_for_status()
            await self.poster(url,payload)
        async def no_wait(delay):await asyncio.sleep(0)
        reporter = self.reporter(post_json=poster,sleep=no_wait)
        await self.start(reporter); reporter.offer(envelope())
        await eventually(lambda: reporter._state.get('startup') is True)
        for number in (2,3,4):
            reporter.offer(envelope(number))
            await eventually(lambda: reporter._state.get('last_completed') == number)
        self.sent.clear(); value = envelope(5)
        for name,row in value['accounts'].items():
            event = close('abandoned-' + name); event['account'] = name; row['closes'] = [event]
        reporter.offer(value)
        await eventually(lambda: bool(reporter._state.get('abandoned')))
        await asyncio.sleep(.6)
        self.assertEqual(len({p['content'].split('[paper:')[-1].split(' part:')[0] for _,p in self.sent}),1)
        self.assertLessEqual(len(self.sent),4)
        self.assertNotIn('cycle',reporter._state['cadences'])
        self.assertEqual(reporter._state['events'],[])
        # A later input is a new bounded observation, so transient failures must
        # not suppress every future financial summary for this cadence.
        reporter.offer(envelope(6,NOW+timedelta(minutes=1)))
        await eventually(lambda: reporter._state['cadences'].get('cycle') == '1')


class PaperNotificationProcessShutdownTests(unittest.TestCase):
    def test_stalled_metadata_never_keeps_asyncio_run_alive_after_cancellation(self):
        # Native entrypoint uses asyncio.run. A cancelled task alone cannot prove
        # terminal shutdown when default-executor threads are still running.
        script = textwrap.dedent('''
            import asyncio,json,sys,tempfile,time,threading
            from pathlib import Path
            sys.path.insert(0,str(Path('tests').resolve()))
            from test_paper_notifications import identity,settings,envelope
            from hermes_trading.notifications import PaperDiscordReporter
            entered=threading.Event()
            async def main(root,operation):
                async def fake_post(*args):pass
                reporter=PaperDiscordReporter(root,identity(),settings(),post_json=fake_post)
                original=getattr(reporter,operation)
                def stalled():
                    entered.set();time.sleep(3)
                    return original()
                setattr(reporter,operation,stalled)
                reporter.offer(envelope())
                task=asyncio.create_task(reporter.run())
                while not entered.is_set():await asyncio.sleep(.001)
                task.cancel()
                try:await task
                except asyncio.CancelledError:pass
            with tempfile.TemporaryDirectory() as root:
                tick=time.monotonic();asyncio.run(main(root,sys.argv[1]))
                print(json.dumps({'elapsed':time.monotonic()-tick}))
        ''')
        for operation in ('_load','_persist'):
            with self.subTest(operation=operation):
                result = subprocess.run([sys.executable,'-B','-X','utf8','-c',script,operation],
                    capture_output=True,text=True,timeout=8,check=True)
                self.assertLess(json.loads(result.stdout)['elapsed'], 2)


if __name__ == "__main__":
    unittest.main()
