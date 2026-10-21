import json,sqlite3,unittest,zlib,tempfile,random
from pathlib import Path
from unittest.mock import patch
from copy import deepcopy
from hermes_trading.reflection_supervision import _hash,_json
from hermes_trading.paired_packet_storage import store_packet,load_packet,MAX_OBJECT_BYTES


class PacketStorageTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.execute('BEGIN')
        self.packet={'schema_version':1,'created_at':'2026-10-11T00:00:00+00:00','release':{'module.py':'a'*64},
            'snapshots':{'ETH/EUR':{'price':{'received_at':'first','bid':'80','candles':[{'close':str(i)} for i in range(255)],'closes':list(range(255))}}},
            'strategies':{'adaptive':{'version':'1'}},'budgets':{'x':{'net_equity':'100'}},'custom':{'__storage_ref':'user data'}}
    def tearDown(self):self.db.close()
    def test_roundtrip_preserves_full_input_hash(self):
        saved=store_packet(self.db,self.packet);restored=load_packet(self.db,saved)
        self.assertEqual(restored,self.packet);self.assertEqual(_hash(restored),_hash(self.packet))
    def test_same_history_reused_with_distinct_quote_and_time(self):
        first=store_packet(self.db,self.packet)
        count=self.db.execute('SELECT COUNT(*) FROM paired_packet_objects').fetchone()[0]
        changed=deepcopy(self.packet);changed['created_at']='later';changed['snapshots']['ETH/EUR']['price']['received_at']='second'
        second=store_packet(self.db,changed)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM paired_packet_objects').fetchone()[0],count)
        self.assertEqual(load_packet(self.db,first),self.packet);self.assertEqual(load_packet(self.db,second),changed)
    def test_legacy_plain_json_remains_readable(self):
        self.assertEqual(load_packet(self.db,_json(self.packet)),self.packet)
    def test_missing_or_corrupt_object_rejected(self):
        saved=store_packet(self.db,self.packet)
        self.db.execute("UPDATE paired_packet_objects SET data=?",(zlib.compress(b'{}'),))
        with self.assertRaises(ValueError):load_packet(self.db,saved)
        self.db.execute('DELETE FROM paired_packet_objects')
        with self.assertRaises(ValueError):load_packet(self.db,saved)
    def test_trailing_or_truncated_packet_rejected(self):
        saved=store_packet(self.db,self.packet)
        for bad in (saved+b'extra',saved[:-1]):
            with self.assertRaises(ValueError):load_packet(self.db,bad)
    def test_oversized_object_rejected(self):
        self.packet['release']={'big':'x'*(MAX_OBJECT_BYTES+1)}
        with self.assertRaises(ValueError):store_packet(self.db,self.packet)
    def test_objects_rollback_with_financial_packet(self):
        store_packet(self.db,self.packet);self.db.rollback()
        exists=self.db.execute("SELECT 1 FROM sqlite_master WHERE name='paired_packet_objects'").fetchone()
        self.assertTrue(exists is None or self.db.execute('SELECT COUNT(*) FROM paired_packet_objects').fetchone()[0]==0)

    def test_reopen_readonly_decodes_without_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'packet.sqlite';db=sqlite3.connect(path);db.execute('BEGIN')
            saved=store_packet(db,self.packet);db.commit();db.close()
            before=path.read_bytes();readonly=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
            try:self.assertEqual(load_packet(readonly,saved),self.packet)
            finally:readonly.close()
            self.assertEqual(path.read_bytes(),before)

    def test_invalid_reference_path_rejected(self):
        from hermes_trading.paired_packet_storage import MAGIC
        saved=store_packet(self.db,self.packet);envelope=json.loads(zlib.decompress(saved[len(MAGIC):]))
        envelope['refs'][0]['path']=['custom','__storage_ref']
        with self.assertRaises(ValueError):load_packet(self.db,MAGIC+zlib.compress(_json(envelope).encode()))

    def test_generated_nested_values_roundtrip_and_hash(self):
        generator=random.Random(11011)
        def value(depth):
            if depth==0:return generator.choice([None,True,False,0,-100,10**70,'EUR é 漢字','\\\"\n','__storage_ref',''])
            kind=generator.randrange(3)
            if kind==0:return value(0)
            if kind==1:return [value(depth-1) for _ in range(generator.randrange(12))]
            return {str(index)+'é':value(depth-1) for index in range(generator.randrange(8))}
        for index in range(128):
            with self.subTest(index=index):
                packet=deepcopy(self.packet);packet['custom']=value(3)
                packet['strategies']={'generated':value(3)}
                packet['snapshots']['ETH/EUR']['price']['closes']=[value(2) for _ in range(generator.randrange(25))]
                restored=load_packet(self.db,store_packet(self.db,packet))
                self.assertEqual(restored,packet);self.assertEqual(_hash(restored),_hash(packet))

    def test_writer_admits_only_reader_reconstructable_size(self):
        from hermes_trading import paired_packet_storage
        limit=len(_json(self.packet).encode())+1
        with patch.object(paired_packet_storage,'MAX_PACKET_BYTES',limit):
            with self.assertRaisesRegex(ValueError,'bound'):
                store_packet(self.db,self.packet)
