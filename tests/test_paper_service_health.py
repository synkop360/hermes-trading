"""Operational telemetry must not serialize wallet or position contents."""
import asyncio
from contextlib import redirect_stderr
from datetime import datetime, timezone
import importlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class PaperServiceHealthTests(unittest.TestCase):
    def test_inventory_exposes_only_fixed_bucket_sizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'adaptive_200').mkdir();(root/'private-identity').mkdir()
            (root/'adaptive_200/trading.db').write_bytes(b'1234')
            (root/'private-identity/secret-account.json').write_bytes(b'123')
            (root/'decisions.jsonl').write_bytes(b'12')
            report=self.api.storage_inventory(root)
            self.assertEqual(report['buckets']['adaptive_200'],{'bytes':4,'files':1})
            self.assertEqual(report['buckets']['other_directories'],{'bytes':3,'files':1})
            self.assertEqual(report['buckets']['root_logs'],{'bytes':2,'files':1})
            self.assertNotIn('private-identity',json.dumps(report))
            self.assertNotIn('secret-account',json.dumps(report))

    def setUp(self):
        self.assertTrue((ROOT / 'scripts/paper_service.py').is_file(), 'paper service launcher missing')
        self.api = importlib.import_module('scripts.paper_service')
        self.root = Path(tempfile.mkdtemp(prefix='.test-tmp-service-health-', dir=ROOT))
        self.addCleanup(shutil.rmtree, self.root)
        self.now = datetime(2026, 11, 9, 18, 0, tzinfo=timezone.utc)

    def heartbeat(self, **changes):
        data = dict(timestamp=self.now.isoformat(), mode='paper', execution='simulated',
                    status='ok', model_ready=True, batch_id='private-batch-identity',
                    entry_admission={'blocked':False, 'reasons':[]},
                    accounts={f'{a}_{w}':{'status':'observed','net_equity':'SECRET_BALANCE',
                                         'positions':['SECRET_POSITION']}
                              for a in ('adaptive','control') for w in (50,100,150,200)})
        data.update(changes)
        (self.root / 'heartbeat.json').write_text(json.dumps(data), encoding='utf-8')

    def test_health_contains_only_operational_allowlist(self):
        self.heartbeat()
        report = self.api.read_health(self.root, now=self.now)
        self.assertTrue(report['heartbeat_fresh'])
        self.assertEqual(report['accounts_observed'], 8)
        self.assertFalse(report['entries_blocked'])
        encoded = json.dumps(report)
        for secret in ('SECRET_BALANCE','SECRET_POSITION','private-batch-identity','net_equity','positions'):
            self.assertNotIn(secret, encoded)

    def test_missing_heartbeat_is_unknown_not_zero_accounts(self):
        report = self.api.read_health(self.root, now=self.now)
        self.assertEqual(report['read_status'], 'unavailable')
        self.assertIsNone(report['accounts_observed'])
        self.assertIsNone(report['entries_blocked'])

    def test_storage_projection_is_typed_and_does_not_include_private_fields(self):
        self.heartbeat(worker_lifecycle={'storage_stop':False,'storage_report':{
            'status':'ready','state_bytes':123,'free_bytes':456,
            'limits':{'max_state_bytes':500000000,'reserve_bytes':134217728},
            'private':'SECRET_STORAGE'}})
        report=self.api.read_health(self.root,now=self.now)
        self.assertEqual(report['storage_state_bytes'],123)
        self.assertEqual(report['storage_free_bytes'],456)
        self.assertEqual(report['storage_reserve_bytes'],134217728)
        self.assertFalse(report['storage_stop'])
        self.assertNotIn('SECRET_STORAGE',json.dumps(report))
        self.heartbeat(worker_lifecycle={'storage_stop':'SECRET','storage_report':{
            'status':'SECRET','state_bytes':True,'free_bytes':-1,'limits':[]}})
        report=self.api.read_health(self.root,now=self.now)
        self.assertIsNone(report['storage_state_bytes'])
        self.assertIsNone(report['storage_free_bytes'])
        self.assertIsNone(report['storage_stop'])
        self.assertEqual(report['storage_status'],'unknown')

    def test_stale_heartbeat_cannot_claim_current_health(self):
        self.heartbeat(timestamp='2026-11-09T17:50:00+00:00')
        report = self.api.read_health(self.root, now=self.now)
        self.assertFalse(report['heartbeat_fresh'])
        self.assertEqual(report['heartbeat_age_seconds'], 600)

    def test_future_heartbeat_is_not_fresh(self):
        self.heartbeat(timestamp='2026-11-09T18:01:00+00:00')
        self.assertFalse(self.api.read_health(self.root, now=self.now)['heartbeat_fresh'])

    def test_runtime_fingerprint_changes_with_installed_python_source(self):
        package = self.root/'runtime'
        package.mkdir()
        (package/'__init__.py').write_bytes(b'')
        module = package/'worker.py'
        module.write_bytes(b'VALUE = 1\n')
        first = self.api.runtime_fingerprint(package)
        self.assertEqual(len(first), 64)
        (package/'private.json').write_text('SECRET_NON_SOURCE')
        self.assertEqual(self.api.runtime_fingerprint(package), first)
        module.write_bytes(b'VALUE = 2\n')
        self.assertNotEqual(self.api.runtime_fingerprint(package), first)

    def test_unrecognized_admission_reason_is_redacted(self):
        self.heartbeat(entry_admission={'blocked':True,'reasons':['supervisor_health_stale','SECRET_REASON']})
        report = self.api.read_health(self.root, now=self.now)
        self.assertEqual(report['entry_block_reasons'], ['supervisor_health_stale','other'])
        self.assertNotIn('SECRET_REASON', json.dumps(report))

    def test_invalid_json_does_not_log_its_contents(self):
        (self.root/'heartbeat.json').write_text('SECRET_MALFORMED')
        report = self.api.read_health(self.root, now=self.now)
        self.assertEqual(report['read_status'], 'unavailable')
        self.assertNotIn('SECRET_MALFORMED', json.dumps(report))

    def test_deeply_nested_invalid_json_remains_unavailable(self):
        (self.root/'heartbeat.json').write_text('{"deep":' * 10000 + '0' + '}' * 10000)
        self.assertEqual(self.api.read_health(self.root, now=self.now)['read_status'], 'unavailable')

    def test_one_output_failure_does_not_permanently_stop_observer(self):
        self.heartbeat()
        class OnceBroken(io.StringIO):
            broken = False
            def write(self, text):
                if not self.broken:
                    self.broken = True
                    raise OSError('SECRET_IO_EXCEPTION')
                return super().write(text)
        output, errors = OnceBroken(), io.StringIO()
        original_sleep = asyncio.sleep
        async def runner(root):
            for _ in range(4):
                await original_sleep(0)
        async def fast_sleep(_):
            await original_sleep(0)
        with patch.object(self.api.asyncio, 'sleep', new=fast_sleep), redirect_stderr(errors):
            asyncio.run(self.api.serve(self.root, runner=runner, stream=output))
        self.assertIn('paper_service_health', output.getvalue())
        self.assertNotIn('SECRET_IO_EXCEPTION', errors.getvalue())

    def test_observer_runs_alongside_worker_and_never_prints_worker_result(self):
        self.heartbeat()
        output = io.StringIO()
        async def runner(root):
            await asyncio.sleep(0)
            return {'balance':'SECRET_RESULT'}
        result = asyncio.run(self.api.serve(self.root, runner=runner, stream=output))
        self.assertEqual(result, {'balance':'SECRET_RESULT'})
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertTrue(any(row['kind']=='paper_service_health' for row in records))
        self.assertEqual(records[-1]['kind'], 'paper_worker_stopped')
        self.assertNotIn('SECRET_RESULT', output.getvalue())

    def test_worker_error_propagates_without_private_exception_text(self):
        output = io.StringIO()
        async def runner(root):
            raise ValueError('SECRET_EXCEPTION')
        with self.assertRaises(ValueError):
            asyncio.run(self.api.serve(self.root, runner=runner, stream=output))
        self.assertNotIn('SECRET_EXCEPTION', output.getvalue())
