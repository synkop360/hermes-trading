import json
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from hermes_trading.market_capture import CaptureJournal, parse_ticker


def payload(**changes):
    value = dict(feed="ticker", product_id="PF_SOLUSD", time=1000,
                 bid=100, ask=101, bid_size=2, ask_size=3,
                 suspended=False, post_only=False, next_funding_rate_time=3600000,
                 funding_rate="0.001", relative_funding_rate="0.00001")
    value.update(changes)
    return json.dumps(value)


class CaptureTests(unittest.TestCase):
    def test_quotes_preserve_units_and_age(self):
        tick = parse_ticker(payload(), received_ns=1_100_000_000)
        self.assertEqual(tick.funding_per_unit, Decimal("0.001"))
        self.assertEqual(tick.relative_funding, Decimal("0.00001"))
        self.assertEqual(tick.age_ms, Decimal("100"))
        self.assertTrue(tick.usable(max_age_ms=1000))
        self.assertFalse(tick.usable(max_age_ms=50))

    def test_omitted_documented_zero_rates_are_explicit(self):
        d = json.loads(payload())
        del d["funding_rate"]
        del d["relative_funding_rate"]
        tick = parse_ticker(json.dumps(d), received_ns=1_100_000_000)
        self.assertEqual(tick.funding_per_unit, 0)
        self.assertTrue(tick.funding_omitted)
        del d["bid_size"]
        with self.assertRaises(ValueError):
            parse_ticker(json.dumps(d), received_ns=1_100_000_000)

    def test_bad_numeric_or_incomplete_messages_fail(self):
        for change in ({"bid": "NaN"}, {"ask": 99}, {"bid_size": -1},
                       {"time": True}, {"funding_rate": "Infinity"},
                       {"suspended": "false"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                parse_ticker(payload(**change), received_ns=1_100_000_000)

    def test_suspended_future_or_empty_depth_is_not_usable(self):
        for change in ({"suspended": True}, {"post_only": True},
                       {"time": 5000}, {"ask_size": 0}):
            tick = parse_ticker(payload(**change), received_ns=1_100_000_000)
            self.assertFalse(tick.usable(max_age_ms=1000))

    def test_control_events_are_not_quotes(self):
        self.assertIsNone(parse_ticker('{"event":"subscribed","feed":"ticker"}',
                                       received_ns=1_100_000_000))

    def test_funding_signs_must_agree(self):
        with self.assertRaises(ValueError):
            parse_ticker(payload(relative_funding_rate="-0.00001"),
                         received_ns=1_100_000_000)

    def test_wire_decimal_precision_is_retained(self):
        raw = payload().replace('"0.001"', '0.001234567890123456789')
        tick = parse_ticker(raw, received_ns=1_100_000_000)
        self.assertEqual(tick.funding_per_unit, Decimal("0.001234567890123456789"))

    def test_committed_record_survives_reopen_and_requires_explicit_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "capture.sqlite"
            with CaptureJournal(path, metadata={"protocol": "v1"}) as journal:
                journal.append("received", 100, payload())
            with self.assertRaises(FileExistsError):
                CaptureJournal(path, metadata={"protocol": "v1"})
            with CaptureJournal(path, metadata={"protocol": "v1"}, resume=True) as journal:
                self.assertEqual(journal.verify(), 1)
                journal.append("disconnect", 101, '{"reason":"test"}')
                self.assertEqual(journal.verify(), 2)
            with self.assertRaises(ValueError):
                CaptureJournal(path, metadata={"protocol": "v2"}, resume=True)

    def test_tampering_is_rejected_on_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "capture.sqlite"
            with CaptureJournal(path, metadata={}) as journal:
                journal.append("received", 100, payload())
            with sqlite3.connect(path) as db:
                db.execute("UPDATE events SET raw='changed' WHERE seq=1")
            db.close()
            with self.assertRaises(ValueError):
                CaptureJournal(path, metadata={}, resume=True)

    def test_second_writer_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "capture.sqlite"
            with CaptureJournal(path, metadata={}) as first:
                first.append("received", 100, payload())
                with self.assertRaises(sqlite3.OperationalError):
                    CaptureJournal(path, metadata={}, resume=True)

    def test_atomic_batch_validation_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "capture.sqlite"
            with CaptureJournal(path, metadata={}) as journal:
                with self.assertRaises(ValueError):
                    journal.append_many([("received", 100, payload()), ("received", -1, payload())])
                self.assertEqual(journal.verify(), 0)
                self.assertEqual(journal.append_many([("received", 100, payload()),
                                                       ("received", 101, payload())]), 2)
            with CaptureJournal(path, metadata={}, resume=True) as journal:
                self.assertEqual(journal.verify(), 2)


if __name__ == "__main__":
    unittest.main()
