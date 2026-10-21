"""Selected primary worker, snapshot and isolated launcher correctness only."""
import base64,json,sqlite3,unittest
from contextlib import closing,nullcontext
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from hermes_trading.paired_primary import resolve_primary,PrimarySelection
from hermes_trading.reflection_supervision import _hash
from hermes_trading.supervisor_sync import capture_startup_release,make_snapshot,validate_snapshot
from test_paired_primary_finance import selected_manifest


class SelectedWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_secondary_pair_halt_keeps_selected_worker_snapshot_and_supervision_ready(self):
        from decimal import Decimal
        from test_paired_primary_archive import fresh_selected
        for wallet in (50,200):
            f,p=fresh_selected(self,wallet);f.limits['max_cycles']=2
            (f.root/'worker_limits.json').write_text(json.dumps(f.limits))
            path=f.root/'control_100/wallet_costs.json';costs=json.loads(path.read_text())
            costs['totals']['hosting']['amount']=str(Decimal(100)*Decimal('.03'));path.write_text(json.dumps(costs))
            account=json.loads((f.primary/'wallet_account.json').read_text());seen=[]
            async def observe(seconds):
                heartbeat=json.loads((f.primary/'heartbeat.json').read_text())
                self.assertEqual(heartbeat['status'],'ok',heartbeat)
                self.assertTrue(heartbeat['accounts']['control_100']['halted'])
                snapshot=make_snapshot(f.primary,now=f.at)
                validate_snapshot(snapshot,f.policy,now=f.at,expected_primary=p,expected_account_hash=_hash(account))
                seen.append(snapshot);await f.sleep(seconds)
            result=await f.invoke(sleep=observe)
            self.assertEqual(result['completed_batches'],2);self.assertEqual(len(seen),2)

    async def test_worker_and_real_snapshot_use_selected_opening_and_account(self):
        from test_paired_inception import PairedInceptionTests
        from hermes_trading.paired_inception import initialize_paired_inception
        from hermes_trading.paired_worker import run_worker
        for wallet in (50,200):
            with self.subTest(wallet=wallet):
                f=PairedInceptionTests();f.setUp();self.addCleanup(f.tearDown)
                manifest=selected_manifest(f.fixture.manifest,wallet);p=resolve_primary(manifest)
                (f.root/'manifest.json').write_text(json.dumps(manifest));primary=f.root/p.profile_key('adaptive')
                for name in ('reflection_owner.json','model_access.json'):
                    (primary/name).write_bytes((f.fixture.primary/name).read_bytes())
                limits=dict(f.fixture.limits,max_cycles=2)
                (f.root/'worker_limits.json').write_text(json.dumps(limits))
                initialize_paired_inception(f.root,clock=lambda:f.at);tick=[f.at+timedelta(seconds=1)];seen=[]
                account=json.loads((primary/'wallet_account.json').read_text())
                async def wait(seconds):
                    snap=make_snapshot(primary,now=tick[0]);self.assertEqual(snap['schema_version'],2)
                    files=validate_snapshot(snap,f.fixture.policy,now=tick[0],expected_primary=p,expected_account_hash=_hash(account))
                    heartbeat=json.loads(files['heartbeat.json']);release=heartbeat['worker_release']
                    self.assertEqual(release['primary_wallet_eur'],wallet)
                    self.assertEqual(release['primary_selection_sha256'],p.selection_sha256)
                    self.assertEqual(release['account_hash'],_hash(account));seen.append(snap)
                    tick[0]+=timedelta(seconds=seconds)
                state=await run_worker(f.root,collector_factory=lambda:f.fixture.market,clock=lambda:tick[0],sleep=wait)
                self.assertEqual(state['completed_batches'],2);self.assertEqual(len(seen),2)
                self.assertEqual(f.fixture.market.collect.await_count,2)
                self.assertTrue((primary/'heartbeat.json').exists())


