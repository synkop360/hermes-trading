"""Recorded original archive/cut resolution on invented native eight-account runs."""
from contextlib import closing
from datetime import timedelta
from pathlib import Path
import asyncio,hashlib,importlib.util,json,secrets,shutil,sqlite3,tempfile,unittest

from hermes_trading import archive_journal as journal,archive_store as store
from hermes_trading.archive_coordinator import capture
from hermes_trading.archive_snapshot import snapshot
from hermes_trading.control_auth import sign_command
from hermes_trading.paper_publication import _release_hashes
from hermes_trading.reflection_supervision import _hash,_json
import test_paired_primary_archive as native


class RecordedHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.available=importlib.util.find_spec('hermes_trading.archive_shared_resolver') is not None
        if not cls.available:return
        cls.fixtures=unittest.TestCase();cls.addClassCleanup(cls.fixtures.doCleanups)
        cls.cases={}
        async def build(wallet):
            f,p=native.fresh_selected(cls.fixtures,wallet,fees=True,alias=wallet==50)
            async def pause(seconds):
                await f.sleep(seconds)
                if f.market.collect.await_count==2:raise RuntimeError('invented completed-cut pause')
            with cls.fixtures.assertRaisesRegex(RuntimeError,'completed-cut pause'):await f.invoke(sleep=pause)
            pc=native.controller_for(cls.fixtures,f,p)
            tmp=tempfile.TemporaryDirectory();cls.fixtures.addCleanup(tmp.cleanup);root=Path(tmp.name)
            archive=root/'archive';parent=None
            for index,through in enumerate((1,2)):
                if index:await f.invoke()
                cohort=root/f'cohort-{index}'
                result=capture(pc,cohort,enabled=True,worker_capture=lambda target,**limits:snapshot(f.root,target,enabled=True,**limits),
                    experiment_id='paired-test',expected_release=_release_hashes(),through_sequence=through,
                    max_bytes=32*1024**2,timeout_seconds=90,now=f.at)
                names=[row['path'] for row in json.loads((cohort/'snapshot_inventory.json').read_text())['files']]+['snapshot_inventory.json']
                digest=store.seal(cohort,archive,names,experiment='paired-test',first_batch=1 if index==0 else 3,
                                  last_batch=result['certificate']['observed_complete'],parent=parent)
                cert=result['certificate'];path=f.primary/'paper_publications.sqlite'
                request=journal.prepare(path,enabled=True,experiment_id='paired-test',release_sha256=cert['release_sha256'],
                    archive_sha256=digest,account_snapshots=cert['account_snapshots'],copies={'pc':'e'*64,'backup':'f'*64},through_sequence=through)
                keys={name:secrets.token_hex(32) for name in ('pc','backup')};body=_json(request)
                receipts={name:dict(body=body,nonce=request['operation_id'],timestamp=f.at.isoformat(),
                    signature=sign_command(key,request['operation_id'],f.at,body)) for name,key in keys.items()}
                journal.acknowledge(path,request['operation_id'],receipts,keys,now=f.at,enabled=True)
                journal.retire(path,request['operation_id'],enabled=True);parent=digest
            cls.cases[wallet]=dict(worker=f.root,archive=archive,manifest=f.manifest,relative=p.profile_directory(f.manifest,'adaptive')+'/paper_publications.sqlite',
                                   now=f.at,first=json.loads((cohort/'cohort_capture.json').read_text()),head=parent)
        asyncio.run(build(50));asyncio.run(build(200))

    def setUp(self):
        self.assertTrue(self.available,'recorded original archive resolver is not implemented')
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)

    def copied(self,wallet=200):
        case=self.cases[wallet];worker=self.root/'worker';archive=self.root/'archive'
        shutil.copytree(case['worker'],worker);shutil.copytree(case['archive'],archive)
        current=worker/case['relative']
        options=dict(enabled=True,format='recorded_archive_store_v1',current=dict(path=current,sha256=hashlib.sha256(current.read_bytes()).hexdigest()),
            archive_root=archive,shared_relative_path=case['relative'],expected_cohort_sha256=_hash(case['manifest']),
            expected_experiment_id='paired-test',cache_root=self.root,max_bytes=64*1024**2,timeout_seconds=120,now=case['now'])
        return case,worker,options

    def reader(self,options,**overrides):
        from hermes_trading.archive_shared_resolver import recorded_shared_history
        return recorded_shared_history(**dict(options,**overrides))

    def inventory(self,root):
        return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}

    def change_request(self,options,change):
        path=options['current']['path']
        with closing(sqlite3.connect(path)) as db:
            row=db.execute("SELECT operation_id,request FROM paired_archive_operations ORDER BY rowid DESC LIMIT 1").fetchone()
            request=json.loads(row[1]);change(request)
            db.execute('UPDATE paired_archive_operations SET request=?,request_sha256=? WHERE operation_id=?',
                       (_json(request),_hash(request),row[0]));db.commit()
        options['current']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()

    def reseal(self,case,options,mutate):
        archive=options['archive_root'];raw=store.validate(archive,case['head'])[-1]
        target=self.root/'forged-cohort';store.restore(archive,case['head'],target);mutate(target)
        def refresh(root):
            path=root/'snapshot_inventory.json';value=json.loads(path.read_text())
            for row in value['files']:
                file=root/row['path'];row.update(bytes=file.stat().st_size,sha256=hashlib.sha256(file.read_bytes()).hexdigest())
            path.write_bytes((_json(value)+'\n').encode());return hashlib.sha256(path.read_bytes()).hexdigest()
        metadata_path=target/'cohort_capture.json';metadata=json.loads(metadata_path.read_text())
        metadata['worker_inventory_sha256']=refresh(target/'worker')
        metadata['supervisor_inventory_sha256']=refresh(target/'supervisor')
        metadata_path.write_bytes((_json(metadata)+'\n').encode());refresh(target)
        inventory=json.loads((target/'snapshot_inventory.json').read_text())
        names=[row['path'] for row in inventory['files']]+['snapshot_inventory.json']
        digest=store.seal(target,archive,names,experiment='paired-test',first_batch=raw['first_batch'],
                          last_batch=raw['last_batch'],parent=raw['parent'])
        self.change_request(options,lambda request:request.update(archive_sha256=digest))

    def test_two_recorded_retirements_resolve_originals_for_selected50_and200(self):
        for wallet in (50,200):
            with self.subTest(wallet=wallet),tempfile.TemporaryDirectory() as folder:
                self.root=Path(folder);case,worker,options=self.copied(wallet)
                before=self.inventory(self.root)
                with self.reader(options) as view:
                    self.assertEqual(view.complete,3);self.assertEqual(view.retired_complete,2)
                    self.assertEqual([r[0] for r in view.history.execute('SELECT rowid FROM paired_batches ORDER BY rowid')],[1,2,3])
                    self.assertEqual(view.current.execute('SELECT COUNT(*) FROM paired_batches').fetchone()[0],1)
                    for db in (view.current,view.history):
                        with self.assertRaises(sqlite3.OperationalError):db.execute('DELETE FROM paired_batches')
                self.assertEqual(before,self.inventory(self.root))

    def test_disabled_unknown_format_bounds_deadline_pin_and_wal_fail(self):
        _,_,options=self.copied()
        for change in ({'enabled':False},{'format':'unknown'},{'max_bytes':4096},{'timeout_seconds':10**1000},
                       {'timeout_seconds':float('nan')},{'expected_experiment_id':'unrelated'},
                       {'expected_cohort_sha256':'0'*64},{'shared_relative_path':'adaptive_50/paper_publications.sqlite'}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                with self.reader(options,**change):pass
        ticks=iter((0,0,121,121))
        with self.assertRaises(TimeoutError):
            with self.reader(options,clock=lambda:next(ticks,121)):pass
        with self.assertRaises(ValueError):
            with self.reader(options,current=dict(options['current'],sha256='0'*64)):pass
        wal=options['current']['path'].with_name('paper_publications.sqlite-wal');wal.write_bytes(b'invented unsealed WAL')
        with self.assertRaisesRegex(ValueError,'snapshot|sidecar'):
            with self.reader(options):pass

    def test_missing_parent_object_or_manifest_is_not_replaced_with_summaries(self):
        for missing in ('parent','object','manifest'):
            with self.subTest(missing=missing),tempfile.TemporaryDirectory() as folder:
                self.root=Path(folder);case,_,options=self.copied()
                manifest=options['archive_root']/'manifests'/(case['head']+'.json')
                value=json.loads(manifest.read_text())
                path=(options['archive_root']/'manifests'/(value['parent']+'.json') if missing=='parent' else
                      options['archive_root']/'objects'/value['files'][0]['objects'][0] if missing=='object' else manifest)
                path.unlink()
                with self.assertRaises((OSError,ValueError)):
                    with self.reader(options):pass

    def test_changed_current_during_use_rejects_the_report_only_view(self):
        _,_,options=self.copied()
        with self.assertRaisesRegex(ValueError,'digest|changed'):
            with self.reader(options):
                with closing(sqlite3.connect(options['current']['path'])) as db:
                    db.execute('UPDATE paired_identity SET manifest_hash=?',('0'*64,));db.commit()

    def test_rehashed_current_request_cannot_substitute_other_account_or_release_evidence(self):
        for field in ('account_snapshots','release_sha256','next_input_hash'):
            with self.subTest(field=field),tempfile.TemporaryDirectory() as folder:
                self.root=Path(folder);_,_,options=self.copied()
                def change(request):
                    if field=='account_snapshots':request[field]['control_50']='0'*64
                    else:request[field]='0'*64
                self.change_request(options,change)
                with self.assertRaisesRegex(ValueError,'certificate|'+field):
                    with self.reader(options):pass

    def test_resealed_cohort_with_false_certificate_is_not_trusted_by_its_new_hash(self):
        for field in ('risk_state_sha256','retirement_authorized'):
            with self.subTest(field=field),tempfile.TemporaryDirectory() as folder:
                self.root=Path(folder);case,_,options=self.copied()
                def mutate(target):
                    path=target/'cohort_capture.json';metadata=json.loads(path.read_text())
                    metadata['certificate'][field]=True if field=='retirement_authorized' else '0'*64
                    path.write_bytes((_json(metadata)+'\n').encode())
                self.reseal(case,options,mutate)
                with self.assertRaisesRegex(ValueError,'cut certificate'):
                    with self.reader(options):pass

    def test_runtime_limits_and_account_sources_stay_unchanged_on_budget_failure(self):
        _,worker,options=self.copied();before=self.inventory(self.root)
        # Current alone fits; full original cohort materialization must not.
        limit=options['current']['path'].stat().st_size+4096
        with self.assertRaises(ValueError):
            with self.reader(options,max_bytes=limit):pass
        self.assertEqual(before,self.inventory(self.root))

    def test_native_report_uses_recorded_resolver_and_unknown_archive_is_incomplete(self):
        from hermes_trading.paired_performance_report import paired_performance_report
        from unittest.mock import patch
        case,worker,options=self.copied()
        config={k:v for k,v in options.items() if k in {'enabled','format','current','archive_root','cache_root','max_bytes','timeout_seconds'}}
        reached=[]
        from hermes_trading.archive_shared_resolver import recorded_shared_history
        def observed(**kwargs):reached.append(kwargs);return recorded_shared_history(**kwargs)
        with patch('hermes_trading.archive_shared_resolver.recorded_shared_history',side_effect=observed):
            report=paired_performance_report(worker,now=case['now']+timedelta(days=1),history=config)
        self.assertEqual(len(reached),1,'native report did not use recorded original archive lookup')
        self.assertFalse(report['profitability_qualified']);self.assertFalse(report['eligible_for_real_trading'])
        self.assertIn('calendar',report,'resolver failed before the native calendar read')
        config['archive_root']=self.root/'unavailable'
        rejected=paired_performance_report(worker,now=case['now']+timedelta(days=1),history=config)
        self.assertEqual(rejected['data_status'],'incomplete')

    def test_deep_archived_review_json_returns_incomplete_without_crashing(self):
        from hermes_trading.paired_performance_report import paired_performance_report
        case,worker,options=self.copied()
        def mutate(target):
            with closing(sqlite3.connect(target/'supervisor/llm_review_state.sqlite')) as db:
                db.execute('INSERT INTO reviews VALUES(?,?)',('invented-deep-record','['*2000+'0'+']'*2000));db.commit()
        self.reseal(case,options,mutate)
        config={k:v for k,v in options.items() if k in {'enabled','format','current','archive_root','cache_root','max_bytes','timeout_seconds'}}
        try:report=paired_performance_report(worker,now=case['now']+timedelta(days=1),history=config)
        except RecursionError:self.fail('deep but bounded archived review JSON escaped report validation')
        self.assertEqual(report['data_status'],'incomplete')
        self.assertEqual(report['source_error_class'],'RecursionError')


if __name__=='__main__':unittest.main()
