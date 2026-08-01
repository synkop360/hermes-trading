import subprocess
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from hermes_trading.costs import CostLedger
from hermes_trading.model_runner import ReflectionModelRunner
from hermes_trading.providers import HermesCatalog, ModelCandidate, ProviderAttempt, ProviderRouter


class Store:
    def __init__(self): self.attempts = []
    def provider_attempts(self): return self.attempts
    def record_provider_attempt(self, attempt): self.attempts.append(attempt)


class ReflectionModelRunnerTests(unittest.TestCase):
    def test_default_hermes_is_first_and_has_no_model_override(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, '{"variable":"entry.threshold"}\n', '')
        result = ReflectionModelRunner(ProviderRouter(Store(), HermesCatalog(())), command_run=run).run("prompt")
        self.assertEqual(calls[0][:3], ["hermes", "-z", "prompt"])
        self.assertIn("--usage-file", calls[0])
        self.assertEqual(result.provider, "hermes_auto")

    def test_openai_runs_only_after_all_free_candidates(self):
        calls = []
        catalog = HermesCatalog((ModelCandidate("nous", "a:free", True, "test"), ModelCandidate("openrouter", "b:free", True, "test")))
        def run(command, **kwargs):
            calls.append(command)
            raise RuntimeError("429")
        result = ReflectionModelRunner(ProviderRouter(Store(), catalog), command_run=run, openai_run=lambda prompt: '{"variable":"entry.threshold"}').run("prompt")
        self.assertEqual([call[:3] for call in calls], [["hermes", "-z", "prompt"], ["hermes", "-z", "prompt"], ["hermes", "-z", "prompt"]])
        self.assertIn("--usage-file", calls[1])
        self.assertEqual(calls[1][-4:], ["--provider", "nous", "-m", "a:free"])
        self.assertEqual(calls[2][-4:], ["--provider", "openrouter", "-m", "b:free"])
        self.assertEqual(result.provider, "openai_api")

    def test_default_command_runner_is_patchable_at_runtime(self):
        def run(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, '{"variable":"entry.threshold"}\n', '')

        with patch("hermes_trading.model_runner.subprocess.run", side_effect=run) as patched:
            result = ReflectionModelRunner(ProviderRouter(Store(), HermesCatalog(()))).run("prompt")

        self.assertEqual(result.provider, "hermes_auto")
        self.assertEqual(patched.call_args.args[0][:3], ["hermes", "-z", "prompt"])

    def test_free_hermes_usage_records_zero_cost_when_confirmed_free(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = CostLedger(Path(temp_dir))
            catalog = HermesCatalog((ModelCandidate("nous", "a:free", True, "test"),))

            def run(command, **kwargs):
                usage_path = Path(command[command.index("--usage-file") + 1])
                usage_path.write_text('{"input_tokens":100,"output_tokens":20}\n', encoding="utf-8")
                if "--provider" not in command:
                    raise RuntimeError("default unavailable")
                return subprocess.CompletedProcess(command, 0, '{"variable":"entry.threshold"}\n', '')

            result = ReflectionModelRunner(
                ProviderRouter(Store(), catalog),
                command_run=run,
                cost_ledger=ledger,
            ).run("prompt")

            self.assertEqual(result.provider, "nous")
            self.assertEqual(ledger.costs_between()["hermes"], Decimal("0"))


if __name__ == "__main__":
    unittest.main()
