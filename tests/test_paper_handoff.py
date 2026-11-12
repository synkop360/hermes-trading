import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
import unittest
import tempfile,sqlite3,json
from contextlib import closing
from scripts.paper_handoff import quiesce


class HandoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_storage_handoff_holds_real_worker_lease_and_rejects_pending_work(self):
        from scripts.paper_handoff import closed_storage_boundary
        from hermes_trading.archive_snapshot import ownership
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            with closing(sqlite3.connect(root/'worker_runtime.sqlite')) as db:
                db.execute('CREATE TABLE lifecycle(id INTEGER PRIMARY KEY,state TEXT)')
                db.execute('INSERT INTO lifecycle VALUES(1,?)',(json.dumps({'storage_stop':True}),));db.commit()
            (root/'manifest.json').write_text('{}')
            with patch('hermes_trading.paired_worker._profiles',return_value={('adaptive',200):(root,None,None)}), \
                    patch('hermes_trading.paired_primary.resolve_primary',return_value=SimpleNamespace(wallet_eur=200)), \
                    patch('hermes_trading.paired_worker._active_storage_positions',return_value={'adaptive_200':0}), \
                    patch('hermes_trading.paired_worker._stats',return_value={'pending':False,'latest':{'input_hash':'bound'}}) as stats:
                self.assertTrue(closed_storage_boundary(root))
                stats.return_value={'pending':True,'latest':{'input_hash':'bound'}}
                with self.assertRaises(ValueError):closed_storage_boundary(root)
                with ownership(root,role='worker',enabled=True):
                    with self.assertRaises(sqlite3.OperationalError):closed_storage_boundary(root)
    async def test_stops_only_after_native_settled_observation_and_suppresses_result(self):
        closed=[]
        async def worker(root,**options):
            try:
                options['observer']({'kind':'operational','private':'wallet'})
                options['observer']({'kind':'settled','private':'wallet'})
                self.fail('collection continued after settlement')
            finally:closed.append(True)
        boot=SimpleNamespace(run_worker=worker)
        async def run(root):return await boot.run_worker(root)
        boot.run_paper=run;stream=io.StringIO()
        with patch('scripts.paper_handoff.runtime_hash',return_value='19fdcb676120b21b44d54076de08e148259e34bdfe61bbdbdffbf20b694e12ed'):
            self.assertEqual(await quiesce(Path('/app/state'),boot=boot,stream=stream),'quiescent')
        self.assertEqual(closed,[True]);self.assertIs(boot.run_worker,worker)
        self.assertNotIn('private',stream.getvalue());self.assertNotIn('wallet',stream.getvalue())

    async def test_wrong_source_never_starts_the_worker(self):
        boot=SimpleNamespace(run_paper=AsyncMock(),run_worker=AsyncMock())
        with patch('scripts.paper_handoff.runtime_hash',return_value='wrong'):
            with self.assertRaises(ValueError):await quiesce(Path('/app/state'),boot=boot)
        boot.run_paper.assert_not_called()


if __name__=='__main__':unittest.main()
