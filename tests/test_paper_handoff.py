import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
import unittest
from scripts.paper_handoff import quiesce


class HandoffTests(unittest.IsolatedAsyncioTestCase):
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
