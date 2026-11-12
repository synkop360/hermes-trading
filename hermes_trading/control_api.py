from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path

from hermes_trading.control import ControlService
from hermes_trading.control_auth import SQLiteNonceReplayStore, verify_command
from hermes_trading.operator_config import OperatorConfigError, list_operator_config, set_operator_config
from hermes_trading.persistence.repository import StateRepository
from hermes_trading.pnl_graph import build_pnl_graph_series, render_pnl_graph_png
from hermes_trading.qualification import QualificationPhase


@dataclass(frozen=True)
class ControlResponse:
    status: int
    body: str | bytes
    content_type: str = "application/json"


class LocalControlApi:
    def __init__(
        self,
        repository: StateRepository,
        state_dir: Path,
        *,
        secret: str,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._state_dir = state_dir
        self._secret = secret
        self._now = now or (lambda: datetime.now(UTC))

    def handle(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: str,
    ) -> ControlResponse:
        if not self._authorized(headers, body):
            return _json_response(401, {"error": "unauthorized"})
        if method == "GET" and path == "/status":
            return _json_response(200, self._status_payload())
        if method == "GET" and path == "/pnl-graph":
            return self._pnl_graph_response()
        if method == "GET" and path == "/config":
            return _json_response(200, list_operator_config(self._state_dir, os.environ))
        if method == "POST" and path == "/config":
            return self._config_response(body)
        if method == "POST" and path == "/qualification":
            return self._qualification_response(body)
        return _json_response(404, {"error": "not_found"})

    def _authorized(self, headers: Mapping[str, str], body: str) -> bool:
        nonce = _header(headers, "X-Hermes-Nonce")
        timestamp = _header(headers, "X-Hermes-Timestamp")
        signature = _header(headers, "X-Hermes-Signature")
        if not nonce or not timestamp or not signature:
            return False
        try:
            signed_at = datetime.fromisoformat(timestamp).astimezone(UTC)
        except ValueError:
            return False
        return verify_command(
            self._secret,
            nonce,
            signed_at,
            body,
            signature,
            SQLiteNonceReplayStore(self._repository),
            now=self._now(),
        )

    def _status_payload(self) -> dict[str, object]:
        return {
            "status": "ok",
            "system_status": self._repository.export_system_status(),
            "trading_mode": os.getenv("HERMES_TRADING_MODE", "training"),
            "real_orders_allowed": _truthy(os.getenv("HERMES_TRADING_ALLOW_REAL_ORDERS")),
        }

    def _qualification_response(self, body: str) -> ControlResponse:
        try:
            payload = json.loads(body)
            phase = QualificationPhase(str(payload["phase"]))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return _json_response(400, {"error": "invalid_qualification_request"})
        result = ControlService(self._repository, self._state_dir).request_qualification(phase)
        return _json_response(200, result)

    def _pnl_graph_response(self) -> ControlResponse:
        series = build_pnl_graph_series(
            self._repository,
            self._state_dir,
            now=self._now(),
            env=os.environ,
        )
        return ControlResponse(200, render_pnl_graph_png(series), "image/png")

    def _config_response(self, body: str) -> ControlResponse:
        try:
            payload = json.loads(body)
            key = str(payload["key"])
            value = str(payload["value"])
            result = set_operator_config(self._state_dir, key, value, os.environ)
        except (KeyError, TypeError, json.JSONDecodeError):
            return _json_response(400, {"error": "invalid_config_request"})
        except OperatorConfigError as error:
            return _json_response(400, {"error": "invalid_config_request", "detail": str(error)})
        return _json_response(200, result)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _json_response(status: int, payload: dict[str, object]) -> ControlResponse:
    return ControlResponse(status, json.dumps(payload, separators=(",", ":"), sort_keys=True))


def make_control_handler(api: LocalControlApi) -> type[BaseHTTPRequestHandler]:
    class ControlHttpHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._handle_control_request()

        def do_POST(self) -> None:
            self._handle_control_request()

        def log_message(self, format: str, *args: object) -> None:
            return

        def _handle_control_request(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else ""
            response = api.handle(self.command, self.path, self.headers, body)
            encoded = response.body if isinstance(response.body, bytes) else response.body.encode("utf-8")
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return ControlHttpHandler
