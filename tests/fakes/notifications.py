"""Local notification doubles: nothing here opens a network connection."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


class RecordingTransport:
    """Records every message handed over; optionally fails the next sends."""

    def __init__(self, *, failures: int = 0) -> None:
        self.messages: list[dict] = []
        self.failures = failures

    def send(self, message: dict) -> None:
        if self.failures:
            self.failures -= 1
            raise ConnectionError("injected transport failure")
        self.messages.append(message)

    def kinds(self) -> list[str]:
        return [message["kind"] for message in self.messages]


class ManualClock:
    """A fake UTC clock that moves only when told to."""

    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> datetime:
        self.now += timedelta(seconds=seconds)
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.advance(seconds)
