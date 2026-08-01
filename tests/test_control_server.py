import unittest
from http.server import HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from hermes_trading.control_server import (
    ControlServerConfig,
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

    def test_config_rejects_ipv6_loopback_without_ipv6_server_support(self) -> None:
        with self.assertRaises(ControlServerConfigError):
            resolve_control_server_config(
                {
                    "HERMES_CONTROL_HMAC_SECRET": "secret",
                    "HERMES_CONTROL_HOST": "::1",
                }
            )

    def test_build_control_server_revalidates_host_at_bind_boundary(self) -> None:
        with TemporaryDirectory() as state_dir:
            config = ControlServerConfig(
                state_dir=Path(state_dir),
                host="0.0.0.0",
                port=0,
                hmac_secret="secret",
            )

            with patch("hermes_trading.control_server.StateRepository.open") as repository_open, patch(
                "hermes_trading.control_server.HTTPServer"
            ) as http_server:
                repository = MagicMock()
                repository_open.return_value = repository

                with self.assertRaises(ControlServerConfigError):
                    build_control_server(config)

                repository_open.assert_not_called()
                http_server.assert_not_called()

    def test_build_control_server_rejects_ipv6_loopback_at_bind_boundary(self) -> None:
        with TemporaryDirectory() as state_dir:
            config = ControlServerConfig(
                state_dir=Path(state_dir),
                host="::1",
                port=0,
                hmac_secret="secret",
            )

            with patch("hermes_trading.control_server.StateRepository.open") as repository_open, patch(
                "hermes_trading.control_server.HTTPServer"
            ) as http_server:
                with self.assertRaises(ControlServerConfigError):
                    build_control_server(config)

                repository_open.assert_not_called()
                http_server.assert_not_called()

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
