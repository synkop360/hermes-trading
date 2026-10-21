from copy import deepcopy
from datetime import datetime,timedelta,timezone
import hashlib,json
from pathlib import Path
import tempfile,unittest
import yaml

from hermes_trading.venue_constraints import constraint_record
from hermes_trading.venue_candidate import prepare_draft_revision,build_activation_receipt,validate_activation_venue


class VenueCandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.source=self.root/'source';self.source.mkdir()
        original=Path(__file__).resolve().parents[1]/'research/runs/approved-cost-reserve-v1'
        self.original=original
        for name in ['manifest.json','authorization.json']:
            (self.source/name).write_bytes((original/name).read_bytes())
        manifest=json.loads((original/'manifest.json').read_text(encoding='utf-8'))
        for profile in manifest['profiles']:
            name=profile['directory'];(self.source/name).mkdir()
            for filename in ['strategy.yaml','goal.yaml','wallet_account.draft.json']:
                (self.source/name/filename).write_bytes((original/name/filename).read_bytes())
        # Unknown runtime/cost files must never be copied into a new candidate.
        (self.source/'sensitive.env').write_text('invented test data')
        (self.source/'adaptive_100/wallet_costs.draft.json').write_text('invented stale costs')
        refresh=original.parent/'venue-refresh-2026-10-17'
        received=json.loads((refresh/'verification.json').read_text(encoding='utf-8'))
        raw=json.loads((refresh/'asset-pairs.raw.json').read_text(encoding='utf-8'))
        records={asset:constraint_record(pair,asset=asset,received_at=received['received_at'],source_url=received['url'])
            for pair in raw['result'].values() for asset in manifest['assets']
            if pair['wsname'].replace('XBT/','BTC/')==asset}
        self.venue={'schema_version':1,'records':records};self.at=datetime.fromisoformat(received['received_at'])
        self.target=self.root/'candidate'

    def build(self):return prepare_draft_revision(self.source,self.target,venue_receipt=self.venue)

    def test_exact_eight_disabled_drafts_correct_24_minima_and_preserve_originals(self):
        before={str(path.relative_to(self.source)):hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.source.rglob('*') if path.is_file()}
        result=self.build();manifest=json.loads((self.target/'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(result['changed_fields'],24);self.assertFalse(result['activated'])
        self.assertEqual(manifest['status'],'prepared_draft_not_activated')
        self.assertEqual(manifest['execution_venue_protocol'],'dated_rules_v1')
        self.assertEqual(len(manifest['profiles']),8)
        for profile in manifest['profiles']:
            name=profile['directory'];old=yaml.safe_load((self.source/name/'strategy.yaml').read_text())
            new=yaml.safe_load((self.target/name/'strategy.yaml').read_text())
            for asset,value in [('BTC/EUR','0.00006'),('ETH/EUR','0.002'),('XRP/EUR','4')]:
                self.assertEqual(new['symbols'][asset]['simulation']['minimum_order_quantity'],value)
                old['symbols'][asset]['simulation']['minimum_order_quantity']=value
            self.assertEqual(new,old)
            for filename in ['goal.yaml','wallet_account.draft.json']:
                self.assertEqual((self.source/name/filename).read_bytes(),(self.target/name/filename).read_bytes())
        self.assertEqual(before,{str(path.relative_to(self.source)):hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in self.source.rglob('*') if path.is_file()})
        self.assertFalse((self.target/'sensitive.env').exists())
        self.assertFalse((self.target/'adaptive_100/wallet_costs.draft.json').exists())
        self.assertEqual((self.target/'authorization.json').read_bytes(),(self.source/'authorization.json').read_bytes())

    def test_enabled_existing_partial_or_active_source_rejected_before_target_creation(self):
        with self.assertRaises(ValueError):prepare_draft_revision(self.source,self.target,venue_receipt=self.venue,enabled=True)
        self.assertFalse(self.target.exists())
        path=self.source/'manifest.json';manifest=json.loads(path.read_text())
        for mutation in ['active','partial','foreign_path']:
            value=deepcopy(manifest)
            if mutation=='active':value['status']='paper_ready'
            if mutation=='partial':value['profiles'].pop()
            if mutation=='foreign_path':value['profiles'][0]['directory']='../outside'
            path.write_text(json.dumps(value))
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):self.build()
            self.assertFalse(self.target.exists())
        path.write_text(json.dumps(manifest));self.build()
        with self.assertRaises(ValueError):self.build()

    def activate_copy(self):
        self.build();manifest=json.loads((self.target/'manifest.json').read_text())
        manifest['status']='paper_ready';(self.target/'manifest.json').write_text(json.dumps(manifest))
        for profile in manifest['profiles']:
            path=self.target/profile['directory'];account=json.loads((path/'wallet_account.draft.json').read_text())
            account.update(experiment_id='synthetic-activation',activated_at=self.at.isoformat(),status='paper_ready')
            (path/'wallet_account.json').write_text(json.dumps(account))
        return manifest

    def test_saved_research_facts_do_not_authorize_later_activation(self):
        self.activate_copy()
        with self.assertRaisesRegex(ValueError,'stale'):
            build_activation_receipt(self.target,self.venue,at=self.at+timedelta(seconds=300))
        self.assertFalse((self.target/'venue_activation.json').exists())

    def test_activation_receipt_binds_all_strategies_accounts_goals_and_original_time(self):
        self.activate_copy()
        receipt=build_activation_receipt(self.target,self.venue,at=self.at)
        (self.target/'venue_activation.json').write_text(json.dumps(receipt))
        self.assertEqual(validate_activation_venue(self.target),receipt)
        with self.assertRaisesRegex(ValueError,'stale'):
            validate_activation_venue(self.target,at=self.at+timedelta(seconds=300))
        # Retained audit remains valid at its original time after wall-clock age.
        self.assertEqual(validate_activation_venue(self.target),receipt)
        strategy=self.target/'control_200/strategy.yaml';rules=yaml.safe_load(strategy.read_text())
        rules['symbols']['ETH/EUR']['simulation']['minimum_order_quantity']='0.001'
        strategy.write_text(yaml.safe_dump(rules))
        with self.assertRaises(ValueError):validate_activation_venue(self.target)

    def test_new_protocol_profile_loading_requires_bound_activation_evidence(self):
        from hermes_trading.paired_paper import _profiles
        manifest=self.activate_copy()
        with self.assertRaisesRegex(ValueError,'venue'):_profiles(self.target,manifest)
        receipt=build_activation_receipt(self.target,self.venue,at=self.at)
        (self.target/'venue_activation.json').write_text(json.dumps(receipt))
        self.assertEqual(len(_profiles(self.target,manifest)),8)

    def test_corrected_inception_binds_venue_receipt_and_recovers_original_clock(self):
        from hermes_trading.paired_inception import initialize_paired_inception,read_paired_inception
        from hermes_trading.reflection_supervision import _hash
        manifest=self.activate_copy()
        venue=build_activation_receipt(self.target,self.venue,at=self.at)
        (self.target/'venue_activation.json').write_text(json.dumps(venue))
        for profile in manifest['profiles']:
            costs={'schema_version':1,'experiment_id':'synthetic-activation','currency':'EUR',
                'scope_start':self.at.isoformat(),'as_of':self.at.isoformat(),
                'totals':{name:{'amount':'0','source':'synthetic_test_only','quality':'actual'}
                         for name in ('hosting','ai','tools','funding')}}
            (self.target/profile['directory']/'wallet_costs.json').write_text(json.dumps(costs))
        opening=initialize_paired_inception(self.target,clock=lambda:self.at)
        self.assertEqual(opening['venue_activation_hash'],_hash(venue))
        self.assertEqual(initialize_paired_inception(self.target,clock=lambda:self.at+timedelta(days=1)),opening)
        self.assertEqual(read_paired_inception(self.target),opening)


if __name__=='__main__':unittest.main()
