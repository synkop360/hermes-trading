"""Native original journal to version2 view; no installed transport or worker mode."""
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import hashlib,json,sqlite3,tempfile,unittest
from pathlib import Path

import yaml
import test_paired_worker as fixtures
from hermes_trading import archive_witness as original
from hermes_trading import archive_protective_witness as witness
from hermes_trading.archive_protective_source import source_view
from hermes_trading.paired_paper import run_paired_cycle,process_paper_account
from hermes_trading.reflection_supervision import _hash,_json


class ProtectiveSourceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f=fixtures.WorkerTests();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.f.limits['max_cycles']=4;(self.f.root/'worker_limits.json').write_text(json.dumps(self.f.limits))
        for profile in self.f.manifest['profiles']:
            path=self.f.root/profile['directory'];rules=yaml.safe_load((path/'strategy.yaml').read_bytes())
            rules['simulation'].update(taker_fee_rate='.001',maker_fee_rate='.001',quantity_decimals=8)
            raw=yaml.safe_dump(rules).encode();(path/'strategy.yaml').write_bytes(raw)
            profile['strategy_sha256']=hashlib.sha256(raw).hexdigest()
        (self.f.root/'manifest.json').write_text(json.dumps(self.f.manifest))
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/'receiver.sqlite'
        self.identity=original.identity_for(self.f.root)
        self.binding=witness.initialize(self.path,self.identity,source_root=self.f.root,enabled=True,now=self.f.at)
        self.operations=0

    def head(self):return witness.read(self.path,self.binding,self.identity,enabled=True)
    def view(self,head,**kwargs):return source_view(self.f.root,self.binding,self.identity,head,enabled=True,now=self.f.at,**kwargs)
    def apply(self,head,view=None,action='check'):
        self.operations+=1
        return witness.apply(self.path,self.binding,self.identity,view or self.view(head),action=action,
            expected_head_sha256=head['head_sha256'],operation_id=f'{self.operations:032x}',enabled=True,now=self.f.at)
    async def enter(self):
        head=self.apply(self.head(),action='reserve')
        await run_paired_cycle(self.f.root,self.f.market,now=self.f.clock,reservation_id=head['state']['reservation']['reservation_id'])
        self.f.at+=timedelta(minutes=1)
        quote=replace(self.f.snapshot,price=dict(self.f.snapshot.price,bid='77',ask='78',received_at=self.f.at.isoformat()))
        self.f.market.collect.return_value={quote.asset:quote}
        return head
    async def protect(self,head,**kwargs):
        return await run_paired_cycle(self.f.root,self.f.market,now=self.f.clock,
            reservation_id=head['state']['credit']['credit_id'],operational_stop='paired_archive_stop',entries_blocked=True,**kwargs)

    def test_disabled_foreign_head_and_genesis_do_not_create_a_journal(self):
        head=self.head()
        with self.assertRaises(ValueError):source_view(self.f.root,self.binding,self.identity,head)
        self.assertEqual(self.view(head)['point']['complete'],0)
        self.assertFalse((self.f.primary/'paper_publications.sqlite').exists())
        with self.assertRaises(ValueError):self.view(dict(head,purpose='paired_archive_witness_v1'))

    async def test_original_normal_and_protective_records_form_the_exact_source_prefix(self):
        head=await self.enter();await self.protect(head)
        view=self.view(head);self.assertEqual([row['kind'] for row in view['suffix']],['ordinary','protective'])
        previous=view['prior_prefix_sha256']
        for row in view['suffix']:
            self.assertEqual(row['previous_prefix_sha256'],previous)
            self.assertEqual(row['prefix_sha256'],_hash({'previous':previous,'record':row['record']}))
            previous=row['prefix_sha256']
        done=self.apply(head,view);self.assertEqual(done['state']['point']['complete'],2)
        self.assertIsNone(done['state']['credit'])
        from hermes_trading.archive_review import canonical_closed_rows
        from hermes_trading.paired_performance_report import _canonical_account
        from hermes_trading.paired_cost_stress import completed_entry_lineages
        for profile in self.f.manifest['profiles']:
            path=self.f.root/profile['directory'];positions=self.f.positions(profile['directory'])
            self.assertEqual(len(positions),1);self.assertEqual(positions[0].status.value,'closed')
            with closing(sqlite3.connect(path/'trading.db')) as db:
                db.row_factory=sqlite3.Row;rows=canonical_closed_rows(db)
                self.assertEqual(rows[0]['exit_reason'],'paired_archive_stop')
                orders,closed,entries=_canonical_account(path,self.f.at,database=db)
                self.assertTrue(any(Decimal(order['fee'])>0 for order in orders))
                counts=completed_entry_lineages(closed,entries,start=self.f.now-timedelta(hours=1),end=self.f.at)
                self.assertEqual(counts['completed'],0)
        self.assertEqual(self.f.market.collect.await_count,2)

    async def test_original_partial_protective_packet_is_replayed_without_recollection(self):
        head=await self.enter();head=self.apply(head)
        async def fail_after_account(*args,**kwargs):
            await process_paper_account(*args,**kwargs);raise RuntimeError('after native protective account')
        with self.assertRaisesRegex(RuntimeError,'protective account'):await self.protect(head,process_account=fail_after_account)
        pending=self.view(head);self.assertEqual(pending['pending']['kind'],'protective')
        frozen=self.apply(head,pending)
        await self.protect(frozen)
        self.assertEqual(self.f.market.collect.await_count,2)
        completed=self.view(frozen);self.assertEqual(completed['suffix'][0]['input_hash'],pending['pending']['input_hash'])
        self.assertEqual(self.apply(frozen,completed)['state']['point']['complete'],2)

    async def test_protective_credit_cannot_be_claimed_by_an_ordinary_entry_packet(self):
        head=self.head()
        await run_paired_cycle(self.f.root,self.f.market,now=self.f.clock,reservation_id=head['state']['credit']['credit_id'])
        with self.assertRaisesRegex(ValueError,'protective|credit'):self.view(head)

    async def test_one_changed_account_result_cannot_claim_a_completed_eight_account_view(self):
        head=await self.enter()
        from hermes_trading.paired_packet_storage import load_packet,store_record
        with closing(sqlite3.connect(self.f.primary/'paper_publications.sqlite')) as db:
            value=load_packet(db,db.execute('SELECT completed FROM paired_batches').fetchone()[0])
            value['adaptive_50']['input_hash']='f'*64
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE paired_batches SET completed=?',(store_record(db,value),));db.commit()
        with self.assertRaisesRegex(ValueError,'account|settlement|input'):self.view(head)

    async def test_source_byte_and_time_bounds_do_not_mutate_the_original_journal(self):
        head=await self.enter();path=self.f.primary/'paper_publications.sqlite';before=path.read_bytes()
        with self.assertRaises(ValueError):self.view(head,max_bytes=1)
        ticks=iter((0,20))
        with self.assertRaises(TimeoutError):self.view(head,clock=lambda:next(ticks,20))
        self.assertEqual(path.read_bytes(),before)


if __name__=='__main__':unittest.main()
