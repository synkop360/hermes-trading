from copy import deepcopy
from decimal import Decimal
import asyncio,base64,hashlib,json,sqlite3,unittest
from datetime import timedelta
import uuid,yaml

from hermes_trading.paired_source_validation import PrimarySourceValidator
from hermes_trading.paired_packet_storage import load_packet
from hermes_trading.paired_paper import _profiles,run_paired_cycle
from hermes_trading.paired_performance_report import _canonical_account


class PrimarySourceValidationTests(unittest.TestCase):
    def setUp(self):
        from test_paired_worker import WorkerTests
        self.fixture=WorkerTests();self.fixture.setUp()
        asyncio.run(run_paired_cycle(self.fixture.root,self.fixture.market,now=self.fixture.now))
        db=sqlite3.connect(self.fixture.root/'adaptive_100/paper_publications.sqlite')
        try:
            self.packet=load_packet(db,db.execute('SELECT payload FROM paired_batches').fetchone()[0])
            settlement=load_packet(db,db.execute('SELECT payload FROM paired_settlements').fetchone()[0])
            self.observation=settlement['adaptive_100']['observation']
        finally:db.close()
        profiles=_profiles(self.fixture.root,self.fixture.manifest)
        self.validator=PrimarySourceValidator(self.fixture.manifest,profiles)
        self.config=profiles[('adaptive',100)][2]
        self.order=_canonical_account(self.fixture.root/'adaptive_100',self.fixture.now)[0][0]
        self.order['strategy_version']='1'
        self.holdings={'ETH/EUR':{'quantity':Decimal(self.order['quantity']),'entry_fee_rate':Decimal(self.order['fee_rate']),
            'slippage_rate':Decimal(self.order['slippage_rate']),'strategy_version':'1'}}
    def tearDown(self):self.fixture.tearDown()

    def test_actual_worker_packet_and_measurement_reconcile(self):
        self.validator.validate_packet(self.packet)
        self.validator.validate_fill(self.packet,'adaptive_100',self.order)
        self.validator.validate_observation(self.packet,'adaptive_100',self.observation,self.holdings)

    def test_canonical_stop_and_source_bound_stress_guard(self):
        from hermes_trading.paired_cost_stress import FixedOrderCostStress
        from hermes_trading.execution_policy import initial_stop_price
        from hermes_trading.entry_contracts import EntryContractResolver
        from hermes_trading.loop import _load_yaml
        from hermes_trading.paired_paper import _snapshot
        self.assertIn('initial_stop_price',self.order)
        strategy=EntryContractResolver(self.fixture.root/'adaptive_100',
            _load_yaml(self.fixture.root/'adaptive_100/strategy.yaml'))('ETH/EUR','1')
        expected=initial_stop_price(_snapshot(self.packet['snapshots']['ETH/EUR']),strategy,
                                    Decimal(self.order['fill_price']))
        self.assertEqual(Decimal(self.order['initial_stop_price']),expected)
        engine=FixedOrderCostStress('100')
        self.assertTrue(hasattr(self.validator,'stressed_prebatch_equity'))
        equity=self.validator.stressed_prebatch_equity(self.packet,'adaptive_100',engine)
        result=self.validator.validate_configured_stress(self.packet,'adaptive_100',self.order,engine,equity)
        self.assertEqual(result['unknowns'],[])
        self.assertEqual(Decimal(result['risk_budget']),equity*Decimal('.0025')*Decimal(str(strategy.get('position_size_r','.5'))))
        engine.apply_order(self.order)
        # Frozen equity remains fixed but current cash/exposure include this fill.
        other=dict(self.order,asset='ETH/EUR')
        result=self.validator.validate_configured_stress(self.packet,'adaptive_100',other,engine,equity)
        self.assertIn('existing_symbol_inventory',result['failures'])

    def test_two_asset_guard_freezes_equity_but_updates_cash_and_exposure(self):
        # Invented conditional guard context, not a worker-admission or cost-source receipt.
        from hermes_trading.paired_cost_stress import FixedOrderCostStress
        from hermes_trading.paired_paper import _encode,_decode
        packet=deepcopy(self.packet)
        budget=_decode(packet['budgets']['adaptive_100']);budget['known_accrued_costs']='47'
        packet['budgets']['adaptive_100']=_encode(budget)
        snapshot=_decode(packet['snapshots']['ETH/EUR']);snapshot['asset']='XRP/EUR'
        packet['snapshots']['XRP/EUR']=_encode(snapshot)
        engine=FixedOrderCostStress('100')
        frozen=self.validator.stressed_prebatch_equity(packet,'adaptive_100',engine)
        first=self.validator.validate_configured_stress(packet,'adaptive_100',self.order,engine,frozen)
        engine.apply_order(self.order)
        second=self.validator.validate_configured_stress(packet,'adaptive_100',
            dict(self.order,asset='XRP/EUR'),engine,frozen)
        self.assertEqual(first['risk_budget'],second['risk_budget'])
        self.assertEqual(Decimal(second['frozen_stressed_equity']),Decimal('6'))
        self.assertEqual(Decimal(first['spendable_cash_before_order']),Decimal('6'))
        self.assertEqual(Decimal(second['spendable_cash_before_order']),Decimal('1'))
        self.assertEqual(Decimal(first['entry_basis_exposure_before_order']),Decimal('0'))
        self.assertEqual(Decimal(second['entry_basis_exposure_before_order']),Decimal('5'))
        self.assertIn('cash',second['failures'])
        self.assertNotIn('existing_symbol_inventory',second['failures'])

    def test_changed_goal_or_strategy_cannot_use_another_binding(self):
        packet=deepcopy(self.packet);packet['bindings']['adaptive_100']['goal_hash']='wrong'
        with self.assertRaisesRegex(ValueError,'binding'):self.validator.validate_packet(packet)
        packet=deepcopy(self.packet);packet['strategies']['adaptive']['version']='999'
        with self.assertRaises(ValueError):self.validator.validate_packet(packet)

    def test_changed_common_source_status_is_rejected(self):
        packet=deepcopy(self.packet);packet['market_sources']['ETH/EUR']['valid']=False
        with self.assertRaisesRegex(ValueError,'status'):self.validator.validate_packet(packet)

    def test_raw_cost_hash_and_declared_costs_must_agree(self):
        packet=deepcopy(self.packet);packet['costs']['adaptive_100']['raw_sha256']='wrong'
        with self.assertRaisesRegex(ValueError,'hash'):self.validator.validate_observation(packet,'adaptive_100',self.observation,self.holdings)
        packet=deepcopy(self.packet);source=packet['costs']['adaptive_100']
        source['data']['totals']['hosting']['amount']='1'
        raw=json.dumps(source['data']).encode()
        source.update(raw_base64=base64.b64encode(raw).decode(),raw_sha256=hashlib.sha256(raw).hexdigest())
        with self.assertRaisesRegex(ValueError,'cost receipt'):self.validator.validate_observation(packet,'adaptive_100',self.observation,self.holdings)

    def test_changed_bid_fee_or_retained_version_is_rejected(self):
        for key,value in (('bid','999999'),('exit_fee_rate','.01')):
            observation=deepcopy(self.observation);observation['mark_provenance']['ETH/EUR'][key]=value
            with self.assertRaisesRegex(ValueError,'liquidation mark'):
                self.validator.validate_observation(self.packet,'adaptive_100',observation,self.holdings)
        observation=deepcopy(self.observation);observation['mark_entry_versions']['ETH/EUR']='2'
        with self.assertRaisesRegex(ValueError,'entry contract'):
            self.validator.validate_observation(self.packet,'adaptive_100',observation,self.holdings)

    def test_fill_must_match_original_quote_and_batch_time(self):
        order=dict(self.order,fill_price='999')
        with self.assertRaisesRegex(ValueError,'fill differs'):self.validator.validate_fill(self.packet,'adaptive_100',order)
        order=dict(self.order,at='2020-01-01T00:00:00+00:00')
        with self.assertRaisesRegex(ValueError,'same-time'):self.validator.validate_fill(self.packet,'adaptive_100',order)

    def test_buy_cannot_bypass_entry_freeze_peer_source_or_risk_halt(self):
        cases=[]
        packet=deepcopy(self.packet);packet['operational_entries_blocked']=True;cases.append(packet)
        packet=deepcopy(self.packet);packet['market_sources']['other/EUR']={'valid':False};cases.append(packet)
        packet=deepcopy(self.packet);packet['risk_state_before_execution']['global_halted']=True;cases.append(packet)
        for packet in cases:
            with self.assertRaisesRegex(ValueError,'entry admission'):
                self.validator.validate_fill(packet,'adaptive_100',self.order)
        # Exit quote validation still applies, but entry freezes permit liquidation.
        packet=deepcopy(self.packet);packet['operational_entries_blocked']=True
        from hermes_trading.paired_paper import _decode
        bid=_decode(packet['snapshots']['ETH/EUR'])['price']['bid']
        order=dict(self.order,side='sell',fill_price=str(Decimal(str(bid))))
        self.validator.validate_fill(packet,'adaptive_100',order,holdings=self.holdings)

    def test_publication_timeline_rejects_retroactive_candidate_packet(self):
        from hermes_trading.paper_publication import build_publication_request,publish_paper_request
        from hermes_trading.paper_strategy_proposal import apply_paper_strategy_proposal
        from hermes_trading.paired_paper import _encode,_decode,_snapshot,market_source_status
        from hermes_trading.reflect import _next_version
        state=self.fixture.root/'adaptive_100';base_bytes=(state/'strategy.yaml').read_bytes()
        base=yaml.safe_load(base_bytes)
        candidate,variable,asset=apply_paper_strategy_proposal(base,
            {'variable':'entry.threshold','asset':None,'value':31},assets=['ETH/EUR'])
        candidate['version']=_next_version(base['version'])
        policy=json.loads((state/'reflection_owner.json').read_text())
        review={'review_id':str(uuid.uuid4()),'outcome':'pending','started_at':self.fixture.now.isoformat(),
            'request':{'goal_sha256':hashlib.sha256((state/'goal.yaml').read_bytes()).hexdigest(),
                       'strategy_sha256':hashlib.sha256(base_bytes).hexdigest(),'strategy':base},
            'decision':{'status':'awaiting_publication','candidate':candidate,'variable':variable,'asset':asset,
                        'based_on_sha256':hashlib.sha256(base_bytes).hexdigest()},
            'response':{'provider':policy['model']['provider'],'model':policy['model']['name']}}
        published=self.fixture.now+timedelta(seconds=1)
        publish_paper_request(state,build_publication_request(review,policy),
                              env={'HERMES_TRADING_MODE':'paper'},now=published)
        validator=PrimarySourceValidator(self.fixture.manifest,_profiles(self.fixture.root,self.fixture.manifest))
        db=sqlite3.connect((state/'paper_publications.sqlite').resolve().as_uri()+'?mode=ro',uri=True)
        try:validator.prepare_publications(db,now=published+timedelta(seconds=5))
        finally:db.close()
        self.assertFalse(validator.publication_provenance_required)
        validator.validate_packet(self.packet)
        early=deepcopy(self.packet);early['strategies']['adaptive']=_encode(candidate)
        early['created_at']=(published+timedelta(seconds=2)).isoformat()
        snapshots={asset:_snapshot(row) for asset,row in early['snapshots'].items()}
        early['market_sources']=market_source_status(snapshots,published+timedelta(seconds=2))
        with self.assertRaisesRegex(ValueError,'causal cutoff'):validator.validate_packet(early)
        later=deepcopy(early);snapshot=_decode(later['snapshots']['ETH/EUR'])
        snapshot['candle']['close_timestamp']=published+timedelta(seconds=1)
        snapshot['price']['received_at']=(published+timedelta(seconds=1)).isoformat()
        later['snapshots']['ETH/EUR']=_encode(snapshot)
        later['market_sources']=market_source_status({'ETH/EUR':_snapshot(later['snapshots']['ETH/EUR'])},published+timedelta(seconds=2))
        validator.validate_packet(later)
        # A changed writer must invalidate a previous successful preparation.
        policy['owner']='deterministic_worker'
        (state/'reflection_owner.json').write_text(json.dumps(policy))
        db=sqlite3.connect((state/'paper_publications.sqlite').resolve().as_uri()+'?mode=ro',uri=True)
        try:validator.prepare_publications(db,now=published+timedelta(seconds=5))
        finally:db.close()
        self.assertTrue(validator.publication_provenance_required)
        self.assertIsNone(validator.publication_timeline)
