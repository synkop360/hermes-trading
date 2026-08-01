import unittest
from http.server import HTTPServer
from pathlib import Path

from hermes_trading.control_server import (
    ControlServerConfigError,
    build_control_server,
    resolve_control_server_config,
)


class ControlServerTests(unittest.TestCase):
    def test_config_requires_hmac_secret_and_defaults_to_loopback(self) -> None:
        with self.assertRaises(ControlServerConfigError):
            resolve_control_server_config({})

        config = resolve_control_server_config(
            {"HERMES_CONTROL_HMAC_SECRET": "secret", "HERMES_STATE_DIR": "/tmp/hermes"}
        )

        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.port, 8765)
        self.assertEqual(config.state_dir, Path("/tmp/hermes"))

    def test_config_rejects_non_loopback_host(self) -> None:
        with self.assertRaises(ControlServerConfigError):
            resolve_control_server_config(
                {
                    "HERMES_CONTROL_HMAC_SECRET": "secret",
                    "HERMES_CONTROL_HOST": "0.0.0.0",
                }
            )

    def test_build_control_server_constructs_http_server_and_repository(self) -> None:
        config = resolve_control_server_config(
            {
                "HERMES_CONTROL_HMAC_SECRET": "secret",
                "HERMES_CONTROL_PORT": "0",
            }
        )

        server, repository = build_control_server(config)
        try:
            self.assertIsInstance(server, HTTPServer)
            self.assertEqual(repository.integrity_check(), "ok")
        finally:
            server.server_close()
            repository.close()
