"""Selected-primary opening, common execution and reporting on invented inputs."""
from copy import deepcopy
from contextlib import closing
from datetime import timedelta
from decimal import Decimal
import json,sqlite3,unittest
from unittest.mock import patch

from hermes_trading.paired_primary import resolve_primary,reporting_protocol_for
from hermes_trading.paired_performance_report import REPORTING_PROTOCOL,paired_performance_report
from hermes_trading.paired_risk import initial_risk_state
from hermes_trading.paired_inception import initialize_paired_inception,read_paired_inception
from hermes_trading.paired_paper import run_paired_cycle,_profiles
from hermes_trading.paired_source_validation import PrimarySourceValidator


def selected_manifest(manifest,wallet=200,rationale='a'*64):
    value=deepcopy(manifest);value.pop('reporting_protocol',None)
    value['primary_wallet_eur']=wallet
    value['primary_selection']=dict(schema_version=1,purpose='paired_primary_selection_v1',wallet_eur=wallet,rationale_sha256=rationale)
    if 'steering' in value:value['steering']['review_wallet']=f'adaptive_{wallet}'
    value['reporting_protocol']=reporting_protocol_for(value,REPORTING_PROTOCOL)
    return value


class SelectedPrimaryFinanceTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self,wallet):
        from test_paired_inception import PairedInceptionTests
        fixture=PairedInceptionTests();fixture.setUp();self.addCleanup(fixture.tearDown)
        fixture.fixture.manifest=selected_manifest(fixture.fixture.manifest,wallet)
        (fixture.root/'manifest.json').write_text(json.dumps(fixture.fixture.manifest))
        return fixture

    async def test_selected_opening_common_execution_and_report_preserve_all_eight_accounts(self):
        for wallet in (50,100,200):
            with self.subTest(wallet=wallet):
                f=self.fixture(wallet);manifest=f.fixture.manifest;p=resolve_primary(manifest)
                opening=initialize_paired_inception(f.root,clock=lambda:f.at)
                selected=f.root/f'adaptive_{wallet}'/'paper_publications.sqlite'
                self.assertTrue(selected.exists(),'opening journal is in the wrong primary account')
                self.assertEqual(read_paired_inception(f.root),opening)
                result=await run_paired_cycle(f.root,f.fixture.market,now=f.at+timedelta(seconds=1))
                self.assertEqual(len(result['accounts']),8);self.assertEqual(f.fixture.market.collect.await_count,1)
                self.assertEqual(result['risk_state']['primary_wallet_eur'],wallet)
                self.assertEqual(result['risk_state']['primary_selection_sha256'],p.selection_sha256)
                self.assertEqual(result['risk_state']['accounts'][f'adaptive_{wallet}']['peak'],str(wallet))
                source=PrimarySourceValidator(manifest,_profiles(f.root,manifest))
                self.assertEqual(set(source.paths),{f'adaptive_{wallet}',f'control_{wallet}'})
                from hermes_trading.paired_packet_storage import load_packet
                from hermes_trading.paired_performance_report import _canonical_account
                with closing(sqlite3.connect(selected)) as db:
                    packet=load_packet(db,db.execute('SELECT payload FROM paired_batches').fetchone()[0])
                    source.prepare_publications(db,now=f.at+timedelta(seconds=2))
                source.validate_packet(packet)
                for name in source.paths:
                    orders,_,_=_canonical_account(source.paths[name],f.at+timedelta(seconds=2))
                    self.assertTrue(orders,'selected primary fixture did not exercise an original fill')
                    for order in orders:source.validate_fill(packet,name,order,holdings={})
                report=paired_performance_report(f.root,now=f.at+timedelta(seconds=2))
                self.assertEqual(report['schema_version'],2);self.assertEqual(report['primary_wallet_eur'],wallet)
                self.assertEqual(report['reporting_protocol'],manifest['reporting_protocol'])
                self.assertFalse(report['profitability_qualified']);self.assertFalse(report['eligible_for_real_trading'])

    async def test_mismatched_persisted_risk_and_changed_selection_fail_before_recollection(self):
        f=self.fixture(200);initialize_paired_inception(f.root,clock=lambda:f.at)
        await run_paired_cycle(f.root,f.fixture.market,now=f.at+timedelta(seconds=1))
        path=f.root/'adaptive_200/paper_publications.sqlite'
        with closing(sqlite3.connect(path)) as db:
            db.execute('UPDATE paired_risk_state SET state=?',(json.dumps(initial_risk_state()),));db.commit()
        with self.assertRaisesRegex(ValueError,'risk|primary'):
            await run_paired_cycle(f.root,f.fixture.market,now=f.at+timedelta(seconds=2))
        self.assertEqual(f.fixture.market.collect.await_count,1)
        changed=selected_manifest(f.fixture.manifest,200,rationale='b'*64)
        (f.root/'manifest.json').write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError,'manifest|binding'):read_paired_inception(f.root)

    def test_new_reporting_method_is_rejected_before_profile_io(self):
        f=self.fixture(200);bad=deepcopy(f.fixture.manifest);bad['reporting_protocol']['assessment_days']=90
        with patch('hermes_trading.paired_paper.load_wallet_config',side_effect=AssertionError('profile I/O before protocol check')):
            with self.assertRaisesRegex(ValueError,'reporting|protocol'):_profiles(f.root,bad)

    async def test_selected_and_nonselected_breaches_stop_the_correct_actual_account_cohort(self):
        for breached in (100,200):
            f=self.fixture(200);initialize_paired_inception(f.root,clock=lambda:f.at)
            await run_paired_cycle(f.root,f.fixture.market,now=f.at+timedelta(seconds=1))
            path=f.root/f'control_{breached}'/'wallet_costs.json'
            costs=json.loads(path.read_text());costs['totals']['hosting']['amount']=str(Decimal(breached)*Decimal('.03'))
            path.write_text(json.dumps(costs))
            result=await run_paired_cycle(f.root,f.fixture.market,now=f.at+timedelta(seconds=2))
            self.assertEqual(result['risk_state']['halted_wallets'],[breached])
            self.assertEqual(result['risk_state']['global_halted'],breached==200)
            self.assertEqual(len(result['accounts']),8)

    def test_selected_one_day_report_uses_real_opening_keys_capital_and_both_cost_books(self):
        from hermes_trading.paired_packet_storage import store_record
        from hermes_trading.persistence.repository import StateRepository
        from hermes_trading.reflection_supervision import _hash
        from paired_source_fixture import SyntheticPrimarySources
        for wallet in (50,200):
            f=self.fixture(wallet);manifest=f.fixture.manifest;primary=resolve_primary(manifest)
            opening=initialize_paired_inception(f.root,clock=lambda:f.at)
            names=[primary.profile_key(arm) for arm in ('adaptive','control')]
            for name in names:
                repo=StateRepository.open(f.root/name/'trading.db');repo.migrate();repo.close()
            sources=SyntheticPrimarySources(f.root,manifest)
            with closing(sqlite3.connect(f.root/names[0]/'paper_publications.sqlite')) as db:
                db.execute('CREATE TABLE paired_identity(id INTEGER PRIMARY KEY,manifest_hash TEXT)')
                db.execute('INSERT INTO paired_identity VALUES(1,?)',(_hash(manifest),))
                db.execute('CREATE TABLE paired_batches(batch_id TEXT,status TEXT,payload BLOB,input_hash TEXT,completed BLOB)')
                db.execute('CREATE TABLE paired_settlements(batch_id TEXT,payload BLOB)')
                db.execute('CREATE TABLE paired_risk_state(id INTEGER PRIMARY KEY,state TEXT)')
                db.execute('INSERT INTO paired_risk_state VALUES(1,?)',(json.dumps(initial_risk_state(primary)),))
                for minute in range(5,1441,5):
                    at=f.at+timedelta(minutes=minute)
                    observations={name:dict(deepcopy(opening['accounts'][name]),at=at.isoformat(),
                        cost_coverage={'status':'current','as_of':at.isoformat()},execution_venue_protocol='dated_rules_v1',
                        liquidation_complete=True) for name in names}
                    packet=sources.packet(at,observations,Decimal(10));digest=_hash(packet)
                    final={name:dict(finalized=True,input_hash=digest,net_equity=row['net_equity']) for name,row in observations.items()}
                    settlement={name:dict(observation=row,measurement_error=None,net_equity=row['net_equity']) for name,row in observations.items()}
                    db.execute('INSERT INTO paired_batches VALUES(?,?,?,?,?)',(str(minute),'complete',store_record(db,packet),digest,store_record(db,final)))
                    db.execute('INSERT INTO paired_settlements VALUES(?,?)',(str(minute),store_record(db,settlement)))
                db.commit()
            report=paired_performance_report(f.root,now=f.at+timedelta(days=1))
            self.assertEqual(report['data_status'],'measured_conditional_on_recorded_inputs',report)
            self.assertEqual(report['calendar']['base']['daily_equity'],[[str(Decimal(wallet)-Decimal('.01'))]*2]*2)
            self.assertEqual(report['calendar']['stress']['daily_equity'],[[str(Decimal(wallet)-Decimal('.02'))]*2]*2)
            self.assertFalse(report['profitability_qualified'])
            from hermes_trading.paired_packet_storage import load_packet
            for bad_risk in (initial_risk_state(),initial_risk_state(type(primary)(wallet,'b'*64))):
                with closing(sqlite3.connect(f.root/names[0]/'paper_publications.sqlite')) as db:
                    db.execute('UPDATE paired_risk_state SET state=?',(json.dumps(bad_risk),));db.commit()
                rejected=paired_performance_report(f.root,now=f.at+timedelta(days=1))
                with self.subTest(wallet=wallet,case='persisted-risk',schema=bad_risk['schema_version']):
                    self.assertNotEqual(rejected.get('data_status'),'measured_conditional_on_recorded_inputs',rejected)
                with closing(sqlite3.connect(f.root/names[0]/'paper_publications.sqlite')) as db:
                    db.execute('UPDATE paired_risk_state SET state=?',(json.dumps(initial_risk_state(primary)),))
                    row=db.execute('SELECT rowid,payload,completed FROM paired_batches ORDER BY rowid DESC LIMIT 1').fetchone()
                    packet=load_packet(db,row[1]);completed=load_packet(db,row[2]);packet['risk_state_before_execution']=bad_risk
                    digest=_hash(packet)
                    for value in completed.values():value['input_hash']=digest
                    db.execute('UPDATE paired_batches SET payload=?,input_hash=?,completed=? WHERE rowid=?',
                        (store_record(db,packet),digest,store_record(db,completed),row[0]));db.commit()
                rejected=paired_performance_report(f.root,now=f.at+timedelta(days=1))
                with self.subTest(wallet=wallet,case='no-order-packet-risk',schema=bad_risk['schema_version']):
                    self.assertNotEqual(rejected.get('data_status'),'measured_conditional_on_recorded_inputs',rejected)
                with closing(sqlite3.connect(f.root/names[0]/'paper_publications.sqlite')) as db:
                    packet['risk_state_before_execution']=initial_risk_state(primary);digest=_hash(packet)
                    for value in completed.values():value['input_hash']=digest
                    db.execute('BEGIN')
                    db.execute('UPDATE paired_batches SET payload=?,input_hash=?,completed=? WHERE rowid=?',
                        (store_record(db,packet),digest,store_record(db,completed),row[0]));db.commit()


class SelectedCalendarTests(unittest.TestCase):
    def test_declared_capital_sets_initial_peak_and_rejects_cross_wallet_observations(self):
        from test_paired_statistics import PairedStatisticsTests,START
        from hermes_trading.paired_statistics import aligned_calendar_equity
        for wallet in (50,100,150,200):
            rows=list(PairedStatisticsTests().pairs())
            for pair in rows:
                for row in pair:
                    row.update(starting_balance=str(wallet),net_equity=str(Decimal(wallet)*Decimal('.98')),
                               trading_cash=str(Decimal(wallet)*Decimal('.98')))
            report=aligned_calendar_equity(rows,start=START,days=2,starting_balance=str(wallet))
            self.assertEqual([Decimal(v) for v in report['max_drawdown']],[Decimal('.02')]*2)
            rows[0][0]['starting_balance']=str(wallet+1)
            with self.assertRaisesRegex(ValueError,'capital'):
                aligned_calendar_equity(rows,start=START,days=2,starting_balance=str(wallet))


if __name__=='__main__':unittest.main()
