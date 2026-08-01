from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
import json
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from hermes_trading.control_auth import sign_command


class ControlClientConfigError(RuntimeError):
    """Raised when a control client would call a non-local control API."""


RequestJson = Callable[[str, str, Mapping[str, str], str], dict[str, object]]


class ControlClient:
    def __init__(
        self,
        base_url: str,
        hmac_secret: str,
        *,
        request_json: RequestJson | None = None,
        now: Callable[[], datetime] | None = None,
        nonce: Callable[[], str] | None = None,
        allowed_hosts: frozenset[str] | None = None,
    ) -> None:
        _validate_control_url(base_url, allowed_hosts or frozenset())
        self._base_url = base_url.rstrip("/")
        self._hmac_secret = hmac_secret
        self._request_json = request_json or _httpx_request_json
        self._now = now or (lambda: datetime.now(UTC))
        self._nonce = nonce or (lambda: f"control-{uuid4()}")

    def status(self) -> dict[str, object]:
        return self._signed_request("GET", "/status", "")

    def qualification(self, phase: str = "validation_ready") -> dict[str, object]:
        body = json.dumps({"phase": phase}, separators=(",", ":"), sort_keys=True)
        return self._signed_request("POST", "/qualification", body)

    def _signed_request(self, method: str, path: str, body: str) -> dict[str, object]:
        current = self._now().astimezone(UTC)
        nonce = self._nonce()
        headers = {
            "Content-Type": "application/json",
            "X-Hermes-Nonce": nonce,
            "X-Hermes-Timestamp": current.isoformat(),
            "X-Hermes-Signature": sign_command(self._hmac_secret, nonce, current, body),
        }
        return self._request_json(method, self._base_url + path, headers, body)


def control_status_message(payload: Mapping[str, object]) -> str:
    return (
        f"status={payload.get('status')} "
        f"mode={payload.get('trading_mode')} "
        f"real_orders_allowed={_bool_text(payload.get('real_orders_allowed'))}"
    )


def qualification_message(payload: Mapping[str, object]) -> str:
    return (
        f"qualification phase={payload.get('requested_phase')} "
        f"promotable={_bool_text(payload.get('promotable'))} "
        f"promoted={_bool_text(payload.get('promoted'))} "
        f"run_id={payload.get('run_id')}"
    )


def _httpx_request_json(method: str, url: str, headers: Mapping[str, str], body: str) -> dict[str, object]:
    response = httpx.request(method, url, headers=dict(headers), content=body.encode("utf-8"), timeout=10.0)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("control API returned a non-object payload")
    return payload


def _validate_control_url(value: str, allowed_hosts: frozenset[str]) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "http":
        raise ControlClientConfigError("control client base URL must be HTTP")
    if parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        return
    if parsed.hostname in allowed_hosts:
        return
    raise ControlClientConfigError("control client base URL host must be loopback or explicitly allowed")


def _bool_text(value: object) -> str:
    return "true" if value is True else "false" if value is False else str(value)
