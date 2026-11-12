from datetime import timedelta
from copy import deepcopy
import hashlib,json,sqlite3,unittest
import uuid,yaml

from hermes_trading.publication_timeline import PublicationTimeline


class PublicationTimelineTests(unittest.TestCase):
    def setUp(self):
        from test_paper_publication import PaperPublicationTests,NOW
        self.fixture=PaperPublicationTests();self.fixture.setUp()
        self.state=self.fixture.state;self.now=NOW
        self.original=(self.state/'strategy.yaml').read_bytes()
        self.baseline=hashlib.sha256(self.original).hexdigest()
        self.request=self.fixture.stage();self.receipt=self.fixture.publish(self.request)
    def tearDown(self):self.fixture.tearDown()

    def timeline(self):
        db=sqlite3.connect((self.state/'paper_publications.sqlite').resolve().as_uri()+'?mode=ro',uri=True)
        try:return PublicationTimeline(self.state,db,self.baseline,now=self.now+timedelta(hours=1))
        finally:db.close()

    def mutate(self,change):
        db=sqlite3.connect(self.state/'paper_publications.sqlite')
        try:change(db);db.commit()
        finally:db.close()

    def test_real_publication_is_available_only_after_closed_signal_cutoff(self):
        timeline=self.timeline()
        self.assertEqual(timeline.strategy_at(self.now,self.now+timedelta(seconds=1))['version'],'01')
        self.assertEqual(timeline.strategy_at(self.now-timedelta(microseconds=1),self.now)['version'],'01')
        self.assertEqual(timeline.strategy_at(self.now+timedelta(microseconds=1),self.now+timedelta(seconds=1))['version'],'02')
        self.assertEqual(timeline.strategy_at(None,self.now+timedelta(seconds=1))['version'],'02')

    def test_receipt_must_bind_original_request_and_candidate(self):
        receipt=deepcopy(self.receipt);receipt['candidate_sha256']='wrong'
        self.mutate(lambda db:db.execute('UPDATE publications SET receipt=?',(json.dumps(receipt),)))
        with self.assertRaisesRegex(ValueError,'receipt'):self.timeline()

    def test_request_hash_and_original_base_bytes_are_checked(self):
        self.mutate(lambda db:db.execute("UPDATE publications SET request_hash='wrong'"))
        with self.assertRaisesRegex(ValueError,'request'):self.timeline()

    def test_unfinished_publication_cannot_establish_availability(self):
        self.mutate(lambda db:db.execute('UPDATE publications SET receipt=NULL'))
        with self.assertRaisesRegex(ValueError,'unfinished'):self.timeline()

    def test_archive_and_current_strategy_cannot_diverge_from_chain(self):
        (self.state/'history/v0001.yaml').write_text('version: 01\n')
        with self.assertRaisesRegex(ValueError,'archive'):self.timeline()

    def test_publication_cannot_precede_review_or_be_future_dated(self):
        for stamp in (self.now-timedelta(seconds=1),self.now+timedelta(days=2)):
            receipt=deepcopy(self.receipt);receipt['published_at']=stamp.isoformat()
            self.mutate(lambda db:db.execute('UPDATE publications SET receipt=?',(json.dumps(receipt),)))
            with self.assertRaisesRegex(ValueError,'time'):self.timeline()

    def test_selection_cannot_use_a_future_signal(self):
        timeline=self.timeline()
        with self.assertRaisesRegex(ValueError,'future'):
            timeline.strategy_at(self.now+timedelta(seconds=2),self.now)

    def test_two_publications_at_same_time_use_durable_commit_order(self):
        from hermes_trading.paper_publication import build_publication_request,publish_paper_request
        from hermes_trading.reflection_supervision import _hash
        from hermes_trading.paired_paper import _causal_strategy
        from hermes_trading.loop import _load_yaml
        from hermes_trading.market_data import MarketSnapshot,ClosedCandle
        from decimal import Decimal
        raw=(self.state/'strategy.yaml').read_bytes();base=yaml.safe_load(raw)
        candidate=deepcopy(base);candidate['symbols']['BTC/EUR']['entry']['threshold']=32;candidate['version']='03'
        review=deepcopy(self.request['review']);review['review_id']=str(uuid.uuid4())
        review['request'].update(strategy=base,strategy_sha256=hashlib.sha256(raw).hexdigest())
        review['decision'].update(candidate=candidate,based_on_sha256=hashlib.sha256(raw).hexdigest())
        policy=json.loads((self.state/'reflection_owner.json').read_text())
        request=build_publication_request(review,policy)
        publish_paper_request(self.state,request,env={'HERMES_TRADING_MODE':'paper'},now=self.now)
        timeline=self.timeline()
        self.assertEqual(timeline.strategy_at(self.now,self.now+timedelta(seconds=1))['version'],'01')
        after=self.now+timedelta(seconds=1)
        self.assertEqual(timeline.strategy_at(after,after)['version'],'03')
        snapshot=MarketSnapshot('BTC/EUR','5m','available','evaluate','test',
            ClosedCandle(after-timedelta(minutes=5),after,Decimal(1),Decimal(1),Decimal(1),Decimal(1),None))
        db=sqlite3.connect(self.state/'paper_publications.sqlite')
        try:
            selected=_causal_strategy(db,_load_yaml(self.state/'strategy.yaml'),
                (self.state/'strategy.yaml').read_bytes(),{'BTC/EUR':snapshot},now=after,
                baseline_hash=self.baseline,baseline_path=self.state/'history/v0001.yaml')
            self.assertEqual(selected['version'],'03')
        finally:db.close()
