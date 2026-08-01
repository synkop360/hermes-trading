import unittest
from pathlib import Path

from hermes_trading.colocated import (
    discord_bot_configured,
    resolve_colocated_assets,
    should_start_control_api,
)


class ColocatedEntrypointTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
