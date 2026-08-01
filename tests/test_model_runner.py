import subprocess
import unittest
from unittest.mock import patch

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
        self.assertEqual(calls[0], ["hermes", "-z", "prompt"])
        self.assertEqual(result.provider, "hermes_auto")

    def test_openai_runs_only_after_all_free_candidates(self):
        calls = []
        catalog = HermesCatalog((ModelCandidate("nous", "a:free", True, "test"), ModelCandidate("openrouter", "b:free", True, "test")))
        def run(command, **kwargs):
            calls.append(command)
            raise RuntimeError("429")
        result = ReflectionModelRunner(ProviderRouter(Store(), catalog), command_run=run, openai_run=lambda prompt: '{"variable":"entry.threshold"}').run("prompt")
        self.assertEqual(calls, [["hermes", "-z", "prompt"], ["hermes", "-z", "prompt", "--provider", "nous", "-m", "a:free"], ["hermes", "-z", "prompt", "--provider", "openrouter", "-m", "b:free"]])
        self.assertEqual(result.provider, "openai_api")

    def test_default_command_runner_is_patchable_at_runtime(self):
        def run(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, '{"variable":"entry.threshold"}\n', '')

        with patch("hermes_trading.model_runner.subprocess.run", side_effect=run) as patched:
            result = ReflectionModelRunner(ProviderRouter(Store(), HermesCatalog(()))).run("prompt")

        self.assertEqual(result.provider, "hermes_auto")
        self.assertEqual(patched.call_args.args[0], ["hermes", "-z", "prompt"])


if __name__ == "__main__":
    unittest.main()
