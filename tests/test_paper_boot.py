"""The container must never fall back to the legacy strategy writer."""
import importlib
import importlib.util
from dataclasses import replace
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock,patch


class PaperBootTests(unittest.IsolatedAsyncioTestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('hermes_trading.paper_boot'),
                             'explicit paper-only container boot is absent')
        return importlib.import_module('hermes_trading.paper_boot')

    async def test_live_ambiguous_or_real_order_environment_rejected_before_state_io(self):
        module=self.module()
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'absent'
            for env in ({},{'HERMES_TRADING_MODE':'live','HERMES_TRADING_ALLOW_REAL_ORDERS':'false'},
                        {'HERMES_TRADING_MODE':'paper','HERMES_TRADING_ALLOW_REAL_ORDERS':'true'},
                        {'HERMES_TRADING_MODE':'paper'}):
                with self.subTest(env=env),self.assertRaises(ValueError):
                    await module.run_paper(root,env=env)
                self.assertFalse(root.exists())

    async def test_draft_or_archive_enabled_candidate_cannot_create_inception(self):
        module=self.module()
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            env=dict(HERMES_TRADING_MODE='paper',HERMES_TRADING_ALLOW_REAL_ORDERS='false')
            for status,extra in (('prepared_draft_not_activated',{}),('paper_ready',{'archive_exports':{'enabled':True}})):
                (root/'manifest.json').write_text(json.dumps({'status':status,'execution_enabled':True}))
                (root/'worker_limits.json').write_text(json.dumps(extra))
                with patch.object(module,'initialize_paired_inception') as opening,patch.object(module,'run_worker',new_callable=AsyncMock) as worker:
                    with self.subTest(status=status),self.assertRaises(ValueError):
                        await module.run_paper(root,env=env)
                    opening.assert_not_called();worker.assert_not_awaited()

    def ready_fixture(self):
        from test_paired_inception import PairedInceptionTests
        from test_paired_primary_finance import selected_manifest
        module=self.module()
        fixture=PairedInceptionTests();fixture.setUp();self.addCleanup(fixture.tearDown)
        root=fixture.root
        manifest=selected_manifest(fixture.fixture.manifest,200)
        manifest['execution_enabled']=True
        (root/'manifest.json').write_text(json.dumps(manifest))
        for name in ('reflection_owner.json','model_access.json'):
            (root/'adaptive_200'/name).write_bytes((root/'adaptive_100'/name).read_bytes())
        policy=dict(schema_version=1,experiment_id='paired-test',volume_root=str(root.resolve()),
            capacity_bytes=500000000,alert_fraction='0.70',max_worker_errors=3,max_supervisor_errors=3,
            supervisor_stale_seconds=300,diagnostics_max_bytes=1048576,diagnostics_generations=3)
        (root/'paper_safety.json').write_text(json.dumps(policy))
        limits=json.loads((root/'worker_limits.json').read_text())
        limits['daily_backup']=dict(schema_version=1,destination=str((root.parent/'daily').resolve()),
            max_bytes=500000000,timeout_seconds=30,keep=2)
        (root/'worker_limits.json').write_text(json.dumps(limits))
        return module,fixture

    async def test_approved_paper_boot_creates_real_common_opening_in_selected_primary(self):
        from hermes_trading.paired_inception import read_paired_inception
        module,fixture=self.ready_fixture();root=fixture.root
        with patch.object(module,'run_worker',new_callable=AsyncMock,return_value={'status':'synthetic_dispatch'}) as worker:
            result=await module.run_paper(root,env=dict(HERMES_TRADING_MODE='paper',HERMES_TRADING_ALLOW_REAL_ORDERS='false'))
        self.assertEqual(result['status'],'synthetic_dispatch')
        opening=read_paired_inception(root)
        self.assertEqual(len(opening['accounts']),8)
        self.assertEqual(opening['accounts']['adaptive_200']['net_equity'],'199.99')
        self.assertTrue((root/'adaptive_200/paper_publications.sqlite').is_file())
        worker.assert_awaited_once_with(root.resolve())

    async def test_expired_model_after_restart_still_closes_retained_positions(self):
        from hermes_trading.paired_worker import run_worker
        from hermes_trading.paired_inception import read_paired_inception
        module,fixture=self.ready_fixture();root=fixture.root
        limits=json.loads((root/'worker_limits.json').read_text());limits['max_cycles']=2
        (root/'worker_limits.json').write_text(json.dumps(limits))
        from hermes_trading.paper_safety import record_supervisor_result
        record_supervisor_result(root.resolve(),success=True,event_id='first-supervisor',now=datetime.now(timezone.utc))
        async def interrupt(seconds):raise RuntimeError('synthetic interruption after first completed cycle')
        async def first(path):return await run_worker(path,collector_factory=lambda:fixture.fixture.market,sleep=interrupt)
        env=dict(HERMES_TRADING_MODE='paper',HERMES_TRADING_ALLOW_REAL_ORDERS='false')
        with patch.object(module,'run_worker',side_effect=first):
            with self.assertRaisesRegex(RuntimeError,'synthetic interruption'):
                await module.run_paper(root,env=env)
        opening=read_paired_inception(root)
        access_path=root/'adaptive_200/model_access.json'
        access=json.loads(access_path.read_text());access['valid_until']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
        access_path.write_text(json.dumps(access))
        snapshot=replace(fixture.fixture.snapshot,price=dict(fixture.fixture.snapshot.price,bid='77',ask='78',
            received_at=datetime.now(timezone.utc).isoformat()))
        fixture.fixture.market.collect.return_value={'ETH/EUR':snapshot}
        async def no_wait(seconds):pass
        async def resumed(path):return await run_worker(path,collector_factory=lambda:fixture.fixture.market,sleep=no_wait)
        with patch.object(module,'run_worker',side_effect=resumed):
            result=await module.run_paper(root,env=env)
        self.assertEqual(result['completed_batches'],2)
        self.assertEqual(read_paired_inception(root),opening)
        for profile in fixture.fixture.manifest['profiles']:
            positions=fixture.fixture.positions(profile['directory'])
            self.assertEqual(len(positions),1)
            self.assertEqual(positions[0].status.value,'closed')


if __name__=='__main__':unittest.main()
