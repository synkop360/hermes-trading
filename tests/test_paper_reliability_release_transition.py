"""Deploy only the reviewed snapshot reliability correction from the exact installed map."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import test_paper_snapshot_release_transition as previous
from test_paper_release_transition import api, TARGET

BASE='ea752c5f3990acff2a6f7164be733fe2bf92df35'
PURPOSE='paper_supervisor_reliability_release_transition_v1'
DELTA={'supervisor_sync.py'}

def reliability_plan():
    path=Path(__file__).resolve().parents[1]/'research/runs/paper-reliability-v1/predecessor-release-plan.json'
    old=json.loads(path.read_text())['new'];new=deepcopy(old)
    for name in DELTA:new['runtime'][name]='f'*64
    return dict(old_commit=BASE,new_commit='d'*40,target=deepcopy(TARGET),old=old,new=new)

class ReliabilityReleaseTests(unittest.TestCase):
    def test_exact_predecessor_and_delta(self):
        plan=reliability_plan();api().validate_release_plan(plan)
        self.assertEqual(api()._purpose(plan),PURPOSE)

    def test_foreign_predecessor_extra_missing_inventory_or_launcher_reject(self):
        for case in ('old','extra','missing','inventory','launcher','base'):
            plan=reliability_plan()
            if case=='old':
                for side in ('old','new'):plan[side]['runtime']['positions.py']='0'*64
            elif case=='extra':plan['new']['runtime']['positions.py']='0'*64
            elif case=='missing':plan['new']['runtime']['supervisor_sync.py']=plan['old']['runtime']['supervisor_sync.py']
            elif case=='inventory':plan['new']['runtime']['extra.py']='0'*64
            elif case=='launcher':plan['new']['native_launcher']='0'*64
            else:plan['old_commit']=previous.BASE
            with self.subTest(case=case),self.assertRaises(ValueError):api().validate_release_plan(plan)

class ReliabilityNativeTransitionTests(unittest.IsolatedAsyncioTestCase):
    files = previous.cross_host.CrossHostTransitionTests.files
    rows = previous.cross_host.CrossHostTransitionTests.rows
    opening = previous.cross_host.CrossHostTransitionTests.opening
    async def test_closed_copy_preserves_halt_and_reconciles_checked_partial_write(self):
        with patch.object(previous,'snapshot_plan',reliability_plan),patch.object(previous,'BASE',BASE),patch.object(previous,'PURPOSE',PURPOSE):
            await previous.SnapshotClosedCopyTests.test_new_purpose_preserves_original_evidence_and_resumes_only_checked_partial_transition(self)

    async def test_both_hosts_require_scoped_receipts_and_native_validation(self):
        with patch.object(previous,'snapshot_plan',reliability_plan),patch.object(previous,'BASE',BASE),patch.object(previous,'PURPOSE',PURPOSE):
            with patch.object(previous.cross_host,'NativeTransitionFixture',previous.UnlatchedSnapshotTransitionFixture):
                await previous.cross_host.CrossHostTransitionTests.test_two_host_views_require_two_scoped_receipts_and_actual_native_checks(self)
