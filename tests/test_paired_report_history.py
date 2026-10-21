"""Native report equivalence on invented all-eight settled inputs only."""
from contextlib import closing
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import hashlib,json,secrets,shutil,sqlite3,unittest

from hermes_trading import archive_journal as archive
from hermes_trading.control_auth import sign_command
from hermes_trading.paired_inception import initialize_paired_inception
from hermes_trading.paired_packet_storage import store_packet,store_record
from hermes_trading.paired_performance_report import paired_performance_report
from hermes_trading.paired_primary import resolve_primary
from hermes_trading.paired_risk import initial_risk_state
from hermes_trading.persistence.repository import StateRepository
from hermes_trading.reflection_supervision import _hash,_json


class SharedReportHistoryTests(unittest.TestCase):
    def fixture(self,wallet=200):
        from test_paired_inception import PairedInceptionTests
        from test_paired_primary_finance import selected_manifest
        from paired_source_fixture import SyntheticPrimarySources
        f=PairedInceptionTests();f.setUp();self.addCleanup(f.tearDown)
        manifest=selected_manifest(f.fixture.manifest,wallet);primary=resolve_primary(manifest)
        (f.root/'manifest.json').write_text(json.dumps(manifest))
        opening=initialize_paired_inception(f.root,clock=lambda:f.at)
        for profile in manifest['profiles']:
            repo=StateRepository.open(f.root/profile['directory']/'trading.db');repo.migrate();repo.close()
        sources=SyntheticPrimarySources(f.root,manifest)
        path=f.root/primary.profile_directory(manifest,'adaptive')/'paper_publications.sqlite'
        with closing(sqlite3.connect(path)) as db:
            db.executescript('''CREATE TABLE paired_identity(id INTEGER PRIMARY KEY,manifest_hash TEXT);
                CREATE TABLE paired_batches(batch_id TEXT PRIMARY KEY,status TEXT,payload BLOB,input_hash TEXT,completed BLOB);
                CREATE TABLE paired_settlements(batch_id TEXT PRIMARY KEY,payload BLOB);
                CREATE TABLE paired_risk_state(id INTEGER PRIMARY KEY,state TEXT);''')
            db.execute('INSERT INTO paired_identity VALUES(1,?)',(_hash(manifest),))
            db.execute('INSERT INTO paired_risk_state VALUES(1,?)',(_json(initial_risk_state(primary)),))
            for minute in [1]+list(range(5,1441,5)):
                at=f.at+timedelta(minutes=minute)
                observations={name:dict(deepcopy(row),at=at.isoformat(),
                    cost_coverage={'status':'current','as_of':at.isoformat()},
                    execution_venue_protocol='dated_rules_v1',liquidation_complete=True)
                    for name,row in opening['accounts'].items()}
                packet=sources.packet(at,observations,Decimal(10));digest=_hash(packet)
                final={name:dict(finalized=True,input_hash=digest,net_equity=row['net_equity']) for name,row in observations.items()}
                settlement={name:dict(observation=row,measurement_error=None,net_equity=row['net_equity']) for name,row in observations.items()}
                db.execute('INSERT INTO paired_batches VALUES(?,?,?,?,?)',
                    (str(minute),'complete',store_packet(db,packet),digest,store_record(db,final)))
                db.execute('INSERT INTO paired_settlements VALUES(?,?)',(str(minute),store_record(db,settlement)))
            db.commit()
        return f,manifest,path

    def archive(self,f,manifest,path):
        old=f.root/'original-shared.sqlite';shutil.copyfile(path,old)
        experiment=json.loads((path.parent/'wallet_account.json').read_text())['experiment_id']
        request=archive.prepare(path,enabled=True,experiment_id=experiment,release_sha256='b'*64,
            archive_sha256='c'*64,account_snapshots={name:'d'*64 for name in archive.ACCOUNTS},
            copies={'pc':'e'*64,'backup':'f'*64},through_sequence=1)
        keys={name:secrets.token_hex(32) for name in ('pc','backup')};body=_json(request)
        receipts={name:dict(body=body,nonce=request['operation_id'],timestamp=f.at.isoformat(),
            signature=sign_command(key,request['operation_id'],f.at,body)) for name,key in keys.items()}
        archive.acknowledge(path,request['operation_id'],receipts,keys,now=f.at,enabled=True)
        archive.retire(path,request['operation_id'],enabled=True)
        return dict(enabled=True,sources=[dict(path=p,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in (old,path)],
                    cache_root=f.root,max_bytes=32*1024**2,timeout_seconds=30)

    def test_current_only_retired_report_cannot_silently_drop_an_interior_observation(self):
        f,manifest,path=self.fixture()
        baseline=paired_performance_report(f.root,now=f.at+timedelta(days=1))
        self.assertEqual(baseline['data_status'],'measured_conditional_on_recorded_inputs')
        self.archive(f,manifest,path)
        rejected=paired_performance_report(f.root,now=f.at+timedelta(days=1))
        self.assertEqual(rejected['data_status'],'incomplete',rejected)
        self.assertIn('source_evidence_missing_or_inconsistent',rejected['blockers'])

    def test_archived_and_uninterrupted_selected_primary_reports_agree(self):
        for wallet in (50,200):
            with self.subTest(wallet=wallet):
                f,manifest,path=self.fixture(wallet)
                baseline=paired_performance_report(f.root,now=f.at+timedelta(days=1))
                history=self.archive(f,manifest,path)
                before={s['path']:s['path'].read_bytes() for s in history['sources']}
                result=paired_performance_report(f.root,now=f.at+timedelta(days=1),history=history)
                for field in ('data_status','calendar','stress_feasibility','lineages','blockers',
                              'primary_wallet_eur','reporting_protocol','dated_venue_execution_reconciled'):
                    self.assertEqual(result.get(field),baseline.get(field),(field,result))
                self.assertEqual(result['calendar']['base']['observations'],290)
                self.assertEqual(result['calendar']['base']['daily_equity'],[[str(Decimal(wallet)-Decimal('.01'))]*2]*2)
                self.assertEqual(result['calendar']['stress']['daily_equity'],[[str(Decimal(wallet)-Decimal('.02'))]*2]*2)
                self.assertFalse(result['profitability_qualified']);self.assertFalse(result['eligible_for_real_trading'])
                self.assertEqual(before,{s['path']:s['path'].read_bytes() for s in history['sources']})
                for failure in ('disabled','wrong_current','missing'):
                    bad=deepcopy(history)
                    if failure=='disabled':bad['enabled']=False
                    elif failure=='wrong_current':bad['sources']=bad['sources'][:1]
                    else:bad['sources'][0]['path']=f.root/'unavailable.sqlite'
                    rejected=paired_performance_report(f.root,now=f.at+timedelta(days=1),history=bad)
                    self.assertEqual(rejected['data_status'],'incomplete',(failure,rejected))

    def test_lost_archive_metadata_cannot_hide_truncated_history(self):
        f,manifest,path=self.fixture();self.archive(f,manifest,path)
        with closing(sqlite3.connect(path)) as db:
            db.execute('DROP TABLE paired_archive_checkpoint');db.execute('DROP TABLE paired_archive_operations');db.commit()
        report=paired_performance_report(f.root,now=f.at+timedelta(days=1))
        self.assertEqual(report['data_status'],'incomplete')

    def test_extreme_integer_timeout_returns_incomplete_instead_of_crashing(self):
        f,manifest,path=self.fixture();history=self.archive(f,manifest,path)
        try:report=paired_performance_report(f.root,now=f.at+timedelta(days=1),history=dict(history,timeout_seconds=10**1000))
        except OverflowError:self.fail('unbounded integer timeout escaped report validation')
        self.assertEqual(report['data_status'],'incomplete')


if __name__=='__main__':unittest.main()
