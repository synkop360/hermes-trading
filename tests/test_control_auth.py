import unittest
from datetime import UTC, datetime

from hermes_trading.control_auth import sign_command, verify_command


NOW = datetime(2026, 8, 1, tzinfo=UTC)


class ControlAuthTests(unittest.TestCase):
    def test_signed_command_verifies_once_and_rejects_replay(self):
        used = set()
        signature = sign_command("secret", "nonce-1", NOW, '{"command":"status"}')

        self.assertTrue(verify_command("secret", "nonce-1", NOW, '{"command":"status"}', signature, used, now=NOW))
        self.assertFalse(verify_command("secret", "nonce-1", NOW, '{"command":"status"}', signature, used, now=NOW))
