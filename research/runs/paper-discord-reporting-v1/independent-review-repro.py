"""Independent candidate-review probes. Synthetic fixtures/fake IO only.

From the repository root, set PYTHONPATH=tests;. and run with the shared
interpreter, -B -X utf8, and one of: archive, shutdown, costs.
The assertions intentionally fail on candidate bceeea6.
"""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import httpx


def archive_probe():
    from test_archive_protective_worker import ProtectiveWorkerTests
    from hermes_trading.paired_worker import run_worker

    class ArchiveProbe(ProtectiveWorkerTests):
        async def test_archive_protection_has_settled_close_observation(self):
            offers = []
            state = await run_worker(self.f.f.root,
                collector_factory=lambda: self.f.f.market, clock=self.f.f.clock,
                sleep=self.outage, witness=self.client, observer=offers.append)
            closed = {p['directory']: self.f.f.positions(p['directory'])[0].status.value
                      for p in self.f.f.manifest['profiles']}
            print(json.dumps(dict(status=state['status'],
                completed_batches=state['completed_batches'], closed=closed,
                offers=[dict(kind=e['kind'], completed_batches=e['completed_batches'],
                    error=e['error'], close_count=sum(len(a.get('closes', []))
                    for a in e['accounts'].values())) for e in offers]), sort_keys=True))
            self.assertTrue(all(s == 'closed' for s in closed.values()))
            self.assertTrue(any(e['kind'] == 'settled' and e['completed_batches'] == 2
                and sum(len(a.get('closes', [])) for a in e['accounts'].values()) == 8
                for e in offers), 'Missing settled notification for eight canonical archive-protection closes')

    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([
        ArchiveProbe('test_archive_protection_has_settled_close_observation')]))
    return not result.wasSuccessful()


def shutdown_probe():
    from test_paper_notifications import envelope, identity, settings
    from hermes_trading.notifications import PaperDiscordReporter
    entered = threading.Event()

    async def fake_poster(url, payload):
        raise AssertionError('no dispatch expected')

    def stalled_persist():
        entered.set()
        time.sleep(3)
        return True

    with tempfile.TemporaryDirectory() as tmp:
        reporter = PaperDiscordReporter(Path(tmp), identity(), settings(), post_json=fake_poster)
        reporter._persist = stalled_persist
        started = time.monotonic()

        async def main():
            reporter.offer(envelope())
            task = asyncio.create_task(reporter.run())
            while not entered.is_set():
                await asyncio.sleep(.001)
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=2)
            assert done, 'reporter cancellation did not finish'
            print(json.dumps(dict(run_paper_style_cleanup_seconds=round(time.monotonic()-started, 3),
                                  reporter_task_done=task.done())))

        asyncio.run(main())
        elapsed = time.monotonic()-started
        print(json.dumps(dict(asyncio_run_total_seconds=round(elapsed, 3),
                              promised_shutdown_bound_seconds=2)))
        assert elapsed < 2, 'asyncio.run waited for stalled metadata executor thread after reporter cancellation'
    return 0


def costs_probe():
    from test_paper_notification_capture import NotificationCaptureTests
    from test_paper_notifications import settings
    from hermes_trading.notifications import PaperDiscordReporter

    class CostsProbe(NotificationCaptureTests):
        async def test_estimated_native_cost_quality_survives_rendering(self):
            path = self.root/'adaptive_200/wallet_costs.json'
            cost = json.loads(path.read_text())
            cost['totals']['hosting']['quality'] = 'estimated'
            path.write_text(json.dumps(cost))
            capture = (await self.cycle())['notification']
            name = 'adaptive_200'
            native = capture['accounts'][name]['observation']['cost_provenance']
            identity = dict(experiment_id='synthetic', opening_at=self.now.isoformat(),
                opening_hash='opening', manifest_hash=capture['manifest_hash'], primary_wallet_eur=200,
                account_hashes={n: r['account_hash'] for n, r in capture['accounts'].items()})
            reporter = PaperDiscordReporter(self.root, identity, settings(), post_json=lambda *args: None)
            reporter._state = {'last_observed_at': None}
            reporter._snapshot = capture
            reporter._current = dict(at=self.now.isoformat(), status='observed')
            lines = reporter._summary(['synthetic'])
            index = next(i for i, line in enumerate(lines) if line.startswith(name+':'))
            print(json.dumps(dict(native_provenance=native, rendered_cost_line=lines[index+1]), sort_keys=True))
            self.assertIn('estimated', lines[index+1],
                'Estimated hosting costs disappear from rendered native provenance')

    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([
        CostsProbe('test_estimated_native_cost_quality_survives_rendering')]))
    return not result.wasSuccessful()


if __name__ == '__main__':
    probes = dict(archive=archive_probe, shutdown=shutdown_probe, costs=costs_probe)
    # Both transport implementations are forbidden even if a probe is changed.
    with patch.object(httpx.HTTPTransport, 'handle_request', side_effect=AssertionError('real HTTP forbidden')), \
         patch.object(httpx.AsyncHTTPTransport, 'handle_async_request', side_effect=AssertionError('real HTTP forbidden')):
        raise SystemExit(probes[sys.argv[1]]())
