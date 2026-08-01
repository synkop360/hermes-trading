from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import Protocol

from hermes_trading.persistence.repository import StateRepository


class NonceReplayStore(Protocol):
    def add_once(self, nonce: str, *, accepted_at: datetime, expires_at: datetime) -> bool:
        ...


class SQLiteNonceReplayStore:
    def __init__(self, repository: StateRepository) -> None:
        self._repository = repository

    def add_once(self, nonce: str, *, accepted_at: datetime, expires_at: datetime) -> bool:
        return self._repository.record_control_nonce(
            nonce,
            accepted_at=accepted_at,
            expires_at=expires_at,
        )


def sign_command(secret: str, nonce: str, timestamp: datetime, body: str) -> str:
    payload = f"{nonce}.{timestamp.astimezone(UTC).isoformat()}.{body}".encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def verify_command(
    secret: str,
    nonce: str,
    timestamp: datetime,
    body: str,
    signature: str,
    used_nonces: set[str] | NonceReplayStore,
    *,
    now: datetime | None = None,
) -> bool:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    command_timestamp = timestamp.astimezone(UTC)
    if abs((current - command_timestamp).total_seconds()) > 300:
        return False
    if not hmac.compare_digest(sign_command(secret, nonce, timestamp, body), signature):
        return False
    if isinstance(used_nonces, set):
        if nonce in used_nonces:
            return False
        used_nonces.add(nonce)
        return True
    return used_nonces.add_once(
        nonce,
        accepted_at=current,
        expires_at=command_timestamp + timedelta(minutes=5),
    )
