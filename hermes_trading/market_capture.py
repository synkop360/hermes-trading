"""Read-only research data primitives; no exchange or order-entry integration."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import sqlite3


def _number(value):
    if isinstance(value, bool):
        raise ValueError("boolean is not a number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("invalid numeric field") from exc
    if not result.is_finite():
        raise ValueError("non-finite numeric field")
    return result


def _integer(value):
    if type(value) is not int or value < 0:
        raise ValueError("expected nonnegative integer timestamp")
    return value


@dataclass(frozen=True)
class Ticker:
    symbol: str
    server_ms: int
    received_ns: int
    bid: Decimal
    ask: Decimal
    bid_size: Decimal
    ask_size: Decimal
    suspended: bool
    post_only: bool
    funding_per_unit: Decimal
    relative_funding: Decimal
    next_funding_ms: int
    funding_omitted: bool

    @property
    def age_ms(self):
        return Decimal(self.received_ns) / 1_000_000 - self.server_ms

    def usable(self, *, max_age_ms):
        return (not self.suspended and not self.post_only
                and self.bid_size > 0 and self.ask_size > 0
                and 0 <= self.age_ms <= _number(max_age_ms))


def parse_ticker(raw: str, *, received_ns: int):
    """Parse a complete Kraken ticker; keep documented omitted-zero provenance.

    Control events return None and must be retained/handled by the caller.
    Malformed market messages raise rather than becoming synthetic quotes.
    """
    try:
        d = json.loads(raw, parse_float=Decimal)
        if not isinstance(d, dict):
            raise ValueError("message must be an object")
        if "event" in d or d.get("feed") != "ticker":
            return None
        symbol = d["product_id"]
        if not isinstance(symbol, str) or not symbol.startswith("PF_"):
            raise ValueError("expected perpetual symbol")
        for flag in ("suspended", "post_only"):
            if type(d[flag]) is not bool:
                raise ValueError("invalid market status")
        bid, ask, bs, az = (_number(d[k]) for k in
                            ("bid", "ask", "bid_size", "ask_size"))
        if not (0 < bid <= ask and bs >= 0 and az >= 0):
            raise ValueError("invalid quote")
        absolute = _number(d.get("funding_rate", 0))
        relative = _number(d.get("relative_funding_rate", 0))
        if (absolute == 0) != (relative == 0) or absolute * relative < 0:
            raise ValueError("inconsistent funding fields")
        return Ticker(symbol, _integer(d["time"]), _integer(received_ns),
                      bid, ask, bs, az, d["suspended"], d["post_only"],
                      absolute, relative, _integer(d["next_funding_rate_time"]),
                      "funding_rate" not in d or "relative_funding_rate" not in d)
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("malformed ticker") from exc


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class CaptureJournal:
    """Single-writer, atomically committed SQLite journal with a hash chain.

    Explicit resume verifies every record and exact configuration first.
    Hashes detect accidental modification, not an adversary recomputing a chain
    or removing its tail; export external checkpoints for independent auditing.
    """
    def __init__(self, path, *, metadata, resume=False):
        path = Path(path)
        if not resume:
            with path.open("xb"):
                pass
        elif not path.is_file():
            raise FileNotFoundError(path)
        self.config = _canonical({"format": 1, "metadata": metadata})
        self.db = sqlite3.connect(path, timeout=0.2)
        try:
            self.db.execute("PRAGMA locking_mode=EXCLUSIVE")
            self.db.execute("PRAGMA journal_mode=DELETE")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("BEGIN EXCLUSIVE")
            if not resume:
                self.db.execute("CREATE TABLE config (value TEXT NOT NULL)")
                self.db.execute("INSERT INTO config VALUES (?)", (self.config,))
                self.db.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY, kind TEXT NOT NULL, received_ns INTEGER NOT NULL, raw TEXT NOT NULL, previous TEXT NOT NULL, digest TEXT NOT NULL)")
            stored = self.db.execute("SELECT value FROM config").fetchall()
            if stored != [(self.config,)]:
                raise ValueError("journal configuration mismatch")
            self.db.commit()
            self.verify()
        except BaseException:
            self.db.close()
            raise

    def verify(self):
        previous = _hash(self.config)
        count = 0
        for seq, kind, received_ns, raw, prior, digest in self.db.execute(
                "SELECT seq,kind,received_ns,raw,previous,digest FROM events ORDER BY seq"):
            count += 1
            expected = _hash(_canonical([seq, kind, received_ns, raw, previous]))
            if seq != count or prior != previous or digest != expected:
                raise ValueError("journal integrity failure")
            previous = digest
        self.count, self.previous = count, previous
        return count

    def append(self, kind, received_ns, raw):
        return self.append_many([(kind, received_ns, raw)])

    def append_many(self, events):
        """Commit up to 1,000 records together or none; preserve receipt times.

        Caller must bound buffer latency and treat uncommitted data after a
        crash as missing. This method does not implement a network queue.
        """
        batch = []
        seq, previous = self.count, self.previous
        for kind, received_ns, raw in events:
            _integer(received_ns)
            if not isinstance(kind, str) or not kind or not isinstance(raw, str):
                raise ValueError("invalid event")
            if len(batch) >= 1000:
                raise ValueError("batch exceeds 1000 records")
            seq += 1
            digest = _hash(_canonical([seq, kind, received_ns, raw, previous]))
            batch.append((seq, kind, received_ns, raw, previous, digest))
            previous = digest
        with self.db:
            self.db.executemany("INSERT INTO events VALUES (?,?,?,?,?,?)", batch)
        self.count, self.previous = seq, previous
        return seq

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
