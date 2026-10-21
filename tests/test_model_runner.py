import subprocess
import json
import tempfile
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from hermes_trading.costs import CostLedger
from hermes_trading.model_runner import ReflectionModelRunner
from hermes_trading.providers import HermesCatalog, ModelCandidate, ProviderAttempt, ProviderRouter


class Store:
    def __init__(self): self.attempts = []
    def provider_attempts(self): return self.attempts
    def record_provider_attempt(self, attempt): self.attempts.append(attempt)


class ReflectionModelRunnerTests(unittest.TestCase):
    def test_strict_execution_uses_proposal_transport_not_agent_cli(self):
        command=Mock(return_value=subprocess.CompletedProcess([],0,'{"outcome":"no_change"}',''))
        runner=ReflectionModelRunner(ProviderRouter(Store(),HermesCatalog(())),command_run=command,
                                     strict_pinned=True,pinned_candidate=ModelCandidate('nous','fixed:free',True,'test'))
        runner.run('private prompt')
        arguments=command.call_args.args[0]
        self.assertIn('hermes_trading.proposal_transport',arguments);self.assertNotIn('-z',arguments)
        self.assertNotIn('private prompt',arguments);self.assertEqual(command.call_args.kwargs['input'],'private prompt')
        self.assertIn('--free-required',arguments)

    def test_transport_pricing_and_raw_response_survive_usage_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger=CostLedger(Path(tmp))
            proof={'payment':'verified_zero_catalog_price'};raw={'model':'fixed:free','choices':[]}
            def command(arguments,**kwargs):
                Path(arguments[arguments.index('--usage-file')+1]).write_text(json.dumps({'input_tokens':20,'output_tokens':10,'pricing_verification':proof,'raw_response':raw}))
                return subprocess.CompletedProcess(arguments,0,'{"outcome":"no_change"}','')
            runner=ReflectionModelRunner(ProviderRouter(Store(),HermesCatalog(())),command_run=command,cost_ledger=ledger,
                                         strict_pinned=True,pinned_candidate=ModelCandidate('nous','fixed:free',True,'test'))
            runner.run('prompt');event=ledger._events()[0]
            self.assertEqual(event.metadata['pricing_verification'],proof);self.assertEqual(event.metadata['raw_response'],raw)
    def test_usage_evidence_is_retained_if_accounting_write_fails(self):
        paths = []
        ledger = Mock()
        ledger.record.side_effect = OSError("ledger unavailable")
        def command(command, **kwargs):
            path = Path(command[command.index("--usage-file") + 1])
            paths.append(path)
            path.write_text('{"input_tokens":100,"output_tokens":20}', encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, '{"outcome":"no_change"}', '')
        runner = ReflectionModelRunner(ProviderRouter(Store(), HermesCatalog(())), command_run=command,
                                      strict_pinned=True, cost_ledger=ledger,
                                      pinned_candidate=ModelCandidate("test", "fixed-v1", True, "test"))
        try:
            with self.assertRaisesRegex(RuntimeError, "evidence retained at") as error:
                runner.run("prompt")
            self.assertEqual(len(paths), 1)
            self.assertTrue(paths[0].exists())
            self.assertIn(str(paths[0]), str(error.exception))
        finally:
            for path in paths:
                path.unlink(missing_ok=True)

    def test_strict_malformed_output_cannot_call_any_fallback(self):
        command = Mock(return_value=subprocess.CompletedProcess([], 0, 'not a JSON decision', ''))
        local, paid = Mock(), Mock()
        runner = ReflectionModelRunner(ProviderRouter(Store(), HermesCatalog(())), command_run=command,
                                      strict_pinned=True, local_run=local, openai_run=paid,
                                      pinned_candidate=ModelCandidate("test", "fixed-v1", True, "test"))
        with self.assertRaisesRegex(RuntimeError, "JSON object"):
            runner.run("prompt")
        self.assertEqual(command.call_count, 1)
        local.assert_not_called()
        paid.assert_not_called()

    def test_strict_missing_or_malformed_usage_remains_unknown(self):
        for report in (None, 'bad-json', '[]', '{}', '{"input_tokens":true,"output_tokens":4}',
                       '{"input_tokens":-1,"output_tokens":4}'):
            with self.subTest(report=report), tempfile.TemporaryDirectory() as tmp:
                ledger = CostLedger(Path(tmp))
                def command(command, **kwargs):
                    if report is not None:
                        Path(command[command.index("--usage-file") + 1]).write_text(report, encoding="utf-8")
                    return subprocess.CompletedProcess(command, 0, '{"outcome":"no_change"}', '')
                runner = ReflectionModelRunner(ProviderRouter(Store(), HermesCatalog(())), command_run=command,
                                              strict_pinned=True, cost_ledger=ledger,
                                              pinned_candidate=ModelCandidate("test", "fixed-v1", True, "test"))
                runner.run("prompt")
                event = ledger._events()[0]
                self.assertIsNone(event.amount)
                self.assertEqual(event.quality, "missing")
                self.assertEqual(event.metadata["usage_status"], "missing_or_invalid")

    def test_strict_flags_cannot_be_enabled_by_truthy_strings(self):
        for flags in ({"strict_pinned": "true"}, {"paid_calls_authorized": "false"}):
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                ReflectionModelRunner(ProviderRouter(Store(), HermesCatalog(())), **flags)

    def test_strict_pin_failure_cannot_call_any_fallback(self):
        command = Mock(side_effect=RuntimeError("pinned unavailable"))
        local = Mock(return_value='{"outcome":"no_change"}')
        paid = Mock(return_value='{"outcome":"no_change"}')
        runner = ReflectionModelRunner(ProviderRouter(Store(), HermesCatalog(())), command_run=command,
                                      local_run=local, openai_run=paid, strict_pinned=True,
                                      pinned_candidate=ModelCandidate("test", "fixed-v1", True, "test"))
        with self.assertRaisesRegex(RuntimeError, "pinned unavailable"):
            runner.run("prompt")
        self.assertEqual(command.call_count, 1)
        local.assert_not_called()
        paid.assert_not_called()

    def test_strict_pin_requires_explicit_provider_and_model(self):
        for candidate in (None, ModelCandidate("test", "auto", True, "test"),
                          ModelCandidate("", "fixed-v1", True, "test")):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                ReflectionModelRunner(ProviderRouter(Store(), HermesCatalog(())),
                                      strict_pinned=True, pinned_candidate=candidate)

    def test_strict_paid_pin_requires_authorization_before_dispatch(self):
        command = Mock()
        runner = ReflectionModelRunner(ProviderRouter(Store(), HermesCatalog(())), command_run=command,
                                      strict_pinned=True,
                                      pinned_candidate=ModelCandidate("test", "paid-v1", False, "test"))
        with self.assertRaises(PermissionError):
            runner.run("prompt")
        command.assert_not_called()

    def test_strict_authorized_paid_pin_returns_exact_provenance(self):
        command = Mock(return_value=subprocess.CompletedProcess([], 0, '{"outcome":"no_change"}', ''))
        runner = ReflectionModelRunner(ProviderRouter(Store(), HermesCatalog(())), command_run=command,
                                      strict_pinned=True, paid_calls_authorized=True,
                                      pinned_candidate=ModelCandidate("test", "paid-v1", False, "test"))
        result = runner.run("prompt")
        self.assertEqual((result.provider, result.model), ("test", "paid-v1"))
        self.assertEqual(command.call_args.args[0][-4:], ["--provider", "test", "-m", "paid-v1"])

    def test_strict_cooldown_blocks_dispatch(self):
        store = Store()
        now = datetime.now(UTC)
        store.record_provider_attempt(ProviderAttempt("test", "fixed-v1", "rate_limit", now, 300))
        command = Mock()
        runner = ReflectionModelRunner(ProviderRouter(store, HermesCatalog(())), command_run=command,
                                      strict_pinned=True,
                                      pinned_candidate=ModelCandidate("test", "fixed-v1", True, "test"))
        with self.assertRaisesRegex(RuntimeError, "cooling down"):
            runner.run("prompt", now=now)
        command.assert_not_called()

    def test_failed_strict_call_preserves_emitted_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = CostLedger(Path(tmp))
            def command(command, **kwargs):
                Path(command[command.index("--usage-file") + 1]).write_text(
                    '{"input_tokens":100,"output_tokens":20}', encoding="utf-8")
                raise RuntimeError("request failed after usage was written")
            runner = ReflectionModelRunner(ProviderRouter(Store(), HermesCatalog(())), command_run=command,
                                          strict_pinned=True, cost_ledger=ledger,
                                          pinned_candidate=ModelCandidate("test", "fixed-v1", True, "test"))
            with self.assertRaises(RuntimeError):
                runner.run("prompt")
            self.assertTrue(ledger.events_path.exists())
            event = ledger._events()[0]
            self.assertEqual(event.metadata["input_tokens"], 100)

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

    def test_hermes_auto_failure_is_recorded_before_fallback(self):
        store = Store()

        def run(command, **kwargs):
            raise RuntimeError("HTTP 404: default model not found")

        result = ReflectionModelRunner(
            ProviderRouter(store, HermesCatalog(())),
            command_run=run,
            openai_run=lambda prompt: '{"variable":"entry.threshold"}',
        ).run("prompt")

        self.assertEqual(result.provider, "openai_api")
        self.assertEqual(store.attempts[0].provider, "hermes_auto")
        self.assertEqual(store.attempts[0].model, "auto")
        self.assertEqual(store.attempts[0].status, "invalid_model")

    def test_recent_openai_rate_limit_skips_openai_fallback(self):
        store = Store()
        store.record_provider_attempt(
            ProviderAttempt("openai_api", "fallback", "rate_limit", datetime.now(UTC), retry_after_seconds=300)
        )

        def run(command, **kwargs):
            raise RuntimeError("default unavailable")

        def openai_run(prompt):
            raise AssertionError("OpenAI should be cooling down")

        with self.assertRaisesRegex(RuntimeError, "openai_api fallback cooling down"):
            ReflectionModelRunner(
                ProviderRouter(store, HermesCatalog(())),
                command_run=run,
                openai_run=openai_run,
            ).run("prompt")

    def test_openai_failure_is_recorded_and_includes_hermes_errors(self):
        store = Store()

        def run(command, **kwargs):
            raise RuntimeError("HTTP 404: default model not found")

        with self.assertRaisesRegex(RuntimeError, "hermes_auto.*openai_api"):
            ReflectionModelRunner(
                ProviderRouter(store, HermesCatalog(())),
                command_run=run,
                openai_run=lambda prompt: (_ for _ in ()).throw(RuntimeError("HTTP 429: too many requests")),
            ).run("prompt")

        self.assertEqual(store.attempts[-1].provider, "openai_api")
        self.assertEqual(store.attempts[-1].status, "rate_limit")

    def test_local_model_accepts_pretty_printed_json_object(self):
        def run(command, **kwargs):
            raise RuntimeError("default unavailable")

        result = ReflectionModelRunner(
            ProviderRouter(Store(), HermesCatalog(())),
            command_run=run,
            local_run=lambda prompt: '{\n  "variable": "entry.threshold",\n  "value": 32,\n  "reason": "pretty json"\n}',
        ).run("prompt")

        self.assertEqual(result.provider, "local_model")
        self.assertEqual(result.output, '{"variable":"entry.threshold","value":32,"reason":"pretty json"}')

    def test_default_command_runner_is_patchable_at_runtime(self):
        def run(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, '{"variable":"entry.threshold"}\n', '')

        with patch("hermes_trading.model_runner.subprocess.run", side_effect=run) as patched:
            result = ReflectionModelRunner(ProviderRouter(Store(), HermesCatalog(()))).run("prompt")

        self.assertEqual(result.provider, "hermes_auto")
        self.assertEqual(patched.call_args.args[0][:3], ["hermes", "-z", "prompt"])

    def test_pinned_candidate_is_tried_first_and_skips_auto_select_and_pool(self):
        calls = []
        catalog = HermesCatalog((ModelCandidate("nous", "a:free", True, "test"),))

        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, '{"variable":"entry.threshold"}\n', '')

        result = ReflectionModelRunner(
            ProviderRouter(Store(), catalog),
            command_run=run,
            pinned_candidate=ModelCandidate("openrouter", "pinned:model", False, "pinned"),
        ).run("prompt")

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-4:], ["--provider", "openrouter", "-m", "pinned:model"])
        self.assertEqual(result.provider, "openrouter")
        self.assertEqual(result.model, "pinned:model")

    def test_pinned_candidate_failure_still_falls_through_to_local_and_openai(self):
        store = Store()

        def run(command, **kwargs):
            raise RuntimeError("pinned model unavailable")

        result = ReflectionModelRunner(
            ProviderRouter(store, HermesCatalog(())),
            command_run=run,
            pinned_candidate=ModelCandidate("openrouter", "pinned:model", False, "pinned"),
            openai_run=lambda prompt: '{"variable":"entry.threshold"}',
        ).run("prompt")

        self.assertEqual(result.provider, "openai_api")
        self.assertEqual(store.attempts[0].provider, "openrouter")
        self.assertEqual(store.attempts[0].model, "pinned:model")

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
