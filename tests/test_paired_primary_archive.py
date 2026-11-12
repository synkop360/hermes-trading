"""Selected wallet archive composition on fresh invented eight-account cohorts."""
from contextlib import closing
from datetime import timedelta
from pathlib import Path
import hashlib,json,secrets,sqlite3,tempfile,unittest

from hermes_trading.paired_primary import resolve_primary
from hermes_trading.reflection_supervision import _hash,initialize_supervisor
from test_paired_primary_finance import selected_manifest


def fresh_selected(test,wallet,*,alias=False,fees=False):
    from test_paired_inception import PairedInceptionTests
    from hermes_trading.paired_inception import initialize_paired_inception
    opening=PairedInceptionTests();opening.setUp();test.addCleanup(opening.tearDown)
    f=opening.fixture;manifest=selected_manifest(f.manifest,wallet)
    if fees:
        import yaml
        for profile in manifest['profiles']:
            path=f.root/profile['directory']/'strategy.yaml';rules=yaml.safe_load(path.read_bytes())
            rules['simulation'].update(taker_fee_rate='.001',maker_fee_rate='.001',quantity_decimals=8)
            raw=yaml.safe_dump(rules).encode();path.write_bytes(raw);profile['strategy_sha256']=hashlib.sha256(raw).hexdigest()
    if alias:
        row=next(row for row in manifest['profiles'] if row['arm']=='adaptive' and row['wallet_eur']==wallet)
        original=f.root/row['directory'];row['directory']='selected_adaptive';original.rename(f.root/row['directory'])
    primary=resolve_primary(manifest);state=f.root/primary.profile_directory(manifest,'adaptive')
    for name in ('reflection_owner.json','model_access.json'):(state/name).write_bytes((f.primary/name).read_bytes())
    f.primary=state;f.manifest=manifest;(f.root/'manifest.json').write_text(json.dumps(manifest))
    for profile in manifest['profiles']:
        folder=f.root/profile['directory'];(folder/'history').mkdir(exist_ok=True)
        (folder/'history/v0001.yaml').write_bytes((folder/'strategy.yaml').read_bytes())
    f.limits['max_cycles']=3;(f.root/'worker_limits.json').write_text(json.dumps(f.limits))
    initialize_paired_inception(f.root,clock=lambda:opening.at);f.at=opening.at+timedelta(seconds=1)
    return f,primary


def controller_for(test,f,primary):
    from scripts.hermes_watch import _supervisor_release
    tmp=tempfile.TemporaryDirectory();test.addCleanup(tmp.cleanup);pc=Path(tmp.name)/'supervisor';pc.mkdir()
    for name in ('reflection_owner.json','wallet_account.json','strategy.yaml','goal.yaml'):
        (pc/name).write_bytes((f.primary/name).read_bytes())
    (pc/'trades.jsonl').write_text('');initialize_supervisor(pc,now=f.at)
    account=json.loads((pc/'wallet_account.json').read_text())
    limits=dict(schema_version=1,experiment_id=f.policy['experiment_id'],policy_hash=_hash(f.policy),
        release_hash=_hash(_supervisor_release()),activated_at=account['activated_at'],max_cycles=100,
        max_runtime_seconds=86400,interval_seconds=60,max_model_attempts=1,
        primary_wallet_eur=primary.wallet_eur,primary_selection_sha256=primary.selection_sha256,account_hash=_hash(account))
    (pc/'supervisor_limits.json').write_text(json.dumps(limits))
    with closing(sqlite3.connect(pc/'watcher_cycle.sqlite')) as db:db.execute('CREATE TABLE cycle(id)');db.commit()
    return pc


class SelectedArchiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_selected_protective_cut_preserves_offline_claim_without_authority(self):
        from contextlib import ExitStack
        from dataclasses import replace
        from hermes_trading import archive_witness as original,archive_protective_witness as witness
        from hermes_trading.archive_capacity import StorageDomain
        from hermes_trading.archive_nonce import BoundedArchiveNonceStore
        from hermes_trading.archive_protective_receiver import ProtectiveReceiver
        from hermes_trading.archive_protective_transport import ProtectiveClient,ProtectiveServer
        from hermes_trading.archive_protective_export import export_pair,restore_pair
        from hermes_trading.archive_coordinator import capture
        from hermes_trading.archive_snapshot import snapshot
        from hermes_trading.archive_store import seal,restore
        from hermes_trading.archive_recovery_cut import inspect_cut,verify_cut
        from hermes_trading.paper_publication import _release_hashes
        for wallet in (50,200):
            f,p=fresh_selected(self,wallet,fees=True);f.limits['max_cycles']=2
            (f.root/'worker_limits.json').write_text(json.dumps(f.limits));identity=original.identity_for(f.root)
            tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);root=Path(tmp.name)
            stack=self.enterContext(ExitStack());domain=StorageDomain(root,domain_id='selected-cut',limit_bytes=128*1024**2,
                reserve_bytes=1024**2,enabled=True);domain.initialize();owner=stack.enter_context(domain.ownership())
            path=root/'receiver.sqlite';binding=witness.initialize(path,identity,source_root=f.root,enabled=True,now=f.at)
            generation=secrets.token_hex(16);options=dict(owner=owner,expected_store_id=generation,binding=binding,
                identity=identity,enabled=True,max_rows=16,max_pages=64)
            with owner.admit(4*1024**2) as lease:BoundedArchiveNonceStore.initialize(root/'nonce.sqlite',lease=lease,**options)
            nonce=BoundedArchiveNonceStore(root/'nonce.sqlite',**options)
            receiver=ProtectiveReceiver(path,binding,identity,enabled=True,max_bytes=1024**2,clock=domain.clock)
            stack.callback(receiver.close)
            with owner.admit(1024**2):receiver.prepare()
            key=secrets.token_hex(32);server=ProtectiveServer(path,binding,identity,key,nonce,enabled=True,
                max_db_bytes=1024**2,clock=domain.clock,receiver=receiver);offline=[False]
            def exchange(envelope,*,timeout_seconds,max_response_bytes):
                if offline[0]:raise OSError('invented selected receiver outage')
                return server.handle(envelope,now=f.at,timeout_seconds=timeout_seconds)
            client=ProtectiveClient(binding,identity,key,exchange,enabled=True,now=f.clock)
            f.limits['archive_witness']=dict(schema_version=2,binding=binding,identity_sha256=_hash(identity))
            (f.root/'worker_limits.json').write_text(json.dumps(f.limits))
            async def outage(seconds):
                f.at+=timedelta(seconds=seconds);offline[0]=True
                quote=replace(f.snapshot,price=dict(f.snapshot.price,bid='77',ask='78',received_at=f.at.isoformat()))
                f.market.collect.return_value={quote.asset:quote}
            state=await f.invoke(witness=client,sleep=outage)
            self.assertEqual(state['completed_batches'],2);head=receiver.read();self.assertEqual(head['state']['point']['complete'],1)
            artifact=root/'pair.zip';receipt=export_pair(receiver,nonce,artifact,enabled=True,
                expected_head_sha256=head['head_sha256'],max_bytes=2*1024**2)
            pair=root/'restored-pair';restore_pair(artifact,pair,owner=owner,enabled=True,
                expected_archive_sha256=receipt['archive_sha256'],expected_certificate_sha256=receipt['certificate_sha256'],
                binding=binding,identity=identity,expected_nonce_store_id=generation,expected_head_sha256=head['head_sha256'],
                max_bytes=2*1024**2,max_nonce_rows=16,max_nonce_pages=64)
            pc=controller_for(self,f,p);cohort=root/'cohort';release=_release_hashes()
            result=capture(pc,cohort,enabled=True,worker_capture=lambda target,**limits:snapshot(f.root,target,enabled=True,**limits),
                experiment_id=f.policy['experiment_id'],expected_release=release,through_sequence=1,
                max_bytes=16*1024**2,timeout_seconds=90,now=f.at)
            names=[row['path'] for row in json.loads((cohort/'snapshot_inventory.json').read_text())['files']]+['snapshot_inventory.json']
            digest=seal(cohort,root/'objects',names,experiment=f.policy['experiment_id'],first_batch=1,last_batch=2)
            restored=root/'restored-cohort';restore(root/'objects',digest,restored)
            before={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for directory in (restored,pair)
                    for path in directory.rglob('*') if path.is_file()}
            args=dict(owner=owner,enabled=True,expected_cohort_sha256=result['manifest_sha256'],
                expected_archive_sha256=receipt['archive_sha256'],expected_certificate_sha256=receipt['certificate_sha256'],
                binding=binding,identity=identity,expected_nonce_store_id=generation,expected_head_sha256=head['head_sha256'],
                expected_release=release,through_sequence=1,max_bytes=32*1024**2,timeout_seconds=120,now=f.at,
                max_nonce_rows=16,max_nonce_pages=64)
            cut=inspect_cut(restored,pair,artifact,**args)
            self.assertEqual(verify_cut(restored,pair,artifact,expected_cut_sha256=cut['cut_sha256'],**args),cut)
            self.assertEqual(cut['cut']['source_point']['complete'],2);self.assertEqual(cut['cut']['receiver_point']['complete'],1)
            self.assertEqual(cut['cut']['original_claim']['sequence'],2);self.assertEqual(len(cut['cut']['account_snapshots']),8)
            for field in ('independent_backup_verified','post_cut_activity_reconciled','runtime_resumption_authorized','retirement_authorized'):
                self.assertIs(cut[field],False)
            self.assertEqual(f.market.collect.await_count,2)
            self.assertEqual(before,{str(path):hashlib.sha256(path.read_bytes()).hexdigest() for directory in (restored,pair)
                                    for path in directory.rglob('*') if path.is_file()})

    async def test_certificate_rejects_wrong_risk_opening_and_same_policy_controller(self):
        from hermes_trading.archive_certificate import certify
        from hermes_trading.archive_snapshot import snapshot
        from hermes_trading.paper_publication import _release_hashes
        from hermes_trading.paired_risk import initial_risk_state
        f,p=fresh_selected(self,200);pc=controller_for(self,f,p);await f.invoke()
        journal=f.primary/'paper_publications.sqlite';account_raw=(pc/'wallet_account.json').read_bytes()
        limits_raw=(pc/'supervisor_limits.json').read_bytes()
        with closing(sqlite3.connect(journal)) as db:
            risk=db.execute('SELECT state FROM paired_risk_state WHERE id=1').fetchone()[0]
            opening=db.execute('SELECT payload,receipt_hash FROM paired_inception WHERE id=1').fetchone()
        def check_case(label):
            worker=pc.parent/('worker-'+label);controller=pc.parent/('controller-'+label)
            args=dict(enabled=True,max_bytes=64*1024**2,timeout_seconds=30)
            wr=snapshot(f.root,worker,**args);sr=snapshot(pc,controller,role='supervisor',**args)
            return certify(worker,expected_inventory_sha256=wr['manifest_sha256'],
                supervisor_root=controller,expected_supervisor_inventory_sha256=sr['manifest_sha256'],
                experiment_id=f.policy['experiment_id'],expected_release=_release_hashes(),through_sequence=1,now=f.at)
        check_case('valid')
        with closing(sqlite3.connect(journal)) as db:
            for label,replacement in [('legacy-risk',initial_risk_state()),
                    ('other-rationale',initial_risk_state(type(p)(200,'b'*64)))]:
                db.execute('UPDATE paired_risk_state SET state=?',(json.dumps(replacement),));db.commit()
                with self.subTest(label=label),self.assertRaisesRegex(ValueError,'risk|primary'):check_case(label)
            db.execute('UPDATE paired_risk_state SET state=?',(risk,));db.commit()
            db.execute('DELETE FROM paired_inception');db.commit()
            with self.subTest(label='missing-opening'),self.assertRaisesRegex(ValueError,'opening|inception'):check_case('no-opening')
            db.execute('INSERT INTO paired_inception VALUES(1,?,?)',opening);db.commit()
        account=json.loads(account_raw);account['starting_balance']='100';(pc/'wallet_account.json').write_text(json.dumps(account))
        with self.subTest(label='same-policy-other-account'),self.assertRaisesRegex(ValueError,'primary|account'):check_case('other-account')
        (pc/'wallet_account.json').write_bytes(account_raw)
        limits=json.loads(limits_raw);limits['primary_selection_sha256']='b'*64;(pc/'supervisor_limits.json').write_text(json.dumps(limits))
        with self.subTest(label='other-controller-selection'),self.assertRaisesRegex(ValueError,'primary|selection'):check_case('other-selection')
        (pc/'supervisor_limits.json').write_bytes(limits_raw)
        (pc/'supervisor_lifecycle.json').write_text(json.dumps({'binding':'c'*64,'deadline':f.at.isoformat()}))
        with self.subTest(label='other-lifecycle'),self.assertRaisesRegex(ValueError,'lifecycle'):check_case('other-lifecycle')

    async def test_actual_worker_and_separate_controller_capture_selected_cohort(self):
        from hermes_trading.archive_coordinator import capture
        from hermes_trading.archive_snapshot import ownership
        from hermes_trading.paper_publication import _release_hashes
        for wallet in (50,200):
            f,p=fresh_selected(self,wallet,alias=wallet==50);pc=controller_for(self,f,p)
            f.limits['archive_exports']=dict(schema_version=1,enabled=True,max_bytes=32*1024**2,timeout_seconds=30)
            (f.root/'worker_limits.json').write_text(json.dumps(f.limits));captured=[];errors=[]
            target=pc.parent/'cohort'
            async def boundary(owner):
                if owner.complete!=2:return
                def worker_capture(stage,**limits):
                    for root,role in ((f.root,'worker'),(pc,'supervisor')):
                        with self.assertRaisesRegex(sqlite3.OperationalError,'locked'):
                            with ownership(root,role=role,enabled=True):pass
                    return owner.capture(stage,expected_complete=2)
                try:
                    captured.append(capture(pc,target,enabled=True,worker_capture=worker_capture,
                        experiment_id=f.policy['experiment_id'],expected_release=_release_hashes(),through_sequence=1,
                        max_bytes=64*1024**2,timeout_seconds=owner.remaining(),now=f.at))
                except Exception as error:errors.append(error);raise
            result=await f.invoke(archive_boundary=boundary)
            if errors:raise errors[0]
            self.assertEqual(len(captured),1,result)
            certificate=captured[0]['certificate'];self.assertEqual(certificate['observed_complete'],2)
            self.assertEqual(certificate['supervisor_scope'],'separate_snapshot')
            self.assertEqual(set(certificate['account_snapshots']),{f'{a}_{w}' for a in ('adaptive','control') for w in (50,100,150,200)})
            self.assertFalse(captured[0]['retirement_authorized']);self.assertEqual(result['completed_batches'],3)
            self.assertEqual(f.market.collect.await_count,3)

    async def test_native_pending_packet_keeps_original_selected_witness_reservation(self):
        from hermes_trading import archive_witness as witness
        from hermes_trading.paired_paper import run_paired_cycle,process_paper_account
        for wallet in (50,200):
            f,p=fresh_selected(self,wallet);identity=witness.identity_for(f.root)
            self.assertEqual(len(identity),7)
            tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);path=Path(tmp.name)/'receiver.sqlite'
            binding=witness.initialize(path,identity,source_root=f.root,enabled=True,now=f.at)
            def act(action='check'):
                head=witness.read(path,binding,identity,enabled=True)
                view=witness.source_view(f.root,identity,prior_complete=head['state']['point']['complete'],enabled=True)
                return witness.apply(path,binding,identity,view,action=action,expected_head_sha256=head['head_sha256'],enabled=True,now=f.at)
            token=act('reserve')['state']['reservation']['reservation_id']
            async def crash(*args,**kwargs):
                await process_paper_account(*args,**kwargs);raise RuntimeError('after original selected account')
            with self.assertRaisesRegex(RuntimeError,'original selected'):
                await run_paired_cycle(f.root,f.market,now=f.at,reservation_id=token,process_account=crash)
            self.assertEqual(act('reserve')['state']['reservation']['reservation_id'],token)
            await run_paired_cycle(f.root,f.market,now=f.at,reservation_id=token)
            self.assertEqual(act()['state']['point']['complete'],1);self.assertEqual(f.market.collect.await_count,1)


if __name__=='__main__':unittest.main()
