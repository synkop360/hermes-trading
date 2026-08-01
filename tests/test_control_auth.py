import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
import tempfile

from hermes_trading.control_auth import SQLiteNonceReplayStore, sign_command, verify_command
from hermes_trading.persistence.repository import StateRepository


NOW = datetime(2026, 8, 1, tzinfo=UTC)


class ControlAuthTests(unittest.TestCase):
    def test_signed_command_verifies_once_and_rejects_replay(self):
        used = set()
        signature = sign_command("secret", "nonce-1", NOW, '{"command":"status"}')

        self.assertTrue(verify_command("secret", "nonce-1", NOW, '{"command":"status"}', signature, used, now=NOW))
        self.assertFalse(verify_command("secret", "nonce-1", NOW, '{"command":"status"}', signature, used, now=NOW))

    def test_signed_command_rejects_replay_after_repository_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "trading.db"
            repository = StateRepository.open(database_path)
            repository.migrate()
            signature = sign_command("secret", "nonce-1", NOW, '{"command":"status"}')

            self.assertTrue(
                verify_command(
                    "secret",
                    "nonce-1",
                    NOW,
                    '{"command":"status"}',
                    signature,
                    SQLiteNonceReplayStore(repository),
                    now=NOW,
                )
            )
            repository.close()
            repository = StateRepository.open(database_path)
            repository.migrate()
            try:
                self.assertFalse(
                    verify_command(
                        "secret",
                        "nonce-1",
                        NOW,
                        '{"command":"status"}',
                        signature,
                        SQLiteNonceReplayStore(repository),
                        now=NOW,
                    )
                )
            finally:
                repository.close()

    def test_signed_command_rejects_persistent_replay_at_timestamp_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "trading.db"
            repository = StateRepository.open(database_path)
            repository.migrate()
            try:
                boundary_now = NOW + timedelta(minutes=5)
                signature = sign_command("secret", "nonce-1", NOW, '{"command":"status"}')

                self.assertTrue(
                    verify_command(
                        "secret",
                        "nonce-1",
                        NOW,
                        '{"command":"status"}',
                        signature,
                        SQLiteNonceReplayStore(repository),
                        now=boundary_now,
                    )
                )
                self.assertFalse(
                    verify_command(
                        "secret",
                        "nonce-1",
                        NOW,
                        '{"command":"status"}',
                        signature,
                        SQLiteNonceReplayStore(repository),
                        now=boundary_now,
                    )
                )
            finally:
                repository.close()
