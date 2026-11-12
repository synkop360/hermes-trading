from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import hashlib
import json
import unittest

from hermes_trading.positions import PositionEngine
from hermes_trading.persistence.repository import StateRepository
from hermes_trading.strategy import StrategySignal
import test_prospective_execution as fixtures
from venue_fixtures import venue_record


class CurrentVenueExecutionTests(unittest.TestCase):
    tearDown=fixtures.ProspectiveExecutionTests.tearDown
    snapshot=fixtures.ProspectiveExecutionTests.snapshot
    engine=fixtures.ProspectiveExecutionTests.engine
    enter=fixtures.ProspectiveExecutionTests.enter

    def setUp(self):
        fixtures.ProspectiveExecutionTests.setUp(self)
        self.rules['simulation'].update(quote_notional='3',slippage_rate='0',
            minimum_order_quantity='0.001',quantity_decimals=8)

    def market(self, *, offset=1, minimum='0.002', bid='1800', ask='2000', decimals=8):
        snapshot=self.snapshot(bid=bid,ask=ask,received_at=(self.at+timedelta(seconds=offset)).isoformat())
        return replace(snapshot,venue_rules=venue_record('ETH/EUR',self.at+timedelta(seconds=offset),
                       minimum_quantity=minimum,decimals=decimals))

    def original_entry(self):
        result=self.enter(self.market(minimum='0.001',bid='1999'))
        self.assertEqual(result.position.quantity,Decimal('0.0015'))
        return result.position

    def test_current_minimum_blocks_ordinary_and_all_protective_full_exits(self):
        for reason in [None,'paired_archive_stop','paired_storage_stop','paired_drawdown_stop']:
            with self.subTest(reason=reason):
                if reason is not None:
                    self.repo.close();self.tmp.cleanup();self.setUp()
                original=self.original_entry();snapshot=self.market(offset=3)
                if reason is not None:
                    snapshot=replace(snapshot,timeframe='paired-risk:synthetic',candle=replace(snapshot.candle,
                        open_timestamp=self.at+timedelta(seconds=3),close_timestamp=self.at+timedelta(seconds=4),
                        open=Decimal('1800'),high=Decimal('1800'),low=Decimal('1800'),close=Decimal('1800')))
                engine=PositionEngine(self.repo,prospective_at=self.at+timedelta(seconds=4),force_exit_reason=reason,
                    entry_contract_resolver=lambda asset,version:deepcopy(self.rules))
                before=self.repo.export_canonical_tables()
                result=engine.process_candle(snapshot,dict(self.rules,version='2'))
                self.assertEqual(result.orders,());self.assertEqual(result.position,original)
                self.assertEqual(result.reason,'execution_venue_pending')
                self.assertEqual(self.repo.pending_paper_exit(original.position_id)['reason'],reason or 'stop_loss')
                self.assertEqual(len(self.repo.export_canonical_tables()['order_fills']),len(before['order_fills']))
                self.repo.close();self.repo=StateRepository.open(self.path);self.repo.migrate()
                replay=PositionEngine(self.repo,prospective_at=self.at+timedelta(seconds=4),force_exit_reason=reason)
                self.assertEqual(replay.process_candle(snapshot,self.rules).orders,())
                self.assertEqual(self.repo.open_position('ETH/EUR'),original)

    def test_missing_stale_future_or_closed_venue_facts_cannot_fill_entry(self):
        for mutation in ['missing','stale','future','closed']:
            with self.subTest(mutation=mutation):
                snapshot=self.market(bid='1999')
                record=deepcopy(snapshot.venue_rules)
                if mutation=='missing':record=None
                if mutation=='stale':record['received_at']=(self.at-timedelta(minutes=6)).isoformat()
                if mutation=='future':record['received_at']=(self.at+timedelta(minutes=6)).isoformat()
                if mutation=='closed':
                    record['source_pair']['status']='cancel_only'
                    record['source_sha256']=hashlib.sha256(json.dumps(record['source_pair'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
                result=self.enter(replace(snapshot,venue_rules=record))
                self.assertEqual(result.orders,());self.assertIsNone(self.repo.open_position('ETH/EUR'))
                self.repo.close();self.tmp.cleanup();self.setUp()

    def test_entry_rounds_down_and_never_increases_size_to_reach_new_minimum(self):
        self.assertEqual(self.enter(self.market(bid='1999')).orders,())
        self.repo.close();self.tmp.cleanup();self.setUp()
        self.rules['simulation']['quote_notional']='4.000019'
        result=self.enter(self.market(bid='1999'))
        self.assertEqual(result.position.quantity,Decimal('0.00200000'))
        self.assertLessEqual(result.position.quantity*result.position.entry_price,Decimal('4.000019'))

    def test_original_fee_and_version_survive_pending_exit_then_valid_venue(self):
        original=self.original_entry();snapshot=self.market(offset=3)
        engine=PositionEngine(self.repo,prospective_at=self.at+timedelta(seconds=4),
            entry_contract_resolver=lambda asset,version:deepcopy(self.rules))
        self.assertEqual(engine.process_candle(snapshot,self.rules).orders,())
        snapshot=self.market(offset=5,minimum='0.001')
        engine.prospective_at=self.at+timedelta(seconds=6)
        changed=deepcopy(self.rules);changed['version']='2';changed['simulation']['taker_fee_rate']='0'
        result=engine.process_candle(snapshot,changed)
        self.assertEqual(result.position.status.value,'closed');self.assertEqual(result.position.strategy_version,'1')
        self.assertEqual(result.orders[0].fees,Decimal('0.0216000'))
        self.assertEqual(result.position.quantity,original.quantity)

    def test_changed_precision_never_rounds_existing_full_exit_quantity(self):
        original=self.original_entry();snapshot=self.market(offset=3,minimum='0.001',decimals=3)
        result=self.engine(self.at+timedelta(seconds=4)).process_candle(snapshot,self.rules)
        self.assertEqual(result.orders,());self.assertEqual(result.position.quantity,original.quantity)
        self.assertIsNotNone(self.repo.pending_paper_exit(original.position_id))

    def test_partial_leg_or_remainder_below_current_minimum_stays_pending(self):
        for fraction in ('0.2','0.8'):
            with self.subTest(fraction=fraction):
                if fraction!='0.2':
                    self.repo.close();self.tmp.cleanup();self.setUp()
                self.rules['simulation']['quote_notional']='10'
                self.rules['exit']={'partial_close_pct':fraction}
                self.enter(self.market(bid='1999'))
                snapshot=self.market(offset=3,bid='2100',ask='2101')
                signal=StrategySignal('exit','ETH/EUR','partial_exit',Decimal('2100'),snapshot.candle_identity.candle_id)
                result=self.engine(self.at+timedelta(seconds=4)).process_candle(snapshot,self.rules,signal_override=signal)
                self.assertEqual(result.orders,());self.assertEqual(result.position.quantity,Decimal('0.005'))
                self.assertEqual(self.repo.pending_paper_exit(result.position.position_id)['reason'],'partial_exit')
                # A later full stop supersedes either invalid partial and is executable.
                result=self.engine(self.at+timedelta(seconds=6)).process_candle(self.market(offset=5),self.rules)
                self.assertEqual(result.reason,'stop_loss');self.assertEqual(result.position.status.value,'closed')

    def test_missing_stale_or_future_exit_rules_preserve_trigger_until_fresh_metadata(self):
        for mutation in ('missing','stale','future'):
            with self.subTest(mutation=mutation):
                if mutation!='missing':
                    self.repo.close();self.tmp.cleanup();self.setUp()
                original=self.original_entry();snapshot=self.market(offset=3,minimum='0.001')
                record=deepcopy(snapshot.venue_rules)
                if mutation=='missing':record=None
                elif mutation=='stale':record['received_at']=(self.at-timedelta(seconds=300)).isoformat()
                else:record['received_at']=(self.at+timedelta(seconds=5)).isoformat()
                result=self.engine(self.at+timedelta(seconds=4)).process_candle(replace(snapshot,venue_rules=record),self.rules)
                self.assertEqual(result.orders,());self.assertEqual(result.position,original)
                self.assertEqual(self.repo.pending_paper_exit(original.position_id)['reason'],'stop_loss')
                self.repo.close();self.repo=StateRepository.open(self.path);self.repo.migrate()
                result=self.engine(self.at+timedelta(seconds=6)).process_candle(self.market(offset=5,minimum='0.001'),self.rules)
                self.assertEqual(result.reason,'stop_loss');self.assertEqual(result.position.status.value,'closed')


if __name__=='__main__':unittest.main()
