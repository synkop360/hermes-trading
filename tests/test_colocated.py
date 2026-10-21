import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from hermes_trading.control_client import ControlClient
from hermes_trading.colocated import (
    discord_bot_configured,
    resolve_colocated_assets,
    should_start_control_api,
    start_control_api_if_configured,
)
from hermes_trading.run import ensure_state_files, resolve_state_dir


class ColocatedEntrypointTests(unittest.TestCase):
    def test_resolve_state_dir_defaults_to_project_root_state(self) -> None:
        root = Path("/some/project")

        self.assertEqual(resolve_state_dir(root, {}), root / "state")

    def test_resolve_state_dir_honors_env_override(self) -> None:
        root = Path("/some/project")

        self.assertEqual(
            resolve_state_dir(root, {"HERMES_STATE_DIR": "/other/state-consultant"}),
            Path("/other/state-consultant"),
        )

    def test_control_api_starts_only_when_hmac_secret_exists(self) -> None:
        self.assertFalse(should_start_control_api({}))
        self.assertTrue(should_start_control_api({"HERMES_CONTROL_HMAC_SECRET": "secret"}))

    def test_discord_bot_starts_only_when_all_required_secrets_exist(self) -> None:
        self.assertFalse(discord_bot_configured({}))
        self.assertFalse(
            discord_bot_configured(
                {
                    "HERMES_DISCORD_BOT_TOKEN": "token",
                    "HERMES_DISCORD_ALLOWED_USER_IDS": "123",
                }
            )
        )
        self.assertTrue(
            discord_bot_configured(
                {
                    "HERMES_DISCORD_BOT_TOKEN": "token",
                    "HERMES_DISCORD_ALLOWED_USER_IDS": "123",
                    "HERMES_CONTROL_HMAC_SECRET": "secret",
                    "HERMES_DISCORD_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
                }
            )
        )

    def test_colocated_assets_match_worker_resolution(self) -> None:
        self.assertEqual(
            resolve_colocated_assets(
                {"asset": "BTC/USDT", "assets": ["ETH/USDT", "XRP/USDT"]},
                None,
                {},
            ),
            ["ETH/USDT", "XRP/USDT"],
        )
        self.assertEqual(
            resolve_colocated_assets({}, "BTC/USDT,ETH/USDT", {}),
            ["BTC/USDT", "ETH/USDT"],
        )
        self.assertEqual(resolve_colocated_assets({}, None, {}), ["BTC/USDT"])

    def test_module_file_exists_for_docker_entrypoint(self) -> None:
        self.assertTrue((Path(__file__).resolve().parents[1] / "hermes_trading" / "colocated.py").exists())

    def test_colocated_control_api_serves_status_from_background_thread(self) -> None:
        with TemporaryDirectory() as directory:
            state_dir = Path(directory)
            ensure_state_files(state_dir)
            service = start_control_api_if_configured(
                state_dir,
                {
                    "HERMES_CONTROL_HMAC_SECRET": "secret",
                    "HERMES_CONTROL_HOST": "127.0.0.1",
                    "HERMES_CONTROL_PORT": "0",
                },
            )
            self.assertIsNotNone(service)
            try:
                host, port = service.stop.server_address[:2]
                payload = ControlClient(f"http://{host}:{port}", "secret").status()
            finally:
                service.stop.shutdown()
                service.stop.server_close()
                service.thread.join(timeout=5)
                self.assertFalse(service.thread.is_alive())

        self.assertEqual(payload["status"], "ok")


if __name__ == "__main__":
    unittest.main()
