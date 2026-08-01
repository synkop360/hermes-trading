from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime


def sign_command(secret: str, nonce: str, timestamp: datetime, body: str) -> str:
    payload = f"{nonce}.{timestamp.astimezone(UTC).isoformat()}.{body}".encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def verify_command(secret: str, nonce: str, timestamp: datetime, body: str, signature: str, used_nonces: set[str], *, now: datetime | None = None) -> bool:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if nonce in used_nonces or abs((current - timestamp.astimezone(UTC)).total_seconds()) > 300:
        return False
    if not hmac.compare_digest(sign_command(secret, nonce, timestamp, body), signature):
        return False
    used_nonces.add(nonce)
    return True
