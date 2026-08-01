from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from http.server import HTTPServer
import os
from pathlib import Path

from hermes_trading.control_api import LocalControlApi, make_control_handler
from hermes_trading.persistence.repository import StateRepository


class ControlServerConfigError(RuntimeError):
    """Raised when the private local control server is misconfigured."""


@dataclass(frozen=True)
class ControlServerConfig:
    state_dir: Path
    host: str
    port: int
    hmac_secret: str


def resolve_control_server_config(env: Mapping[str, str] | None = None) -> ControlServerConfig:
    values = env if env is not None else os.environ
    hmac_secret = values.get("HERMES_CONTROL_HMAC_SECRET", "").strip()
    if not hmac_secret:
        raise ControlServerConfigError("HERMES_CONTROL_HMAC_SECRET is required")
    host = values.get("HERMES_CONTROL_HOST", "127.0.0.1").strip() or "127.0.0.1"
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ControlServerConfigError("HERMES_CONTROL_HOST must be loopback only")
    try:
        port = int(values.get("HERMES_CONTROL_PORT", "8765"))
    except ValueError as error:
        raise ControlServerConfigError("HERMES_CONTROL_PORT must be an integer") from error
    if not 0 <= port <= 65535:
        raise ControlServerConfigError("HERMES_CONTROL_PORT must be between 0 and 65535")
    return ControlServerConfig(
        state_dir=Path(values.get("HERMES_STATE_DIR", "state")),
        host=host,
        port=port,
        hmac_secret=hmac_secret,
    )


def build_control_server(config: ControlServerConfig) -> tuple[HTTPServer, StateRepository]:
    config.state_dir.mkdir(parents=True, exist_ok=True)
    repository = StateRepository.open(config.state_dir / "trading.db")
    repository.migrate()
    api = LocalControlApi(repository, config.state_dir, secret=config.hmac_secret)
    server = HTTPServer((config.host, config.port), make_control_handler(api))
    return server, repository
