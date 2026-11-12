"""Disabled successor preparation preserves all original economic inputs."""
from pathlib import Path
from copy import deepcopy
import hashlib,json,shutil,tempfile,unittest

from hermes_trading.paired_primary import resolve_primary,reporting_protocol_for
from hermes_trading.paired_performance_report import REPORTING_PROTOCOL


class PrimaryCandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.project=Path(__file__).resolve().parents[1]
        self.original=self.project/'research/runs/current-venue-execution-v1/candidate'
        self.source=self.root/'source';shutil.copytree(self.original,self.source);self.target=self.root/'new'
        self.rationale=dict(schema_version=1,purpose='paired_primary_rationale_v1',wallet_eur=200,
            basis='invented pre-inception admission observation; no profitability result')
        self.approval=json.loads((self.project/'research/runs/primary-wallet-protocol-v1/authorization.json').read_text())

    def build(self,**kwargs):
        from hermes_trading.primary_candidate import prepare_primary_revision
        return prepare_primary_revision(self.source,self.target,rationale=self.rationale,
            implementation_approval=self.approval,**kwargs)

    def test_disabled_successor_binds_selection_release_and_launcher_preserving_all_economic_files(self):
        before={p.relative_to(self.source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in self.source.rglob('*') if p.is_file()}
        result=self.build();manifest=json.loads((self.target/'manifest.json').read_text());p=resolve_primary(manifest)
        self.assertEqual(p.wallet_eur,200);self.assertEqual(manifest['reporting_protocol'],reporting_protocol_for(manifest,REPORTING_PROTOCOL))
        self.assertEqual(manifest['steering']['review_wallet'],'adaptive_200');self.assertFalse(result['activated'])
        self.assertEqual(manifest['status'],'prepared_draft_not_activated');self.assertFalse(manifest['execution_enabled'])
        self.assertEqual(manifest['primary_selection']['rationale_sha256'],hashlib.sha256((self.target/'primary-rationale.json').read_bytes()).hexdigest())
        launch=json.loads((self.target/'supervisor-launch.draft.json').read_text())
        self.assertEqual(launch['primary_selection_sha256'],p.selection_sha256)
        self.assertEqual(launch['schema_version'],2);self.assertFalse(launch['enabled']);self.assertIsNone(launch['account_hash'])
        self.assertIsNone(launch['policy_hash']);self.assertEqual(launch['remote_state_dir'],'/app/state/adaptive_200')
        self.assertTrue(launch['local_state_dir'].endswith('unassigned_until_activation/adaptive_200'))
        for row in manifest['profiles']:
            for filename in ('strategy.yaml','goal.yaml','wallet_account.draft.json'):
                relative=Path(row['directory'])/filename
                self.assertEqual((self.target/relative).read_bytes(),(self.source/relative).read_bytes())
        self.assertEqual(before,{p.relative_to(self.source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in self.source.rglob('*') if p.is_file()})
        revision=json.loads((self.target/'primary-revision.json').read_text())
        self.assertEqual(revision['original_source_sha256'],before)
        self.assertFalse(revision['activation_authorized']);self.assertTrue(revision['economic_strategy_and_fees_preserved'])

    def test_active_wrong_protocol_changed_strategy_and_enabled_build_are_rejected_before_writes(self):
        with self.assertRaises(ValueError):self.build(enabled=True)
        self.assertFalse(self.target.exists())
        path=self.source/'manifest.json';original=json.loads(path.read_text())
        for field,value in [('status','paper_ready'),('reporting_protocol',dict(original['reporting_protocol'],assessment_days=90))]:
            bad=deepcopy(original);bad[field]=value;path.write_text(json.dumps(bad))
            with self.subTest(field=field),self.assertRaises(ValueError):self.build()
            self.assertFalse(self.target.exists())
        path.write_text(json.dumps(original));strategy=self.source/'adaptive_200/strategy.yaml';strategy.write_bytes(strategy.read_bytes()+b'\n')
        with self.assertRaises(ValueError):self.build()
        self.assertFalse(self.target.exists())


if __name__=='__main__':unittest.main()
