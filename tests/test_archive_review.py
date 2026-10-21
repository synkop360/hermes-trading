from contextlib import closing
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json,sqlite3,tempfile,unittest
from pathlib import Path

from hermes_trading.archive_review import merge_closed_rows,reconcile_reviews,reconcile_publications
from hermes_trading.reflection_supervision import initialize_supervisor,ReviewLease,_json


class ReviewHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.at=datetime(2026,10,16,tzinfo=timezone.utc)
        self.policy={'schema_version':1,'experiment_id':'review-test','owner':'external_llm',
            'cadence_closed_trades':5,'one_variable_only':True,'model':{'provider':'nous','name':'fixed:free'}}
        (self.root/'reflection_owner.json').write_text(json.dumps(self.policy))
        initialize_supervisor(self.root,now=self.at)
        self.rows=[{'asset':'ETH/EUR','position_id':str(i),'status':'closed','quantity':'1',
            'closed_at':(self.at+timedelta(seconds=i+1)).isoformat()} for i in range(35)]
        self.now=self.at+timedelta(minutes=1)

    def state(self):
        with closing(sqlite3.connect(self.root/'llm_review_state.sqlite')) as db:
            state=json.loads(db.execute('SELECT state FROM supervisor').fetchone()[0])
            reviews=[json.loads(row[0]) for row in db.execute('SELECT record FROM reviews')]
        return state,reviews

    def check(self,rows=None,state=None,reviews=None):
        original,records=self.state()
        return reconcile_reviews(original if state is None else state,records if reviews is None else reviews,
            self.rows if rows is None else rows,policy=self.policy,now=self.now)

    def prepare(self,rows):
        with ReviewLease(self.root) as lease:return lease.prepare(rows,now=self.now)

    def test_union_retains_more_than_prompt_window_and_rejects_conflicts(self):
        rows=merge_closed_rows(self.rows[:30],self.rows[25:])
        self.assertEqual(len(rows),35)
        with self.assertRaisesRegex(ValueError,'conflict'):
            merge_closed_rows(rows,[dict(rows[0],evidence_classification='operational_recovery')])

    def test_all_unreviewed_ids_survive_beyond_latest25(self):
        result=self.check()
        self.assertEqual(len(result['unreviewed_trade_ids']),35)
        self.assertEqual(len(result['recent_trades']),25)
        self.assertEqual(result['recent_trades'],self.rows[-25:])

    def test_native_pending_review_and_watermark_are_preserved(self):
        pending=self.prepare(self.rows[:5]);result=self.check()
        self.assertEqual(result['pending_review_id'],pending['review_id'])
        self.assertEqual(result['consumed_trade_ids'],[])
        self.assertEqual(len(result['unreviewed_trade_ids']),35)

    def test_completed_no_change_consumes_exact_original_ids_once(self):
        pending=self.prepare(self.rows[:5])
        with ReviewLease(self.root) as lease:
            lease.complete(pending['review_id'],outcome='no_change',provider='nous',model='fixed:free',now=self.now)
        result=self.check()
        self.assertEqual(len(result['consumed_trade_ids']),5)
        self.assertEqual(len(result['unreviewed_trade_ids']),30)
        state,reviews=self.state();duplicate=deepcopy(reviews[0]);duplicate['review_id']='duplicate'
        with self.assertRaisesRegex(ValueError,'consum'):self.check(state=state,reviews=reviews+[duplicate])

    def test_worker_behind_review_or_changed_trade_cannot_qualify(self):
        self.prepare(self.rows[:5])
        with self.assertRaisesRegex(ValueError,'missing|original'):self.check(rows=self.rows[1:])
        rows=deepcopy(self.rows);rows[0]['quantity']='2'
        with self.assertRaisesRegex(ValueError,'original|snapshot'):self.check(rows=rows)

    def test_unknown_consumed_id_and_orphan_audit_cannot_reset_watermark(self):
        state,reviews=self.state();state['completed_trade_ids']=[_json(['ETH/EUR','absent'])]
        with self.assertRaisesRegex(ValueError,'watermark|consum'):self.check(state=state)
        pending=self.prepare(self.rows[:5]);state,reviews=self.state();state['pending_review']=None
        with self.assertRaisesRegex(ValueError,'pending'):self.check(state=state,reviews=reviews)

    def test_failed_attempt_retains_ids_and_recovery_classification(self):
        pending=self.prepare(self.rows[:5])
        with ReviewLease(self.root) as lease:lease.fail(pending['review_id'],reason='synthetic disconnect',now=self.now)
        rows=deepcopy(self.rows);rows[-1].update(evidence_classification='operational_recovery',exit_reason='entry_contract_recovery_exit')
        result=self.check(rows=rows)
        self.assertEqual(len(result['unreviewed_trade_ids']),34)
        self.assertEqual(len(result['recovery_trade_ids']),1)
        self.assertEqual(result['consumed_trade_ids'],[])

    def test_pending_audit_exact_json_types_and_snapshot_hash_are_required(self):
        self.prepare(self.rows[:5]);state,reviews=self.state()
        reviews[0]['trade_snapshot'][0]['quantity']=True
        with self.assertRaisesRegex(ValueError,'pending|snapshot'):self.check(state=state,reviews=reviews)

    def test_publication_links_pending_lost_ack_and_completed_review(self):
        pending=self.prepare(self.rows[:5]);request={'review_id':pending['review_id'],'review':deepcopy(pending)}
        receipt={'review_id':pending['review_id'],'status':'published'}
        with ReviewLease(self.root) as lease:
            lease.record_material(pending['review_id'],publication_intent=request,now=self.now)
        _,records=self.state();reconcile_publications(records,[(request,receipt)])
        with ReviewLease(self.root) as lease:
            lease.complete(pending['review_id'],outcome='changed',provider='nous',model='fixed:free',publication=receipt,now=self.now)
        _,records=self.state();reconcile_publications(records,[(request,receipt)])
        with self.assertRaisesRegex(ValueError,'publication'):reconcile_publications(records,[])
        with self.assertRaisesRegex(ValueError,'publication'):reconcile_publications([],[(request,receipt)])
        changed=deepcopy(records);changed[0]['trade_snapshot'][0]['quantity']='wrong'
        with self.assertRaisesRegex(ValueError,'publication'):reconcile_publications(changed,[(request,receipt)])


if __name__=='__main__':unittest.main()