class SelectedSnapshotTests(unittest.TestCase):
    def setUp(self):
        from test_supervisor_sync import SnapshotTests
        self.f=SnapshotTests();self.f.setUp();self.addCleanup(self.f.tearDown)
        original=self.f.state;self.f.state=original/'selected';self.f.state.mkdir()
        for name in ('reflection_owner.json','goal.yaml','strategy.yaml','trades.jsonl'):
            (self.f.state/name).write_bytes((original/name).read_bytes())

    def prepare(self,wallet):
        from test_supervisor_sync import NOW
        manifest=selected_manifest({'profiles':[dict(arm=arm,wallet_eur=capital,directory=
            self.f.state.name if arm=='adaptive' and capital==wallet else f'{arm}_{capital}')
            for arm in ('adaptive','control') for capital in (50,100,150,200)]},wallet)
        (self.f.state.parent/'manifest.json').write_text(json.dumps(manifest));p=resolve_primary(manifest)
        from hermes_trading.paired_risk import initial_risk_state
        with closing(sqlite3.connect(self.f.state/'paper_publications.sqlite')) as db:
            db.execute('CREATE TABLE IF NOT EXISTS paired_identity(id INTEGER PRIMARY KEY,manifest_hash TEXT)')
            db.execute('INSERT OR REPLACE INTO paired_identity VALUES(1,?)',(_hash(manifest),))
            db.execute('CREATE TABLE IF NOT EXISTS paired_risk_state(id INTEGER PRIMARY KEY,state TEXT)')
            db.execute('INSERT OR REPLACE INTO paired_risk_state VALUES(1,?)',(json.dumps(initial_risk_state(p)),));db.commit()
        policy=json.loads((self.f.state/'reflection_owner.json').read_text())
        account=dict(schema_version=1,experiment_id=policy['experiment_id'],currency='EUR',starting_balance=str(wallet),activated_at=NOW.isoformat())
        (self.f.state/'wallet_account.json').write_text(json.dumps(account))
        report=dict(schema_version=1,status='observed',observed_at=NOW.isoformat(),experiment_id=policy['experiment_id'],
            observation=dict(account_config_hash=_hash(account),experiment_id=policy['experiment_id'],currency='EUR',starting_balance=str(wallet)))
        (self.f.state/'wallet_metrics.json').write_text(json.dumps(report))
        heartbeat=self.f.heartbeat();heartbeat.update(worker_release=capture_startup_release(self.f.state,now=NOW,primary=p),
            wallet_report_hash=_hash(report),wallet_account=dict(status='observed'))
        (self.f.state/'heartbeat.json').write_text(json.dumps(heartbeat))
        return NOW,p,policy,account

    def test_versioned_snapshot_pins_capital_account_rationale_and_cannot_downgrade(self):
        for wallet in (50,100,200):
            now,p,policy,account=self.prepare(wallet);snapshot=make_snapshot(self.f.state,now=now)
            self.assertEqual(snapshot['schema_version'],2)
            validate_snapshot(snapshot,policy,now=now,expected_primary=p,expected_account_hash=_hash(account))
            for primary,account_hash in ((PrimarySelection(wallet,'b'*64),_hash(account)),(p,'c'*64)):
                with self.assertRaises(ValueError):validate_snapshot(snapshot,policy,now=now,expected_primary=primary,expected_account_hash=account_hash)
            with self.assertRaises(ValueError):validate_snapshot(snapshot,policy,now=now)
            downgraded=dict(snapshot,schema_version=1)
            with self.assertRaises(ValueError):validate_snapshot(downgraded,policy,now=now)

    def test_same_owner_and_recomputed_report_hash_cannot_hide_a_different_wallet(self):
        now,p,policy,account=self.prepare(200);good=make_snapshot(self.f.state,now=now)
        for key,value in [('starting_balance','100'),('account_config_hash','d'*64),('currency','USD'),('experiment_id','other')]:
            bad=deepcopy(good);report=json.loads(base64.b64decode(bad['files']['wallet_metrics.json']))
            report['observation'][key]=value
            heartbeat=json.loads(base64.b64decode(bad['files']['heartbeat.json']));heartbeat['wallet_report_hash']=_hash(report)
            bad['files']['wallet_metrics.json']=base64.b64encode(json.dumps(report).encode()).decode()
            bad['files']['heartbeat.json']=base64.b64encode(json.dumps(heartbeat).encode()).decode()
            with self.subTest(key=key),self.assertRaises(ValueError):
                validate_snapshot(bad,policy,now=now,expected_primary=p,expected_account_hash=_hash(account))

    def test_watcher_initial_and_post_publication_snapshot_receive_selected_pins(self):
        import hashlib
        from scripts import hermes_watch
        now,p,policy,account=self.prepare(200);snapshot=make_snapshot(self.f.state,now=now)
        from hermes_trading.reflection_supervision import initialize_supervisor
        initialize_supervisor(self.f.state,now=now-timedelta(hours=1))
        config=dict(schema_version=2,primary_wallet_eur=200,primary_selection_sha256=p.selection_sha256,
                    account_hash=_hash(account),policy_hash=_hash(policy))
        limits=dict(primary_wallet_eur=200,primary_selection_sha256=p.selection_sha256,account_hash=_hash(account))
        (self.f.state/'supervisor_limits.json').write_text(json.dumps(limits))
        receipt=dict(status='published',candidate_sha256=hashlib.sha256((self.f.state/'strategy.yaml').read_bytes()).hexdigest())
        with patch.object(hermes_watch,'STATE_DIR',self.f.state),patch.object(hermes_watch,'PROJECT_DIR',self.f.state.parent), \
                patch.object(hermes_watch,'LAUNCH_CONFIG',config), \
                patch.object(hermes_watch,'_remote_snapshot',return_value=snapshot), \
                patch.object(hermes_watch,'_strict_runner',return_value=nullcontext(object())), \
                patch.object(hermes_watch,'_refresh_supervised_access'),patch.object(hermes_watch,'_sync_supervised_usage',return_value=None), \
                patch.object(hermes_watch,'_sync_to_mirror'), \
                patch('hermes_trading.supervisor_lifecycle.admit_cycle',return_value=None), \
                patch('hermes_trading.global_reflection.run_global_review',return_value={'status':'awaiting_publication'}), \
                patch('hermes_trading.paper_publication.publish_pending_review',return_value=receipt), \
                patch('hermes_trading.supervisor_sync.validate_snapshot',wraps=validate_snapshot) as validate:
            result=hermes_watch._run_supervised_watch(now=now)
            self.assertEqual(result.get('mirror_state'),'current',result)
        self.assertEqual(validate.call_count,2)
        self.assertTrue(all(call.kwargs['expected_primary']==p and call.kwargs['expected_account_hash']==_hash(account)
                            for call in validate.call_args_list))


class SelectedPublicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_selected_review_publishes_one_causal_global_epoch_to_all_adaptive_wallets(self):
        from dataclasses import replace
        import subprocess
        from unittest.mock import Mock
        from test_paired_primary_archive import fresh_selected
        from test_global_reflection import Store
        from hermes_trading.paired_paper import run_paired_cycle,process_paper_account
        from hermes_trading.paired_packet_storage import load_packet
        from hermes_trading.reflection_supervision import initialize_supervisor,ReviewLease
        from hermes_trading.global_reflection import run_global_review
        from hermes_trading.paper_publication import build_publication_request,publish_paper_request
        from hermes_trading.model_runner import ReflectionModelRunner
        from hermes_trading.providers import ProviderRouter,HermesCatalog,ModelCandidate
        for wallet in (50,200):
            f,p=fresh_selected(self,wallet);initialize_supervisor(f.primary,now=f.at)
            usage={'invented_immutable_usage_source':'selected primary'}
            (f.primary/'supervisor_usage_snapshot.json').write_text(json.dumps(usage))
            await run_paired_cycle(f.root,f.market,now=f.at)
            f.at+=timedelta(seconds=1)
            # Five invented unique closes exercise cadence; these are not market results.
            rows=[dict(asset='ETH/EUR',position_id=f'invented-{i}',closed_at=f.at.isoformat(),pnl=0) for i in range(5)]
            (f.primary/'trades.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
            command=Mock(return_value=subprocess.CompletedProcess([],0,json.dumps(dict(outcome='change',variable='entry.threshold',
                asset=None,value=31,reason='invented correctness decision')),''))
            runner=ReflectionModelRunner(ProviderRouter(Store(),HermesCatalog(())),command_run=command,strict_pinned=True,
                pinned_candidate=ModelCandidate('nous','fixed:free',True,'synthetic'))
            review=run_global_review(f.primary,runner,now=f.at)
            self.assertEqual(review['status'],'awaiting_publication',review)
            with ReviewLease(f.primary) as lease:request=build_publication_request(lease.pending_review(),lease.policy)
            publish_paper_request(f.primary,request,env={'HERMES_TRADING_MODE':'paper'},now=f.at)
            f.at+=timedelta(seconds=60)
            snapshot=replace(f.snapshot,candle=replace(f.snapshot.candle,open_timestamp=f.at-timedelta(hours=1),close_timestamp=f.at),
                             price=dict(f.snapshot.price,received_at=f.at.isoformat()))
            f.market.collect.return_value={snapshot.asset:snapshot}
            executed=[]
            async def process(*args,**kwargs):
                executed.append((args[0].name.split('_')[0],int(args[1]['version'])))
                return await process_paper_account(*args,**kwargs)
            await run_paired_cycle(f.root,f.market,now=f.at,process_account=process)
            with closing(sqlite3.connect(f.primary/'paper_publications.sqlite')) as db:
                packet=load_packet(db,db.execute('SELECT payload FROM paired_batches ORDER BY rowid DESC LIMIT 1').fetchone()[0])
            self.assertEqual(int(packet['strategies']['adaptive']['version']),2)
            self.assertEqual(int(packet['strategies']['control']['version']),1)
            self.assertEqual(packet['supervisor_usage']['data'],usage);self.assertEqual(command.call_count,1)
            self.assertEqual(len(executed),8)
            self.assertEqual([version for arm,version in executed if arm=='adaptive'],[2]*4)
            self.assertEqual([version for arm,version in executed if arm=='control'],[1]*4)
            for profile in f.manifest['profiles']:
                self.assertEqual(packet['costs'][profile['directory']]['data']['totals']['hosting']['amount'],'.01')

    def test_venue_profile_loader_validates_the_entire_selected_method(self):
        from hermes_trading.venue_candidate import _profiles
        profiles=[dict(arm=arm,wallet_eur=wallet,directory=f'{arm}_{wallet}')
                  for arm in ('adaptive','control') for wallet in (50,100,150,200)]
        for wallet in (50,200):
            manifest=selected_manifest(dict(profiles=profiles,assets=['BTC/EUR','ETH/EUR','XRP/EUR']),wallet)
            _profiles(manifest)
            manifest['reporting_protocol']['assessment_days']=90
            with self.assertRaisesRegex(ValueError,'reporting|protocol'):_profiles(manifest)

    async def test_admission_rejects_secondary_missing_journal_wrong_risk_and_reselection(self):
        from test_paired_primary_finance import SelectedPrimaryFinanceTests
        from hermes_trading.paired_inception import initialize_paired_inception
        from hermes_trading.paired_paper import run_paired_cycle
        from hermes_trading.paired_risk import initial_risk_state
        from hermes_trading.paper_publication import assert_no_pending_paired_batch
        for wallet in (50,200):
            f=SelectedPrimaryFinanceTests.fixture(self,wallet);p=resolve_primary(f.fixture.manifest);state=f.root/p.profile_key('adaptive')
            with self.assertRaisesRegex(ValueError,'primary|journal'):assert_no_pending_paired_batch(state)
            with self.assertRaisesRegex(ValueError,'primary'):assert_no_pending_paired_batch(f.root/'adaptive_100')
            initialize_paired_inception(f.root,clock=lambda:f.at)
            await run_paired_cycle(f.root,f.fixture.market,now=f.at+timedelta(seconds=1))
            assert_no_pending_paired_batch(state)
            with closing(sqlite3.connect(state/'paper_publications.sqlite')) as db:
                risk=db.execute('SELECT state FROM paired_risk_state').fetchone()[0]
                db.execute('UPDATE paired_risk_state SET state=?',(json.dumps(initial_risk_state()),));db.commit()
                with self.assertRaisesRegex(ValueError,'risk|primary'):assert_no_pending_paired_batch(state)
                db.execute('UPDATE paired_risk_state SET state=?',(risk,));db.commit()
            changed=selected_manifest(f.fixture.manifest,wallet,rationale='b'*64)
            (f.root/'manifest.json').write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError,'manifest|primary'):assert_no_pending_paired_batch(state)


