from datetime import datetime,timedelta,timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import asyncio
import sqlite3
from decimal import Overflow
from unittest.mock import patch
import yaml

from hermes_trading.paired_performance_report import paired_performance_report

NOW=datetime(2026,10,12,tzinfo=timezone.utc)


class PairedReportTests(unittest.TestCase):
    def test_real_temporary_worker_orders_are_checked_without_resizing(self):
        from decimal import Decimal
        from test_paired_worker import WorkerTests
        from hermes_trading.paired_paper import run_paired_cycle,_profiles
        from hermes_trading.paired_performance_report import _canonical_account,_readonly,_settled_pairs
        from hermes_trading.paired_source_validation import PrimarySourceValidator
        from hermes_trading.paired_cost_stress import FixedOrderCostStress
        fixture=WorkerTests();fixture.setUp()
        try:
            for profile in fixture.manifest['profiles']:
                path=fixture.root/profile['directory']/'strategy.yaml'
                rules=yaml.safe_load(path.read_bytes())
                rules['simulation'].update(taker_fee_rate='.008',maker_fee_rate='.004',slippage_rate='.001')
                path.write_text(yaml.safe_dump(rules))
                profile['strategy_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
            (fixture.root/'manifest.json').write_text(json.dumps(fixture.manifest))
            asyncio.run(run_paired_cycle(fixture.root,fixture.market,now=fixture.now))
            profiles=_profiles(fixture.root,fixture.manifest)
            configs=[profiles[(arm,100)][2] for arm in ('adaptive','control')]
            orders=[_canonical_account(fixture.root/(arm+'_100'),fixture.now)[0] for arm in ('adaptive','control')]
            engines=[FixedOrderCostStress('100') for _ in configs]
            validator=PrimarySourceValidator(fixture.manifest,profiles)
            db=_readonly(fixture.root/'adaptive_100/paper_publications.sqlite')
            try:
                pairs=list(_settled_pairs(db,configs,fixture.now-timedelta(minutes=1),
                    fixture.now+timedelta(days=1),engines,orders,source_validator=validator))
            finally:db.close()
            self.assertEqual(len(pairs),1)
            for engine,original in zip(engines,orders):
                self.assertEqual(engine.configured_orders_checked,1)
                self.assertEqual(engine.configured_policy_unknowns,[])
                self.assertIn('risk_budget',engine.configured_policy_failures[0]['failures'])
                self.assertEqual(engine.holdings['ETH/EUR']['quantity'],Decimal(original[0]['quantity']))
                self.assertTrue(engine.cash_feasible)
        finally:fixture.tearDown()

    def test_invalid_yaml_and_decimal_overflow_return_incomplete(self):
        from test_paired_inception import PairedInceptionTests
        from hermes_trading.paired_inception import initialize_paired_inception
        setup=PairedInceptionTests();setup.setUp();fixture=setup.fixture
        try:
            initialize_paired_inception(fixture.root,clock=lambda:fixture.now)
            for error in (yaml.YAMLError('invalid source'),Overflow('overflow')):
                with patch('hermes_trading.paired_performance_report._canonical_account',side_effect=error):
                    report=paired_performance_report(fixture.root,now=fixture.now+timedelta(days=1))
                self.assertEqual(report['data_status'],'incomplete')
                self.assertEqual(report['source_error_class'],type(error).__name__)
                self.assertIn('source_evidence_missing_or_inconsistent',report['blockers'])
        finally:setup.tearDown()

    def settled_db(self,at,*,settlement=None,input_hash=None):
        from hermes_trading.reflection_supervision import _hash
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
        db.execute('CREATE TABLE paired_batches(batch_id TEXT,status TEXT,payload TEXT,input_hash TEXT,completed TEXT)')
        db.execute('CREATE TABLE paired_settlements(batch_id TEXT,payload TEXT)')
        packet={'created_at':at.isoformat()}
        db.execute('INSERT INTO paired_batches VALUES (?,?,?,?,?)',('one','complete',json.dumps(packet),input_hash or _hash(packet),'{}'))
        if settlement is not None:db.execute('INSERT INTO paired_settlements VALUES (?,?)',('one',json.dumps(settlement)))
        return db

    def test_future_failed_measurement_does_not_contaminate_earlier_window(self):
        from hermes_trading.paired_performance_report import _settled_pairs
        failed={name:{'observation':None,'measurement_error':'missing'} for name in ('adaptive_100','control_100')}
        db=self.settled_db(NOW+timedelta(days=181),settlement=failed)
        try:self.assertEqual(list(_settled_pairs(db,[],NOW,NOW+timedelta(days=180),[],[[],[]])),[])
        finally:db.close()

    def test_complete_batch_cannot_silently_lose_settlement(self):
        from hermes_trading.paired_performance_report import _settled_pairs
        db=self.settled_db(NOW)
        try:
            with self.assertRaisesRegex(ValueError,'settlement'):
                list(_settled_pairs(db,[],NOW,NOW+timedelta(days=1),[],[[],[]]))
        finally:db.close()

    def test_original_packet_hash_must_match_batch_binding(self):
        from hermes_trading.paired_performance_report import _settled_pairs
        db=self.settled_db(NOW,settlement={},input_hash='wrong')
        try:
            with self.assertRaisesRegex(ValueError,'hash'):
                list(_settled_pairs(db,[],NOW,NOW+timedelta(days=1),[],[[],[]]))
        finally:db.close()

    def test_current_batch_cost_reservation_catches_transient_deficit(self):
        from hermes_trading.paired_performance_report import _settled_pairs
        from hermes_trading.paired_cost_stress import FixedOrderCostStress
        from hermes_trading.reflection_supervision import _hash
        from test_paired_cost_stress import FixedCostStressTests
        helper=FixedCostStressTests()
        costs=helper.observation('30','70','6','94')['cost_provenance']
        configs=[{'experiment_id':'test','arm':arm} for arm in ('adaptive','control')]
        names=('adaptive_100','control_100')
        packet={'created_at':NOW.isoformat(),'budgets':{name:{'known_accrued_costs':'6','cost_provenance':costs} for name in names}}
        observation=helper.observation('30','70','6','94')
        observation.update(at=NOW.isoformat(),experiment_id='test',
            mark_provenance={'BTC/EUR':{'bid':'10','observed_at':NOW.isoformat(),'exit_fee_rate':'0','slippage_rate':'0'}})
        settlement={name:{'observation':dict(observation,account_config_hash=_hash(config)),
            'net_equity':'94','measurement_error':None} for name,config in zip(names,configs)}
        final={name:{'finalized':True,'input_hash':_hash(packet),'net_equity':'94'} for name in names}
        db=self.settled_db(NOW,settlement=settlement)
        db.execute('UPDATE paired_batches SET payload=?,input_hash=?,completed=?',
            (json.dumps(packet),_hash(packet),json.dumps(final)))
        def order(asset,side,quantity,at):return {'at':at.isoformat(),'asset':asset,'side':side,
            'quantity':quantity,'fill_price':'10','fee':'0','fee_rate':'0','slippage_rate':'0'}
        engines=[FixedOrderCostStress('100'),FixedOrderCostStress('100')]
        for engine in engines:engine.apply_order(order('ETH/EUR','buy','2',NOW-timedelta(minutes=1)))
        orders=[[order('BTC/EUR','buy','7',NOW),order('ETH/EUR','sell','2',NOW)] for _ in engines]
        try:
            rows=list(_settled_pairs(db,configs,NOW,NOW+timedelta(days=1),engines,orders))
            self.assertEqual(len(rows),1)
            for engine in engines:
                self.assertFalse(engine.cash_feasible)
                self.assertEqual(engine.cash_failures[0]['spendable_cash'],'-2')
                self.assertEqual(engine.stressed_cash,30)
        finally:db.close()

    def test_canonical_fill_cannot_be_omitted_after_last_settlement(self):
        from hermes_trading.paired_performance_report import _settled_pairs
        from hermes_trading.paired_cost_stress import FixedOrderCostStress
        from test_paired_cost_stress import FixedCostStressTests
        from hermes_trading.reflection_supervision import _hash
        end=NOW+timedelta(days=1);at=end-timedelta(seconds=60)
        helper=FixedCostStressTests();observation=helper.observation('100','0','0','100',quantity='0')
        observation.update(at=at.isoformat(),experiment_id='test')
        configs=[{'experiment_id':'test','arm':arm} for arm in ('adaptive','control')]
        names=('adaptive_100','control_100')
        packet={'created_at':at.isoformat(),'budgets':{name:observation for name in names}}
        settlement={name:{'observation':dict(observation,account_config_hash=_hash(config)),
            'measurement_error':None,'net_equity':'100'} for name,config in zip(names,configs)}
        final={name:{'finalized':True,'input_hash':_hash(packet),'net_equity':'100'} for name in names}
        db=self.settled_db(at,settlement=settlement)
        db.execute('UPDATE paired_batches SET payload=?,input_hash=?,completed=?',
            (json.dumps(packet),_hash(packet),json.dumps(final)))
        order=dict(helper.order('buy','10.1','.101'),at=(end-timedelta(seconds=30)).isoformat())
        try:
            with self.assertRaisesRegex(ValueError,'unconsumed'):
                list(_settled_pairs(db,configs,NOW,end,[FixedOrderCostStress('100') for _ in configs],[[order],[]]))
        finally:db.close()

    def test_canonical_reader_uses_original_entry_costs_and_does_not_change_database(self):
        from test_paired_worker import WorkerTests
        from hermes_trading.paired_paper import run_paired_cycle
        from hermes_trading.paired_performance_report import _canonical_account
        fixture=WorkerTests();fixture.setUp()
        try:
            asyncio.run(run_paired_cycle(fixture.root,fixture.market,now=fixture.now))
            path=fixture.root/'adaptive_100'
            original=(path/'strategy.yaml').read_bytes()
            (path/'history').mkdir(exist_ok=True)
            (path/'history/v0001.yaml').write_bytes(original)
            current=yaml.safe_load(original);current['version']='2'
            current['simulation']['taker_fee_rate']='.1'
            (path/'strategy.yaml').write_text(yaml.safe_dump(current))
            before=hashlib.sha256((path/'trading.db').read_bytes()).hexdigest()
            orders,positions,entries=_canonical_account(path,fixture.now)
            self.assertEqual(len(orders),1)
            self.assertEqual(orders[0]['fee_rate'],'0')
            self.assertEqual(entries[0]['position_id'],positions[0]['position_id'])
            self.assertEqual(hashlib.sha256((path/'trading.db').read_bytes()).hexdigest(),before)
        finally:fixture.tearDown()

    def test_prepared_profile_report_does_not_activate_or_create_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            path=root/'manifest.json'
            path.write_text(json.dumps({'schema_version':1,'status':'prepared_draft_not_activated'}))
            before=hashlib.sha256(path.read_bytes()).hexdigest()
            report=paired_performance_report(root,now=NOW)
            self.assertFalse(report['profitability_qualified'])
            self.assertIn('experiment_not_activated',report['blockers'])
            self.assertEqual([p.name for p in root.iterdir()],['manifest.json'])
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)

    def test_missing_manifest_is_blocked_without_creating_a_database(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            report=paired_performance_report(root,now=NOW)
            self.assertFalse(report['profitability_qualified'])
            self.assertEqual(list(root.iterdir()),[])

    def test_operational_checkpoint_cannot_be_a_profit_certificate(self):
        # Real paired-worker setup, but no collection/inference or actual activation.
        from test_paired_worker import WorkerTests
        fixture=WorkerTests();fixture.setUp()
        try:
            report=paired_performance_report(fixture.root,now=fixture.now+timedelta(days=30))
            self.assertFalse(report['profitability_qualified'])
            self.assertFalse(report['eligible_for_real_trading'])
            self.assertIn('assessment_before_day180',report['blockers'])
        finally:fixture.tearDown()
