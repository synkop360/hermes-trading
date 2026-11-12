"""Exhaustive finite selection/risk invariants; no strategy or market replay."""
from copy import deepcopy
from decimal import Decimal
import importlib.util
from itertools import permutations
import unittest

from hermes_trading.reflection_supervision import _hash
from hermes_trading.paired_performance_report import REPORTING_PROTOCOL
from hermes_trading.paired_risk import initial_risk_state,update_risk_state,is_halted


class PrimarySelectionTests(unittest.TestCase):
    def test_new_selection_rejects_a_contradictory_review_wallet_label(self):
        api=self.api();manifest=self.manifest(200)
        manifest['steering']={'review_wallet':'adaptive_200'};api.resolve_primary(manifest)
        manifest['steering']['review_wallet']='adaptive_100'
        with self.assertRaisesRegex(ValueError,'primary|review'):api.resolve_primary(manifest)

    def api(self):
        self.assertIsNotNone(importlib.util.find_spec('hermes_trading.paired_primary'),'strict shared primary resolver is missing')
        from hermes_trading import paired_primary
        return paired_primary

    def manifest(self,wallet=200,rationale='a'*64):
        api=self.api()
        selection=dict(schema_version=1,purpose='paired_primary_selection_v1',wallet_eur=wallet,rationale_sha256=rationale)
        manifest=dict(primary_selection=selection,primary_wallet_eur=wallet,profiles=[
            dict(arm=arm,wallet_eur=capital,directory=f'{arm}_{capital}')
            for arm in ('adaptive','control') for capital in (50,100,150,200)])
        manifest['reporting_protocol']=api.reporting_protocol_for(manifest,REPORTING_PROTOCOL)
        return manifest

    def test_all_capitals_resolve_logical_names_real_paths_and_immutable_selection(self):
        for wallet in (50,100,150,200):
            with self.subTest(wallet=wallet):
                manifest=self.manifest(wallet);p=self.api().resolve_primary(manifest)
                self.assertEqual(p.wallet_eur,wallet);self.assertTrue(p.versioned)
                self.assertEqual(p.selection_sha256,_hash(manifest['primary_selection']))
                self.assertEqual(p.profile_key('adaptive'),f'adaptive_{wallet}')
                manifest['profiles'][0]['directory']='custom-local-profile'
                self.assertEqual(p.profile_directory(manifest,'adaptive'),'custom-local-profile' if wallet==50 else f'adaptive_{wallet}')
                with self.assertRaises((AttributeError,TypeError)):p.wallet_eur=100

    def test_legacy_interpretation_never_silently_accepts_another_capital_or_version(self):
        api=self.api();legacy=api.resolve_primary({})
        self.assertEqual(legacy.wallet_eur,100);self.assertFalse(legacy.versioned)
        self.assertIsNone(legacy.selection_sha256)
        for label in (50,150,200,True,100.0,'100',None):
            with self.subTest(label=label),self.assertRaises(ValueError):api.resolve_primary({'primary_wallet_eur':label})
        for value in (None,False,[],{},dict(REPORTING_PROTOCOL,primary_wallet_eur=200),dict(REPORTING_PROTOCOL,schema_version=True)):
            with self.subTest(report=value),self.assertRaises(ValueError):api.resolve_primary({'reporting_protocol':value})

    def test_selection_value_cannot_bypass_strict_capital_and_protocol_validation(self):
        api=self.api()
        for wallet,digest in ((200,None),(True,'a'*64),(200.0,'a'*64),(49,'a'*64),(200,'A'*64),(100,'')):
            with self.subTest(wallet=wallet,digest=digest),self.assertRaises(ValueError):api.PrimarySelection(wallet,digest)

    def test_strict_types_versions_digest_and_conflicting_bindings_are_rejected(self):
        api=self.api();manifest=self.manifest()
        mutations=[('wallet_eur',v) for v in (True,False,0,49,201,200.0,'200',Decimal(200),None)]
        mutations += [('schema_version',v) for v in (True,1.0,'1',2,None)]
        mutations += [('rationale_sha256',v) for v in ('A'*64,'a'*63,'g'*64,True,None)]
        mutations += [('purpose','other'),('extra','unknown')]
        for field,value in mutations:
            bad=deepcopy(manifest);bad['primary_selection'][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):api.resolve_primary(bad)
        for value in (None,[],True):
            with self.subTest(contract=value),self.assertRaises(ValueError):api.resolve_primary(dict(manifest,primary_selection=value))
        for field,value in [('primary_wallet_eur',100),('primary_wallet_eur',200.0),('reporting_protocol',REPORTING_PROTOCOL)]:
            with self.subTest(field=field),self.assertRaises(ValueError):api.resolve_primary(dict(manifest,**{field:value}))
        missing=dict(manifest);missing.pop('reporting_protocol')
        with self.assertRaises(ValueError):api.resolve_primary(missing)

    def test_selection_order_is_irrelevant_but_changed_rationale_changes_identity(self):
        api=self.api();manifest=self.manifest();expected=api.resolve_primary(manifest)
        for keys in permutations(manifest['primary_selection']):
            shuffled=dict(manifest,primary_selection={key:manifest['primary_selection'][key] for key in keys})
            self.assertEqual(api.resolve_primary(shuffled),expected)
        self.assertNotEqual(api.resolve_primary(self.manifest(rationale='b'*64)),expected)

    def test_report_revision_preserves_every_original_method_parameter_and_constant(self):
        api=self.api();before=deepcopy(REPORTING_PROTOCOL)
        for wallet in (50,100,150,200):
            manifest=self.manifest(wallet);protocol=api.reporting_protocol_for(manifest,REPORTING_PROTOCOL)
            self.assertEqual(protocol['schema_version'],2);self.assertEqual(protocol['primary_wallet_eur'],wallet)
            self.assertEqual(protocol['primary_selection_sha256'],_hash(manifest['primary_selection']))
            retained={key:value for key,value in protocol.items() if key not in ('schema_version','primary_wallet_eur','primary_selection_sha256')}
            self.assertEqual(retained,{key:value for key,value in before.items() if key not in ('schema_version','primary_wallet_eur')})
            changed=deepcopy(manifest);changed['reporting_protocol']['bootstrap_seed']+=1
            with self.assertRaises(ValueError):api.reporting_protocol_for(changed,REPORTING_PROTOCOL)
        self.assertEqual(REPORTING_PROTOCOL,before)

    def test_either_selected_arm_globally_halts_and_other_wallet_pairs_halt_locally(self):
        api=self.api()
        for selected in (50,100,150,200):
            p=api.resolve_primary(self.manifest(selected))
            for arm in ('adaptive','control'):
                for breached in (50,100,150,200):
                    with self.subTest(selected=selected,arm=arm,breached=breached):
                        state=initial_risk_state(p)
                        self.assertEqual(state['schema_version'],2);self.assertEqual(state['primary_selection_sha256'],p.selection_sha256)
                        self.assertEqual(state['accounts'][f'{arm}_{selected}']['peak'],str(selected))
                        state=update_risk_state(state,{f'{arm}_{breached}':str(Decimal(breached)*Decimal('.97'))},primary=p)
                        self.assertEqual(state['global_halted'],selected==breached)
                        self.assertTrue(is_halted(state,'adaptive',breached));self.assertTrue(is_halted(state,'control',breached))
                        state=update_risk_state(state,{f'{arm}_{breached}':str(breached*2)},primary=p)
                        self.assertTrue(is_halted(state,arm,breached))

    def test_risk_cannot_mix_capital_rationale_or_legacy_and_versioned100(self):
        api=self.api()
        for wallet in (50,100,150,200):
            p=api.resolve_primary(self.manifest(wallet));state=initial_risk_state(p)
            for other in (api.resolve_primary({}),api.resolve_primary(self.manifest(wallet,rationale='b'*64))):
                with self.subTest(wallet=wallet),self.assertRaises(ValueError):update_risk_state(state,{},primary=other)
            with self.assertRaises(ValueError):update_risk_state(state,{})
            with self.assertRaises(ValueError):update_risk_state(initial_risk_state(),{},primary=p)


if __name__=='__main__':unittest.main()
