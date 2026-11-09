"""Offline checks of the actual disabled release, with isolated mutations."""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import stat
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


class PaperCandidateCheckTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix='.test-tmp-candidate-check-', dir=ROOT))
        self.addCleanup(self.cleanup)
        self.candidate = self.base / 'candidate'
        shutil.copytree(ROOT / 'config' / 'paper-beta-v1', self.candidate)
        self.assertTrue((ROOT / 'scripts/check_paper_candidate.py').is_file(),
                        'disabled candidate checker is missing')
        self.api = importlib.import_module('scripts.check_paper_candidate')

    def cleanup(self):
        self.assertTrue(self.base.resolve().is_relative_to(ROOT.resolve()))
        def writable_retry(function, path, error):
            os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
            function(path)
        shutil.rmtree(self.base, onerror=writable_retry)

    def read(self, name):
        return json.loads((self.candidate / name).read_text(encoding='utf-8'))

    def write(self, name, value):
        (self.candidate / name).write_text(json.dumps(value), encoding='utf-8')

    def invalid(self, fragment):
        report = self.api.check_candidate(self.candidate)
        self.assertFalse(report['candidate_valid'])
        self.assertFalse(report['activation_ready'])
        self.assertIn(fragment, json.dumps(report['errors']))

    def test_intact_candidate_passes_without_writes_or_activation_claim(self):
        def snapshot():
            return {p.relative_to(self.candidate).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in self.candidate.rglob('*') if p.is_file()}
        before = snapshot()
        report = self.api.check_candidate(self.candidate)
        self.assertTrue(report['candidate_valid'], report['errors'])
        self.assertEqual(report['status'], 'valid_disabled_candidate')
        self.assertFalse(report['activation_ready'])
        self.assertFalse(report['activation_assessed'])
        self.assertEqual(len(report['accounts']), 8)
        self.assertEqual(report['primary_wallet_eur'], 200)
        self.assertIn('fresh_model_access', report['remaining_activation_checks'])
        self.assertEqual(before, snapshot())

    def test_modified_strategy_is_rejected_by_its_pin(self):
        with (self.candidate / 'adaptive_50/strategy.yaml').open('a') as out:
            out.write('\n# changed\n')
        self.invalid('strategy hash')

    def test_duplicate_profile_cannot_replace_a_missing_wallet(self):
        manifest = self.read('manifest.json')
        manifest['profiles'][-1] = manifest['profiles'][0]
        self.write('manifest.json', manifest)
        self.invalid('eight distinct')

    def test_traversal_directory_is_rejected(self):
        manifest = self.read('manifest.json')
        manifest['profiles'][0]['directory'] = '../outside'
        self.write('manifest.json', manifest)
        self.invalid('profile directory')

    def change_strategy_and_repin(self, change):
        path = self.candidate / 'adaptive_50/strategy.yaml'
        strategy = yaml.safe_load(path.read_text())
        change(strategy)
        path.write_text(yaml.safe_dump(strategy), encoding='utf-8')
        manifest = self.read('manifest.json')
        manifest['profiles'][0]['strategy_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.write('manifest.json', manifest)

    def test_zero_fees_are_not_accepted_even_after_repinning(self):
        self.change_strategy_and_repin(lambda s: s['simulation'].update(taker_fee_rate='0'))
        self.invalid('declared fees')

    def test_larger_risk_caps_are_not_accepted_after_repinning(self):
        self.change_strategy_and_repin(lambda s: s['simulation'].update(max_order_quote='50'))
        self.invalid('risk caps')

    def test_account_activation_is_refused(self):
        account = self.read('adaptive_50/wallet_account.draft.json')
        account['activated_at'] = '2026-11-09T00:00:00+00:00'
        self.write('adaptive_50/wallet_account.draft.json', account)
        self.invalid('unassigned draft account')

    def test_missing_cost_category_is_not_substituted_with_zero(self):
        costs = self.read('control_50/wallet_cost_sources.draft.json')
        del costs['categories']['hosting']
        self.write('control_50/wallet_cost_sources.draft.json', costs)
        self.invalid('cost categories')

    def test_enabled_execution_is_refused(self):
        manifest = self.read('manifest.json')
        manifest['execution_enabled'] = True
        self.write('manifest.json', manifest)
        self.invalid('disabled manifest')

    def test_invalid_json_is_reported_not_raised(self):
        (self.candidate / 'manifest.json').write_text('{')
        self.invalid('manifest')

    def test_missing_manifest_is_reported_without_creating_it(self):
        (self.candidate / 'manifest.json').unlink()
        self.invalid('manifest')
        self.assertFalse((self.candidate / 'manifest.json').exists())

    def test_safety_threshold_drift_is_refused(self):
        policy = self.read('paper_safety.draft.json')
        policy['alert_fraction'] = '0.95'
        self.write('paper_safety.draft.json', policy)
        self.invalid('safety policy')

    def test_missing_backup_is_refused(self):
        limits = self.read('worker_limits.draft.json')
        del limits['daily_backup']
        self.write('worker_limits.draft.json', limits)
        self.invalid('daily backup')

    def test_repinned_rationale_must_still_identify_eur200(self):
        rationale = self.read('primary-rationale.json')
        rationale['wallet_eur'] = 50
        self.write('primary-rationale.json', rationale)
        manifest = self.read('manifest.json')
        manifest['primary_selection']['rationale_sha256'] = hashlib.sha256(
            (self.candidate / 'primary-rationale.json').read_bytes()).hexdigest()
        self.write('manifest.json', manifest)
        self.invalid('EUR200 rationale')

    def repin_all_with_symbol_override(self, override):
        manifest = self.read('manifest.json')
        for profile in manifest['profiles']:
            path = self.candidate / profile['directory'] / 'strategy.yaml'
            strategy = yaml.safe_load(path.read_bytes())
            strategy['symbols']['BTC/EUR']['simulation'].update(override)
            path.write_bytes(yaml.safe_dump(strategy).encode())
            profile['strategy_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.write('manifest.json', manifest)

    def test_identically_repinned_symbol_fee_override_is_rejected(self):
        self.repin_all_with_symbol_override({'taker_fee_rate': '0'})
        self.invalid('declared fees')

    def test_identically_repinned_symbol_risk_override_is_rejected(self):
        self.repin_all_with_symbol_override({'max_order_quote': '50'})
        self.invalid('risk caps')

    @unittest.skipUnless(shutil.which('git'), 'Git is required for checkout conversion')
    def test_windows_git_checkout_preserves_candidate_pin_bytes(self):
        attributes = ROOT / '.gitattributes'
        repo = self.base / 'git-checkout'
        repo.mkdir()
        def git(*args, data=None):
            process = subprocess.run(['git', '-c', 'core.autocrlf=true', *args], cwd=repo,
                                     input=data, capture_output=True, timeout=10)
            self.assertEqual(process.returncode, 0, process.stderr)
            return process.stdout
        git('init', '-q')
        if attributes.is_file():
            shutil.copyfile(attributes, repo / '.gitattributes')
        raw = (self.candidate / 'adaptive_50/strategy.yaml').read_bytes().replace(b'\r\n', b'\n')
        oid = git('hash-object', '-w', '--stdin', data=raw).decode().strip()
        checkout = git('cat-file', '--filters', '--path=config/paper-beta-v1/adaptive_50/strategy.yaml', oid)
        self.assertEqual(checkout, raw, 'Windows checkout must preserve pinned LF bytes')