class SelectedLaunchTests(unittest.TestCase):
    def setUp(self):
        from test_supervisor_launch import SupervisorLaunchTests
        self.f=SupervisorLaunchTests();self.f.setUp();self.addCleanup(self.f.tearDown)

    def prepare(self,wallet):
        f=self.f;p=resolve_primary(selected_manifest({},wallet));state=f.state.parent/p.profile_key('adaptive');state.mkdir(exist_ok=True)
        for name in ('reflection_owner.json','supervisor_limits.json'):(state/name).write_bytes((f.state/name).read_bytes())
        account=dict(f.account,starting_balance=str(wallet));(state/'wallet_account.json').write_text(json.dumps(account))
        limits=dict(f.limits,primary_wallet_eur=wallet,primary_selection_sha256=p.selection_sha256,account_hash=_hash(account))
        (state/'supervisor_limits.json').write_text(json.dumps(limits))
        f.config.update(schema_version=2,primary_wallet_eur=wallet,primary_selection_sha256=p.selection_sha256,
            account_hash=_hash(account),local_state_dir=str(state),remote_state_dir='/app/state/'+p.profile_key('adaptive'))
        f.write();return p,state,account,limits

    def test_versioned_launch_and_all_four_transports_select_the_same_wallet(self):
        import subprocess
        from scripts import hermes_watch
        for wallet in (50,200):
            p,state,account,limits=self.prepare(wallet)
            self.assertEqual(self.f.validate()['local_state_dir'],state)
            with patch.object(hermes_watch,'PROJECT_DIR',self.f.root),hermes_watch.supervised_launch(self.f.path), \
                    patch.object(hermes_watch,'_run',return_value=subprocess.CompletedProcess([],0,'{}','')) as run:
                hermes_watch._remote_snapshot();hermes_watch._publish_remote({});hermes_watch._install_remote_usage({});hermes_watch._install_remote_access({})
            self.assertEqual(run.call_count,4)
            self.assertTrue(all('--state-dir /app/state/'+p.profile_key('adaptive') in call.args[0][-1] for call in run.call_args_list))

    def test_limit_binding_and_local_account_change_cannot_reuse_a_launch(self):
        from scripts import hermes_watch
        p,state,account,limits=self.prepare(200);self.f.validate()
        (state/'supervisor_limits.json').write_text(json.dumps(dict(limits,primary_selection_sha256='b'*64)))
        with self.assertRaisesRegex(ValueError,'primary|selection'):self.f.validate()
        (state/'supervisor_limits.json').write_text(json.dumps(limits))
        with patch.object(hermes_watch,'PROJECT_DIR',self.f.root),hermes_watch.supervised_launch(self.f.path):
            (state/'wallet_account.json').write_text(json.dumps(dict(account,starting_balance='100')))
            with self.assertRaisesRegex(ValueError,'account|wallet|primary'):hermes_watch._assert_launched_owner(self.f.policy)


if __name__=='__main__':unittest.main()
