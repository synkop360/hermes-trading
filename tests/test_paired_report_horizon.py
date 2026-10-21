"""Synthetic source/accounting integration; never a strategy backtest."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
import hashlib,json,sqlite3,unittest
import yaml

from hermes_trading.domain import Decision,OrderIntent,OrderRecord,OrderStatus,Position,PositionStatus
from hermes_trading.paired_inception import initialize_paired_inception
from hermes_trading.paired_packet_storage import store_record
from hermes_trading.paired_performance_report import REPORTING_PROTOCOL,paired_performance_report
from hermes_trading.persistence.repository import StateRepository
from hermes_trading.reflection_supervision import _hash


class FullHorizonReportTests(unittest.TestCase):
    def setUp(self):
        from test_paired_inception import PairedInceptionTests
        self.setup=PairedInceptionTests();self.setup.setUp()
        self.root=self.setup.root;self.at=self.setup.at;self.manifest=self.setup.fixture.manifest
        self.manifest['reporting_protocol']=REPORTING_PROTOCOL
        for profile in self.manifest['profiles']:
            path=self.root/profile['directory']/'strategy.yaml'
            strategy=yaml.safe_load(path.read_text())
            strategy['simulation'].update(taker_fee_rate='.008',slippage_rate='.001')
            path.write_text(yaml.safe_dump(strategy))
            profile['strategy_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        self.opening=initialize_paired_inception(self.root,clock=lambda:self.at)
        self.events={name:{} for name in ('adaptive_100','control_100')}
        for name in self.events:self._original_orders(name)
        from paired_source_fixture import SyntheticPrimarySources
        self.sources=SyntheticPrimarySources(self.root,self.manifest)

    def tearDown(self):self.setup.tearDown()

    def _original_orders(self,name):
        path=self.root/name;original=(path/'strategy.yaml').read_bytes()
        if name=='adaptive_100':
            (path/'history').mkdir(exist_ok=True)
            (path/'history/v0001.yaml').write_bytes(original)
            current=yaml.safe_load(original);current['version']='2'
            current['simulation']['taker_fee_rate']='.01'
            (path/'strategy.yaml').write_text(yaml.safe_dump(current))
        repo=StateRepository.open(path/'trading.db');repo.migrate()
        def fill(position,side,quantity,minute,price,rate,reason):
            stamp=(self.at+timedelta(minutes=minute)).isoformat()
            identity=name+':'+position.position_id+':'+side
            fee=quantity*price*rate
            repo.record_decision(Decision(identity,'ETH/EUR',side,reason,identity,
                                          position.strategy_version,price,stamp))
            repo.create_order_intent(OrderIntent(identity,identity,'ETH/EUR',side,'market',
                quantity,price,stamp,position_id=position.position_id))
            repo.upsert_order(OrderRecord(identity,identity,'ETH/EUR',side,'market',OrderStatus.FILLED,
                quantity,quantity,price,fee,stamp,stamp))
            self.events[name].setdefault(minute,[]).append({'side':side,'quantity':quantity,
                'price':price,'fee':fee,'rate':rate,'version':position.strategy_version})
        try:
            for index,day in enumerate(range(1,180,3)):
                opened=day*1440+5;closed=(day+1)*1440+5
                rate=D('.01') if name=='adaptive_100' and day>90 else D('.008')
                version='2' if name=='adaptive_100' and day>90 else '1'
                partial=index in (0,30)
                q=D('.05') if partial else D('.1')
                pnl=q*(D('10.989')*(1-rate)-D('10.01')*(1+rate))
                position=Position('root-'+str(index),'ETH/EUR',PositionStatus.CLOSED,q,D('10.01'),
                    D('9'),version,(self.at+timedelta(minutes=opened)).isoformat(),
                    (self.at+timedelta(minutes=closed)).isoformat(),pnl,q*D('20.999')*rate,
                    partial_exit_count=int(partial))
                repo.save_position(position)
                fill(position,'buy',D('.1'),opened,D('10.01'),rate,'synthetic_entry')
                if partial:
                    leg=replace(position,position_id='leg-'+str(index),
                        closed_at=(self.at+timedelta(minutes=opened+720)).isoformat(),partial_exit_count=0)
                    repo.save_position(leg)
                    fill(leg,'sell',q,opened+720,D('10.989'),rate,'partial_exit')
                fill(position,'sell',q,closed,D('10.989'),rate,'take_profit')
            # The mutable canonical position has a future close. At day180 its
            # original buy is filled, inventory remains, and no exit may leak in.
            rate=D('.01') if name=='adaptive_100' else D('.008')
            version='2' if name=='adaptive_100' else '1'
            opened=180*1440-720;closed=181*1440+5
            position=Position('future-close','ETH/EUR',PositionStatus.CLOSED,D('.1'),D('10.01'),
                D('9'),version,(self.at+timedelta(minutes=opened)).isoformat(),
                (self.at+timedelta(minutes=closed)).isoformat(),D('0'),D('0'))
            repo.save_position(position)
            fill(position,'buy',D('.1'),opened,D('10.01'),rate,'synthetic_entry')
            fill(position,'sell',D('.1'),closed,D('10.989'),rate,'take_profit')
        finally:repo.close()

    def _source(self):
        path=self.root/'adaptive_100/paper_publications.sqlite'
        db=sqlite3.connect(path)
        try:
            db.execute('CREATE TABLE paired_identity(id INTEGER PRIMARY KEY,manifest_hash TEXT)')
            db.execute('INSERT INTO paired_identity VALUES (1,?)',(_hash(self.manifest),))
            db.execute('CREATE TABLE paired_batches(batch_id TEXT,status TEXT,payload BLOB,input_hash TEXT,completed BLOB)')
            db.execute('CREATE TABLE paired_settlements(batch_id TEXT,payload BLOB)')
            cash={name:D('100') for name in self.events};inventory={name:None for name in self.events}
            def observation(name,at,bid):
                result=deepcopy(self.opening['accounts'][name]);holding=inventory[name]
                liquidation=D(0) if holding is None else holding['quantity']*bid*D('.999')*(1-holding['rate'])
                basis=D(0) if holding is None else holding['quantity']*holding['price']
                entry_fees=D(0) if holding is None else basis*holding['rate']
                net=cash[name]+liquidation-D('.01')
                result.update(at=at.isoformat(),trading_cash=str(cash[name]),
                    open_basis=str(basis),open_entry_fees=str(entry_fees),
                    closed_net_pnl=str(cash[name]-100+basis+entry_fees),
                    open_liquidation_value=str(liquidation),net_equity=str(net),known_cost_equity=str(net),
                    cost_coverage={'status':'current','as_of':at.isoformat()},
                    execution_venue_protocol='dated_rules_v1',liquidation_complete=True,
                    mark_provenance={} if holding is None else {'ETH/EUR':{'bid':str(bid),
                        'observed_at':at.isoformat(),'exit_fee_rate':str(holding['rate']),'slippage_rate':'.001',
                        'source':'synthetic_received_bid','venue_rules':__import__('venue_fixtures').venue_record('ETH/EUR',at)}},
                    mark_entry_versions={} if holding is None else {'ETH/EUR':holding['version']})
                return result
            for minute in range(5,180*1440+1,5):
                at=self.at+timedelta(minutes=minute)
                bid=D('11') if any(event['side']=='sell' for event in self.events['control_100'].get(minute,[])) else D('10')
                budgets={name:observation(name,at,bid) for name in self.events}
                for name in self.events:
                    for event in self.events[name].get(minute,[]):
                        if event['side']=='buy':
                            cash[name]-=event['quantity']*event['price']+event['fee']
                            inventory[name]=dict(event)
                        else:
                            cash[name]+=event['quantity']*event['price']-event['fee']
                            inventory[name]['quantity']-=event['quantity']
                            if inventory[name]['quantity']==0:inventory[name]=None
                observations={name:observation(name,at,bid) for name in self.events}
                packet=self.sources.packet(at,budgets,bid,adaptive_version='2' if minute>=90*1440 else '1')
                digest=_hash(packet)
                final={name:{'finalized':True,'input_hash':digest,'net_equity':row['net_equity']}
                       for name,row in observations.items()}
                settlement={name:{'observation':row,'measurement_error':None,'net_equity':row['net_equity']}
                            for name,row in observations.items()}
                db.execute('INSERT INTO paired_batches VALUES (?,?,?,?,?)',
                    (str(minute),'complete',store_record(db,packet),digest,store_record(db,final)))
                db.execute('INSERT INTO paired_settlements VALUES (?,?)',(str(minute),store_record(db,settlement)))
            # A later failed batch must not poison the bounded first180 days.
            future={'created_at':(self.at+timedelta(days=181)).isoformat()}
            failed={name:{'observation':None,'measurement_error':'future failure'} for name in self.events}
            db.execute('INSERT INTO paired_batches VALUES (?,?,?,?,?)',
                ('future','complete',store_record(db,future),_hash(future),store_record(db,{})))
            db.execute('INSERT INTO paired_settlements VALUES (?,?)',('future',store_record(db,failed)))
            db.commit()
        finally:db.close()
        return path

    def test_full_horizon_original_versions_partials_open_inventory_and_statistics(self):
        source=self._source()
        paths=[source]+[self.root/name/'trading.db' for name in self.events]
        before={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        report=paired_performance_report(self.root,now=self.at+timedelta(days=181))
        self.assertEqual(report['data_status'],'measured_conditional_on_recorded_inputs')
        self.assertEqual(report['kind'],'final_assessment')
        self.assertEqual([D(value) for value in report['calendar']['base']['daily_equity'][-1]],
                         [D('104.708054'),D('104.838048')])
        self.assertEqual([D(value) for value in report['calendar']['stress']['daily_equity'][-1]],
                         [D('103.416216'),D('103.676192')])
        for arm in ('adaptive','control'):
            self.assertEqual(report['lineages'][arm]['completed_by_90day_block'],[30,30])
            self.assertEqual(report['lineages'][arm]['completed'],60)
        for scenario in ('base','stress'):
            calendar=report['calendar'][scenario]
            self.assertEqual(len(calendar['daily_equity']),181)
            self.assertEqual(calendar['observations'],180*288+1)
            self.assertTrue(all(block['adaptive_positive'] for block in calendar['chronological_growth_blocks']))
            for length in ('7','14'):
                statistics=report['statistics'][scenario][length]
                self.assertEqual(statistics['days'],180)
                self.assertEqual(statistics['resamples'],10000)
                self.assertGreater(statistics['adaptive']['lower_95'],0)
                self.assertLess(statistics['adaptive_minus_control']['lower_95'],0)
        self.assertFalse(report['profitability_qualified'])
        self.assertFalse(report['eligible_for_real_trading'])
        self.assertIn('statistical_assumptions_and_coverage_not_independently_validated',report['blockers'])
        self.assertIn('adaptive_publication_provenance_not_reconciled',report['blockers'])
        self.assertFalse(report['adaptive_publication_provenance_reconciled'])
        self.assertTrue(report['primary_quotes_costs_and_retained_contracts_reconciled'])
        for scenario in ('base','stress'):
            checks=report['stress_feasibility'][scenario]
            self.assertTrue(checks['configured_policy_assessment_complete'])
            self.assertTrue(all(count>=120 for count in checks['configured_orders_checked']))
            self.assertTrue(all(checks['configured_policy_unknowns']))
            self.assertTrue(all(value is not True for value in checks['configured_policy_feasible']))
        self.assertIn('stressed_original_orders_configured_policy_evidence_missing',report['blockers'])
        self.assertEqual(before,{str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})
        # Even a single missing early settlement is never silently bridged.
        db=sqlite3.connect(source);db.execute("DELETE FROM paired_settlements WHERE batch_id='5'");db.commit();db.close()
        broken=paired_performance_report(self.root,now=self.at+timedelta(days=180))
        self.assertEqual(broken['data_status'],'incomplete')
        self.assertIn('source_evidence_missing_or_inconsistent',broken['blockers'])
