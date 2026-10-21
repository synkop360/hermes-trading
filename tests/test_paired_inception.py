from datetime import timedelta
import json,sqlite3,unittest
import asyncio
from copy import deepcopy
from unittest.mock import patch

from hermes_trading.paired_inception import initialize_paired_inception, read_paired_inception


class PairedInceptionTests(unittest.TestCase):
    def setUp(self):
        from test_paired_worker import WorkerTests
        self.fixture=WorkerTests();self.fixture.setUp()
        self.root=self.fixture.root;self.at=self.fixture.now
        for profile in self.fixture.manifest['profiles']:
            path=self.root/profile['directory']
            account=json.loads((path/'wallet_account.json').read_text())
            account['activated_at']=(self.at-timedelta(seconds=10)).isoformat()
            (path/'wallet_account.json').write_text(json.dumps(account))
            costs=json.loads((path/'wallet_costs.json').read_text())
            costs.update(scope_start=account['activated_at'],as_of=self.at.isoformat())
            costs['totals']['hosting']['amount']='.01'
            (path/'wallet_costs.json').write_text(json.dumps(costs))
    def tearDown(self):self.fixture.tearDown()

    def test_fresh_common_receipt_preserves_costs_and_restart_time(self):
        receipt=initialize_paired_inception(self.root,clock=lambda:self.at)
        self.assertEqual(receipt['at'],self.at.isoformat())
        self.assertEqual(len(receipt['accounts']),8)
        self.assertEqual(receipt['accounts']['adaptive_100']['net_equity'],'99.99')
        again=initialize_paired_inception(self.root,clock=lambda:self.at+timedelta(hours=1))
        self.assertEqual(again,receipt)
        self.assertEqual(read_paired_inception(self.root),receipt)
        for profile in self.fixture.manifest['profiles']:
            db=sqlite3.connect(self.root/profile['directory']/'wallet_equity.sqlite')
            try:self.assertEqual(db.execute('SELECT COUNT(*) FROM observations').fetchone()[0],1)
            finally:db.close()

    def test_old_activation_cannot_be_backfilled(self):
        with self.assertRaisesRegex(ValueError,'fresh'):
            initialize_paired_inception(self.root,clock=lambda:self.at+timedelta(minutes=3))
        self.assertFalse((self.root/'adaptive_100/wallet_equity.sqlite').exists())

    def test_unknown_costs_do_not_create_opening_receipt(self):
        path=self.root/'control_200/wallet_costs.json'
        costs=json.loads(path.read_text());costs['totals']['tools']['amount']=None
        costs['totals']['tools']['quality']='missing';path.write_text(json.dumps(costs))
        with self.assertRaisesRegex(ValueError,'cost'):
            initialize_paired_inception(self.root,clock=lambda:self.at)
        with self.assertRaises(ValueError):read_paired_inception(self.root)

    def test_existing_completed_batch_cannot_be_reinitialized(self):
        path=self.root/'adaptive_100/paper_publications.sqlite'
        db=sqlite3.connect(path)
        db.execute('CREATE TABLE paired_batches(batch_id TEXT,status TEXT)')
        db.execute("INSERT INTO paired_batches VALUES ('old','complete')");db.commit();db.close()
        with self.assertRaisesRegex(ValueError,'history'):
            initialize_paired_inception(self.root,clock=lambda:self.at)

    def test_config_change_invalidates_stored_receipt(self):
        initialize_paired_inception(self.root,clock=lambda:self.at)
        path=self.root/'control_50/wallet_account.json'
        account=json.loads(path.read_text());account['extra']='changed';path.write_text(json.dumps(account))
        with self.assertRaisesRegex(ValueError,'binding'):read_paired_inception(self.root)

    def test_interrupted_wallet_copies_recover_original_receipt(self):
        from hermes_trading import paired_inception
        original=paired_inception.record_wallet_observation
        calls=[]
        def interrupt(*args,**kwargs):
            calls.append(1)
            if len(calls)==3:raise OSError('interrupted')
            return original(*args,**kwargs)
        with patch('hermes_trading.paired_inception.record_wallet_observation',side_effect=interrupt):
            with self.assertRaises(OSError):initialize_paired_inception(self.root,clock=lambda:self.at)
        receipt=initialize_paired_inception(self.root,clock=lambda:self.at+timedelta(hours=1))
        self.assertEqual(receipt['at'],self.at.isoformat())

    def test_retry_does_not_rewind_later_wallet_projection(self):
        from hermes_trading.wallet_observer import record_wallet_observation
        receipt=initialize_paired_inception(self.root,clock=lambda:self.at)
        path=self.root/'adaptive_100'
        later=dict(receipt['accounts']['adaptive_100'],at=(self.at+timedelta(minutes=1)).isoformat())
        record_wallet_observation(path,later,now=self.at+timedelta(minutes=1))
        before=(path/'wallet_metrics.json').read_bytes()
        initialize_paired_inception(self.root,clock=lambda:self.at+timedelta(hours=1))
        self.assertEqual((path/'wallet_metrics.json').read_bytes(),before)

    def test_protocol_bound_worker_refuses_incomplete_opening_copies(self):
        from hermes_trading import paired_inception
        from hermes_trading.paired_performance_report import REPORTING_PROTOCOL
        from hermes_trading.paired_worker import run_worker
        self.fixture.manifest['reporting_protocol']=REPORTING_PROTOCOL
        (self.root/'manifest.json').write_text(json.dumps(self.fixture.manifest))
        with patch('hermes_trading.paired_inception.record_wallet_observation',side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):initialize_paired_inception(self.root,clock=lambda:self.at)
        with self.assertRaisesRegex(ValueError,'copies'):
            asyncio.run(run_worker(self.root,collector_factory=lambda:self.fixture.market,clock=lambda:self.at))
        self.assertEqual(self.fixture.market.collect.await_count,0)

    def test_one_day_shared_source_report_uses_real_opening_endpoint(self):
        from hermes_trading.paired_performance_report import REPORTING_PROTOCOL,paired_performance_report
        from hermes_trading.paired_packet_storage import store_record
        from hermes_trading.reflection_supervision import _hash
        from hermes_trading.persistence.repository import StateRepository
        self.fixture.manifest['reporting_protocol']=REPORTING_PROTOCOL
        (self.root/'manifest.json').write_text(json.dumps(self.fixture.manifest))
        receipt=initialize_paired_inception(self.root,clock=lambda:self.at)
        for name in ('adaptive_100','control_100'):
            repo=StateRepository.open(self.root/name/'trading.db');repo.migrate();repo.close()
        db=sqlite3.connect(self.root/'adaptive_100/paper_publications.sqlite')
        db.execute('CREATE TABLE paired_identity(id INTEGER PRIMARY KEY,manifest_hash TEXT)')
        db.execute('INSERT INTO paired_identity VALUES (1,?)',(_hash(self.fixture.manifest),))
        db.execute('CREATE TABLE paired_batches(batch_id TEXT,status TEXT,payload BLOB,input_hash TEXT,completed BLOB)')
        db.execute('CREATE TABLE paired_settlements(batch_id TEXT,payload BLOB)')
        from paired_source_fixture import SyntheticPrimarySources
        from decimal import Decimal
        sources=SyntheticPrimarySources(self.root,self.fixture.manifest)
        for minute in range(5,1441,5):
            at=self.at+timedelta(minutes=minute)
            observations={name:dict(deepcopy(receipt['accounts'][name]),at=at.isoformat())
                          for name in ('adaptive_100','control_100')}
            for row in observations.values():
                row['cost_coverage']={'status':'current','as_of':at.isoformat()}
                row.update(execution_venue_protocol='dated_rules_v1',liquidation_complete=True)
            packet=sources.packet(at,observations,Decimal('10'))
            digest=_hash(packet)
            final={name:{'finalized':True,'input_hash':digest,'net_equity':row['net_equity']}
                   for name,row in observations.items()}
            settlement={name:{'observation':row,'measurement_error':None,'net_equity':row['net_equity']}
                        for name,row in observations.items()}
            db.execute('INSERT INTO paired_batches VALUES (?,?,?,?,?)',
                (str(minute),'complete',store_record(db,packet),digest,store_record(db,final)))
            db.execute('INSERT INTO paired_settlements VALUES (?,?)',(str(minute),store_record(db,settlement)))
        db.commit();db.close()
        report=paired_performance_report(self.root,now=self.at+timedelta(days=1))
        self.assertEqual(report['data_status'],'measured_conditional_on_recorded_inputs')
        self.assertEqual(report['inception_at'],self.at.isoformat())
        self.assertEqual(report['calendar']['base']['daily_equity'],[['99.99','99.99']]*2)
        self.assertEqual(report['calendar']['stress']['daily_equity'],[['99.98','99.98']]*2)
        self.assertFalse(report['profitability_qualified'])
        # A coherent batch/summary hash cannot hide corrupted raw cost bytes.
        from hermes_trading.paired_packet_storage import load_packet
        db=sqlite3.connect(self.root/'adaptive_100/paper_publications.sqlite')
        try:
            db.execute('BEGIN')
            packet,final=db.execute("SELECT payload,completed FROM paired_batches WHERE batch_id='5'").fetchone()
            packet=load_packet(db,packet);final=load_packet(db,final)
            packet['costs']['adaptive_100']['raw_sha256']='wrong'
            digest=_hash(packet)
            for item in final.values():item['input_hash']=digest
            db.execute("UPDATE paired_batches SET payload=?,input_hash=?,completed=? WHERE batch_id='5'",
                       (store_record(db,packet),digest,store_record(db,final)))
            db.commit()
        finally:db.close()
        invalid=paired_performance_report(self.root,now=self.at+timedelta(days=1))
        self.assertEqual(invalid['data_status'],'incomplete')

    def test_worker_deadline_uses_actual_delayed_inception(self):
        from hermes_trading.paired_performance_report import REPORTING_PROTOCOL
        from hermes_trading.paired_worker import run_worker
        for profile in self.fixture.manifest['profiles']:
            path=self.root/profile['directory']
            config=json.loads((path/'wallet_account.json').read_text())
            config['activated_at']=(self.at-timedelta(seconds=110)).isoformat()
            (path/'wallet_account.json').write_text(json.dumps(config))
            costs=json.loads((path/'wallet_costs.json').read_text());costs['scope_start']=config['activated_at']
            (path/'wallet_costs.json').write_text(json.dumps(costs))
        self.fixture.manifest['reporting_protocol']=REPORTING_PROTOCOL
        (self.root/'manifest.json').write_text(json.dumps(self.fixture.manifest))
        limits=self.fixture.limits;limits['max_runtime_seconds']=180*86400
        (self.root/'worker_limits.json').write_text(json.dumps(limits))
        receipt=initialize_paired_inception(self.root,clock=lambda:self.at)
        tick=[self.at+timedelta(seconds=1)]
        async def advance(seconds):tick[0]+=timedelta(seconds=seconds)
        state=asyncio.run(run_worker(self.root,collector_factory=lambda:self.fixture.market,
                                     clock=lambda:tick[0],sleep=advance))
        self.assertEqual(state['deadline'],(self.at+timedelta(days=180)).isoformat())
        self.assertEqual(state['activated_at'],receipt['at'])
        self.assertEqual(state['administrative_activated_at'],(self.at-timedelta(seconds=110)).isoformat())
