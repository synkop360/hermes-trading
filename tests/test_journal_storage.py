import json,sqlite3,tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from hermes_trading.wallet_metrics import WalletJournal
from hermes_trading.reflection_supervision import _json

AT=datetime(2026,10,11,tzinfo=timezone.utc)

class JournalStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'wallet.sqlite'
        self.config={'experiment_id':'test','currency':'EUR','starting_balance':'100','activated_at':AT.isoformat()}
        self.row={'at':AT.isoformat(),'currency':'EUR','starting_balance':'100','external_flow':'0','net_equity':'100','unknowns':[]}
    def tearDown(self):self.tmp.cleanup()
    def test_compressed_observation_exact_and_immutable(self):
        with WalletJournal(self.path,self.config) as journal:
            journal.append(self.row,now=AT)
            stored=journal.db.execute('SELECT payload FROM observations').fetchone()[0]
            self.assertIsInstance(stored,bytes)
            self.assertEqual(journal.observations(),[self.row])
            self.assertFalse(journal.append(self.row,now=AT))
            with self.assertRaises(ValueError):journal.append(dict(self.row,net_equity='99'),now=AT)
    def test_legacy_and_new_rows_reopen_together(self):
        with WalletJournal(self.path,self.config) as journal:
            journal.db.execute('INSERT INTO observations VALUES (?,?)',(self.row['at'],_json(self.row)))
            later=dict(self.row,at=(AT+timedelta(minutes=1)).isoformat(),net_equity='101')
            journal.append(later,now=AT+timedelta(minutes=1))
        with WalletJournal(self.path,self.config) as journal:self.assertEqual(journal.observations(),[self.row,later])
    def test_row_timestamp_must_match_primary_key(self):
        with WalletJournal(self.path,self.config) as journal:
            journal.append(self.row,now=AT)
            journal.db.execute('UPDATE observations SET at=?',((AT+timedelta(minutes=1)).isoformat(),))
            with self.assertRaisesRegex(ValueError,'timestamp'):journal.observations()
    def test_corrupted_compressed_row_is_not_a_zero(self):
        with WalletJournal(self.path,self.config) as journal:
            journal.append(self.row,now=AT)
            journal.db.execute('UPDATE observations SET payload=?',(b'PCP1broken',))
            with self.assertRaises(ValueError):journal.observations()

    def test_canonical_types_cannot_be_rewritten_as_equal_python_values(self):
        with WalletJournal(self.path,self.config) as journal:
            row=dict(self.row,quality_flag=True);journal.append(row,now=AT)
            with self.assertRaises(ValueError):journal.append(dict(row,quality_flag=1),now=AT)
