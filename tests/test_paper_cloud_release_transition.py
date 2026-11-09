"""Cloud supervision accepts only its exact predecessor and frozen successor."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
import sqlite3
from contextlib import closing
from unittest.mock import patch
from scripts import paper_release_transition as transition
import release_transition_fixtures as fixtures


class CloudReleaseTransitionTests(unittest.TestCase):
    def plan(self):
        return json.loads((Path(__file__).resolve().parents[1]/'research/runs/paper-cloud-supervision-v1/source-map.json').read_text())

    def test_exact_cloud_source_map_is_admitted(self):
        plan=self.plan();transition.validate_release_plan(plan)
        self.assertEqual(transition._purpose(plan),'paper_cloud_supervision_release_transition_v1')
        from scripts.hermes_watch import _supervisor_release
        self.assertEqual(_supervisor_release(),plan['new'])

    def test_runtime_launchers_predecessor_and_inventory_are_pinned(self):
        for case in ['base','old','runtime','watcher','launcher','inventory','missing']:
            plan=self.plan()
            if case=='base':plan['old_commit']='a'*40
            elif case=='old':plan['old']['runtime']['proposal_transport.py']='0'*64
            elif case=='runtime':plan['new']['runtime']['model_access_refresh.py']='0'*64
            elif case=='watcher':plan['new']['watcher']='0'*64
            elif case=='launcher':plan['new']['native_launcher']='0'*64
            elif case=='inventory':plan['new']['runtime']['unreviewed.py']='0'*64
            else:plan['new']['runtime']['proposal_transport.py']=plan['old']['runtime']['proposal_transport.py']
            with self.subTest(case=case),self.assertRaises(ValueError):transition.validate_release_plan(plan)


class CloudClosedCopyTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconciled_transition_preserves_actual_native_history_and_counters(self):
        source=CloudReleaseTransitionTests().plan()
        with patch.object(fixtures,'OLD_COMMIT',source['old_commit']), \
                patch.object(fixtures.NativeTransitionFixture,'_releases',return_value=(source['old'],source['new'])):
            f=await fixtures.NativeTransitionFixture.create(self)
        def rows(path,table):
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)) as db:
                return db.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall()
        before=rows(f.supervisor_root/'llm_review_state.sqlite','reviews')
        worker=rows(f.worker_root/'worker_runtime.sqlite','lifecycle')
        life=json.loads((f.supervisor_root/'supervisor_lifecycle.json').read_text())
        accounts={p['directory']:(f.worker_root/p['directory']/'wallet_account.json').read_bytes() for p in f.manifest['profiles']}
        options=dict(worker_root=f.worker_root,supervisor_root=f.supervisor_root,launch_file=f.launch_file,audit_dir=f.audit_dir)
        plan=transition.prepare_plan(f.worker_root,f.supervisor_root,f.launch_file,
            release_plan=f.release_plan,expected_identity=f.expected_identity)
        def interrupt(stage,name):
            if stage=='component_commit':raise RuntimeError('synthetic interruption')
        with self.assertRaisesRegex(RuntimeError,'synthetic interruption'):
            transition.apply_transition(plan,**options,apply=True,approval_ref='synthetic-cloud-owner',fault=interrupt)
        result=transition.apply_transition(plan,**options,apply=True,reconcile=True,approval_ref='synthetic-cloud-owner')
        self.assertEqual(result['status'],'completed')
        self.assertEqual(rows(f.supervisor_root/'llm_review_state.sqlite','reviews'),before)
        self.assertEqual(rows(f.worker_root/'worker_runtime.sqlite','lifecycle'),worker)
        after=json.loads((f.supervisor_root/'supervisor_lifecycle.json').read_text())
        self.assertEqual({k:v for k,v in life.items() if k!='binding'},{k:v for k,v in after.items() if k!='binding'})
        for name,raw in accounts.items():self.assertEqual((f.worker_root/name/'wallet_account.json').read_bytes(),raw)
        inspection=transition.inspect_transition(plan,**options)
        self.assertEqual(inspection['status'],'inspected')
        self.assertTrue(all(component['state']=='after' for component in inspection['components']))


if __name__=='__main__':unittest.main()
