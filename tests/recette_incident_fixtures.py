"""Synthetic doubles for the 13 November 2026 incident replay (LAT-119).

Nothing here reads production state, a checkpoint, a secret or the network.
The eight-account paired layout comes from ``test_paired_paper`` (a temporary
directory with ``adaptive``/``control`` x 50/100/150/200 EUR, ETH/EUR only, a
fake public market). This module only adds the controlled clock, disk and
backup doubles that let the incident chain be replayed deterministically:

* ``IncidentWorld``  -- the eight accounts on the worker's injected clock
  (``self.at``); the fake ``sleep`` advances it and never waits;
* ``FakeDisk``       -- replaces ``shutil.disk_usage`` for storage admission;
* ``FlakyBackup``    -- the real daily backup, failing its first N calls with a
  synthetic ``ENOSPC`` (as on the full 500 MB volume of 13/11 at 02:45 UTC).
"""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import errno
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_paired_worker as worker_fixtures
from venue_fixtures import venue_record
from hermes_trading.paper_safety import record_supervisor_result

MiB = 1024 * 1024
# storage_limits.json of the synthetic experiment (paired_storage_admission).
MAX_STATE_BYTES = 512 * MiB
RESERVE_BYTES = 128 * MiB
SAFETY_POLICY = dict(schema_version=1, experiment_id='paired-test',
                     capacity_bytes=500000000, alert_fraction='0.70',
                     max_worker_errors=3, max_supervisor_errors=3,
                     supervisor_stale_seconds=300, diagnostics_max_bytes=1048576,
                     diagnostics_generations=3)
ACCOUNTS = tuple(f'{arm}_{wallet}' for arm in ('adaptive', 'control') for wallet in (50, 100, 150, 200))


class FakeDisk:
    """Deterministic free-space oracle for ``paired_storage_admission``."""

    def __init__(self, free_bytes):
        self.free_bytes = free_bytes
        self.calls = 0

    def __call__(self, path):
        self.calls += 1
        return SimpleNamespace(total=2 * MAX_STATE_BYTES, used=0, free=self.free_bytes)

    def patch(self):
        return patch('hermes_trading.paired_storage_admission.shutil.disk_usage', side_effect=self)


class FlakyBackup:
    """Real ``create_daily_backup`` failing with a synthetic ENOSPC.

    Either the first ``failures`` calls fail, or each call for which
    ``fail(index, now)`` is true (``index`` counts calls from 1).
    """

    def __init__(self, failures=0, *, fail=None):
        from hermes_trading.paper_backup import create_daily_backup
        self.fail = fail or (lambda index, now: index <= failures)
        self.real = create_daily_backup
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append(kwargs.get('now'))
        if self.fail(len(self.calls), kwargs.get('now')):
            raise OSError(errno.ENOSPC, 'synthetic no space left on device')
        return self.real(*args, **kwargs)

    def patch(self):
        return patch('hermes_trading.paper_backup.create_daily_backup', side_effect=self)


class IncidentWorld(worker_fixtures.WorkerTests):
    """Eight synthetic paper accounts driven by a controlled clock.

    Subclasses inherit the paired worker tests: a test module using this
    world must set ``load_tests = own_tests(globals())`` so that only its own
    ``test_*`` methods run.
    """

    def setUp(self):
        super().setUp()
        self.daily = (self.root.parent / (self.root.name + '-daily')).resolve()
        self.addCleanup(self._remove_daily)

    def _remove_daily(self):
        import shutil
        shutil.rmtree(self.daily, ignore_errors=True)

    # --- configuration -------------------------------------------------
    def enable_safety(self, *, supervisor=True):
        policy = dict(SAFETY_POLICY, volume_root=str(self.root.resolve()))
        (self.root / 'paper_safety.json').write_text(json.dumps(policy))
        # The safety receiver refuses future observations: follow the fake clock.
        receiver = patch('hermes_trading.paper_safety._utc_now', side_effect=lambda: self.at + timedelta(seconds=1))
        receiver.start()
        self.addCleanup(receiver.stop)
        self.supervisor_events = 0
        if supervisor:
            self.supervisor_ok()

    def enable_backup(self):
        self.limits['daily_backup'] = dict(schema_version=1, destination=str(self.daily),
                                           max_bytes=500000000, timeout_seconds=30, keep=2)
        self.write_limits()

    def write_limits(self, **changes):
        self.limits.update(changes)
        (self.root / 'worker_limits.json').write_text(json.dumps(self.limits))

    def supervisor_ok(self):
        self.supervisor_events += 1
        record_supervisor_result(self.root, success=True, event_id=f'synthetic-{self.supervisor_events}', now=self.at)

    # --- one simulated step between two worker cycles ------------------
    def refresh_market(self, *, bid='80', ask='80', close='80'):
        """Fresh synthetic quote, cost horizon and model access at ``self.at``."""
        candle = replace(self.snapshot.candle, open_timestamp=self.at - timedelta(hours=1),
                         close_timestamp=self.at)
        price = dict(self.snapshot.price, bid=bid, ask=ask, received_at=self.at.isoformat())
        self.market.collect.return_value = {'ETH/EUR': replace(self.snapshot, candle=candle, price=price,
                                                               venue_rules=venue_record('ETH/EUR', self.at))}
        self.market.collect.side_effect = None
        for profile in self.manifest['profiles']:
            path = self.root / profile['directory'] / 'wallet_costs.json'
            value = json.loads(path.read_text())
            value['as_of'] = self.at.isoformat()
            path.write_text(json.dumps(value))
        self.access['valid_until'] = (self.at + timedelta(hours=1)).isoformat()
        (self.primary / 'model_access.json').write_text(json.dumps(self.access))

    def heartbeat(self):
        return json.loads((self.root / 'heartbeat.json').read_text())

    def safety_state(self):
        return json.loads((self.root / 'paper_safety_state.json').read_text())

    def diagnostics(self):
        path = self.root / 'paper_runtime.log'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def open_positions(self):
        return {name: [p for p in self.positions(name) if p.status.value != 'closed'] for name in ACCOUNTS}

    def state_bytes(self):
        return sum(path.stat().st_size for path in Path(self.root).rglob('*') if path.is_file())


def own_tests(module_globals):
    """``load_tests`` helper: keep only tests defined in the calling module."""
    import unittest
    name = module_globals['__name__']

    def load_tests(loader, standard_tests, pattern):
        suite = unittest.TestSuite()
        for value in list(module_globals.values()):
            if isinstance(value, type) and issubclass(value, unittest.TestCase) and value.__module__ == name:
                for method in loader.getTestCaseNames(value):
                    if method in value.__dict__:
                        suite.addTest(value(method))
        return suite
    return load_tests
