"""Native, entirely invented state for the approved paper transition tests.

No installed state is copied. Quotes, the clock, catalog metadata, review
materials and usage receipts are synthetic. Native writers create all journals;
no provider, model, deployment or order transport is available to this fixture.
"""
from contextlib import ExitStack, closing, contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch

from hermes_trading.reflection_supervision import _connect, _hash, _json, _time


OLD_COMMIT = '6c9d520da5f8473b51c912d1eb0e269ee94d4b1b'
NEW_COMMIT = 'fb5b65417dfd23f67e9996c25b514bae3af0e723'
PAPER_ENV = {'HERMES_TRADING_MODE': 'paper', 'HERMES_TRADING_ALLOW_REAL_ORDERS': 'false'}


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    Path(path).write_text(_json(value) + '\n', encoding='utf-8')


class SyntheticCyclePaused(RuntimeError):
    """Stop after native commit/heartbeat without consuming the finite budget."""


class NativeTransitionFixture:
    """Use ``await NativeTransitionFixture.create(testcase)``; cleanup is owned
    by that unittest case. ``at`` is an explicitly synthetic, mutable UTC clock.

    ``advance_worker`` resumes native paper boot for exactly one batch then
    releases every connection. The original worker bound is four batches.
    """

    @classmethod
    async def create(cls, testcase):
        self = cls()
        self.testcase = testcase
        from test_paper_boot import PaperBootTests
        boot_fixture = PaperBootTests()
        # The helper case has no running asyncio runner; register its resources
        # directly with the active test instead of invoking its doCleanups.
        boot_fixture.addCleanup = testcase.addCleanup
        self.boot, self.inception_fixture = boot_fixture.ready_fixture()
        self.worker_root = self.inception_fixture.root.resolve()
        self.market = self.inception_fixture.fixture.market
        self.snapshot = self.inception_fixture.fixture.snapshot
        self.at = self.inception_fixture.at
        temporary = tempfile.TemporaryDirectory(prefix='synthetic-release-transition-')
        testcase.addCleanup(temporary.cleanup)
        self.project_root = Path(temporary.name).resolve() / 'project'
        self.project_root.mkdir()
        self.supervisor_root = self.project_root / 'state/paired-supervisor/paired-test/adaptive_200'
        self.supervisor_root.mkdir(parents=True)
        self.launch_file = self.project_root / 'launch.json'
        self.audit_dir = Path(temporary.name).resolve() / 'transition-audit'
        self.primary = self.worker_root / 'adaptive_200'
        self.manifest = read_json(self.worker_root / 'manifest.json')
        from hermes_trading.paired_primary import resolve_primary
        self.selection = resolve_primary(self.manifest)
        self.account = read_json(self.primary / 'wallet_account.json')
        self.policy = read_json(self.primary / 'reflection_owner.json')
        self.policy.update(wallet_required=True, model_access_renewal_required=True,
                           supervisor_lifecycle_required=True)
        self.policy['model'] = {'provider': 'nous', 'name': 'upstage/solar-mini4:free'}
        self.old_release, self.new_release = self._releases()
        self.release_plan = {
            'old_commit': OLD_COMMIT, 'new_commit': NEW_COMMIT,
            'target': {'project': 'f78f42d9-20ec-44f6-ae3f-f8bb79ee7000',
                       'service': '8b94cbc9-293e-4222-a8a7-698b656985f7',
                       'environment': 'fdcc71a3-00a9-410c-8d6f-59af9bc98d36',
                       'experiment_id': 'paired-test'},
            'old': self.old_release, 'new': self.new_release,
        }
        self._configure()
        with self.release_context(self.old_release):
            self.renew_access()
            self._seed_review_history()
            self.import_usage()
            self._seed_supervisor_lifecycle()
            from hermes_trading.paired_inception import initialize_paired_inception
            self.opening = initialize_paired_inception(self.worker_root, clock=lambda: self.at)
        self.expected_identity = {
            'experiment_id': 'paired-test',
            'manifest_sha256': hashlib.sha256((self.worker_root / 'manifest.json').read_bytes()).hexdigest(),
            'opening_hash': _hash(self.opening), 'policy_hash': _hash(self.policy),
            'account_hashes': {row['directory']: _hash(read_json(
                self.worker_root / row['directory'] / 'wallet_account.json'))
                for row in self.manifest['profiles']},
        }
        await self.advance_worker(self.old_release, seconds=1)
        return self

    @staticmethod
    def _releases():
        from hermes_trading import archive_snapshot
        from scripts.hermes_watch import _supervisor_release
        successor = _supervisor_release()
        baseline = deepcopy(successor)
        source = Path(archive_snapshot.__file__).read_bytes()
        corrected = b'[A-Za-z0-9_.+-]+'
        original = b'[A-Za-z0-9_.-]+'
        if source.count(corrected) != 1:
            raise AssertionError('fixture requires the approved single plus-character correction')
        baseline['runtime']['archive_snapshot.py'] = hashlib.sha256(
            source.replace(corrected, original)).hexdigest()
        return baseline, successor

    @contextmanager
    def release_context(self, release):
        """Patch every actual native import site, including the worker guard."""
        with ExitStack() as stack:
            for name in ('paper_publication', 'paired_paper', 'paired_worker', 'supervisor_sync',
                         'model_access_refresh', 'supervisor_usage_feed'):
                stack.enter_context(patch('hermes_trading.' + name + '._release_hashes',
                                          return_value=deepcopy(release['runtime'])))
            yield

    def _configure(self):
        limits = read_json(self.worker_root / 'worker_limits.json')
        limits['max_cycles'] = 4
        limits['daily_backup']['destination'] = str(self.project_root.parent / 'daily')
        write_json(self.worker_root / 'worker_limits.json', limits)
        self.access = {'schema_version': 1, 'provider': 'nous', 'model': self.policy['model']['name'],
                       'policy_hash': _hash(self.policy), 'payment': 'verified_free',
                       'provenance_ref': 'synthetic_reviewed_initial_free_diagnostic',
                       'tools_allowed': False, 'paid_calls_authorized': False,
                       'verified_at': (self.at - timedelta(seconds=1)).isoformat(),
                       'valid_until': (self.at + timedelta(minutes=5)).isoformat()}
        for state in (self.primary, self.supervisor_root):
            write_json(state / 'reflection_owner.json', self.policy)
            write_json(state / 'model_access.json', self.access)
        for name in ('wallet_account.json', 'strategy.yaml', 'goal.yaml'):
            (self.supervisor_root / name).write_bytes((self.primary / name).read_bytes())
        start = self.account['activated_at']
        for profile in self.manifest['profiles']:
            state = self.worker_root / profile['directory']
            event_source = lambda source, events: {
                'mode': 'events', 'source': source, 'coverage_start': start,
                'coverage_end': self.at.isoformat(), 'events': events}
            hosting = {'id': 'synthetic-initial-hosting', 'occurred_at': start,
                       'currency': 'EUR', 'amount': '.01', 'quality': 'actual'}
            ai = event_source('synthetic_explicit_no_control_inference', [])
            if profile['arm'] == 'adaptive':
                ai = {'mode': 'supervised', 'source': 'supervised_ai_with_subscription_v1',
                      'policy_hash': _hash(self.policy),
                      'subscription': {'daily_rate': '0', 'quality': 'actual',
                                       'source': 'synthetic_free_personal_subscription'}}
            write_json(state / 'wallet_cost_sources.json', {
                'schema_version': 1, 'experiment_id': 'paired-test', 'currency': 'EUR',
                'scope_start': start, 'categories': {
                    'hosting': event_source('synthetic_hosting_receipt', [hosting]),
                    'ai': ai, 'tools': event_source('synthetic_explicit_no_tools', []),
                    'funding': {'mode': 'spot_zero', 'source': 'unleveraged_spot_no_borrowing',
                                'quality': 'actual'}}})
        supervisor_limits = {
            'schema_version': 1, 'experiment_id': 'paired-test', 'policy_hash': _hash(self.policy),
            'activated_at': start, 'max_cycles': 20, 'max_runtime_seconds': 86400,
            'interval_seconds': 60, 'max_model_attempts': 2,
            'release_hash': _hash(self.old_release), 'primary_wallet_eur': 200,
            'primary_selection_sha256': self.selection.selection_sha256,
            'account_hash': _hash(self.account),
        }
        for state in (self.primary, self.supervisor_root):
            write_json(state / 'supervisor_limits.json', supervisor_limits)
        write_json(self.launch_file, {
            'schema_version': 2, 'status': 'activated', 'enabled': True,
            'experiment_id': 'paired-test', 'project_dir': str(self.project_root),
            'local_state_dir': str(self.supervisor_root),
            'mirror_dir': str(self.supervisor_root.parent / 'mirror'),
            'remote_state_dir': '/app/state/adaptive_200',
            'policy_hash': _hash(self.policy), 'account_hash': _hash(self.account),
            'release_hash': _hash(self.old_release), 'primary_wallet_eur': 200,
            'primary_selection_sha256': self.selection.selection_sha256,
            'expires_at': (_time(start) + timedelta(seconds=86400)).isoformat(),
            'max_script_seconds': 240,
        })

    def renew_access(self, release=None, *, include_pc=False):
        """Native five-minute renewal from mocked GET metadata; no inference.

        The initial PC import is historical. Later renewals target only worker
        state unless a test deliberately requests ``include_pc``.
        """
        from hermes_trading import model_access_refresh as refresh
        release = self.old_release if release is None else release
        existing = read_json(self.primary / 'model_access.json')
        catalog = {'data': [{'id': self.policy['model']['name'],
                             'pricing': {'prompt': '0', 'completion': '0', 'request': '0'}}]}
        def exchange(method, route, **kwargs):
            if (method, route) != ('GET', '/models'):
                raise AssertionError('synthetic metadata provider accepts GET /models only')
            kwargs['raw_capture'](_json(catalog).encode())
            return deepcopy(catalog)
        with self.release_context(release), \
                patch.object(refresh, '_credentials', return_value={
                    'api_key': 'synthetic-never-valid',
                    'base_url': 'https://inference-api.nousresearch.com/v1'}), \
                patch.object(refresh, '_exchange', side_effect=exchange), \
                patch.object(refresh, '_subscription', return_value={'context': 'personal', 'current': None}):
            access = refresh.probe_free_access(self.policy, existing, now=self.at)
            request = refresh.build_access_request(access, self.policy, self.account)
            receipt = refresh.install_free_access(self.primary, request, env=PAPER_ENV, now=self.at)
            if include_pc or not (self.supervisor_root / 'model_access_refresh.sqlite').exists():
                refresh.install_free_access(self.supervisor_root, request, env=PAPER_ENV, now=self.at)
        return receipt

    def _seed_review_history(self):
        """Native audit transitions for declared synthetic historical materials."""
        from hermes_trading.reflection_supervision import initialize_supervisor, ReviewLease
        from hermes_trading.costs import CostEvent, CostLedger
        initialize_supervisor(self.supervisor_root, now=_time(self.account['activated_at']))
        trades = [{'asset': 'ETH/EUR', 'position_id': 'synthetic-history-' + str(i),
                   'closed_at': (self.at - timedelta(seconds=6-i)).isoformat(), 'pnl': '0'}
                  for i in range(1, 6)]
        with ReviewLease(self.supervisor_root) as lease:
            review = lease.prepare(trades, now=self.at)
            lease.record_material(review['review_id'], now=self.at,
                request={'synthetic': True, 'reason': 'invented historical transition fixture'},
                response={'synthetic': True, 'output': {'outcome': 'no_change'}},
                decision={'synthetic': True, 'status': 'no_change'})
            raw = _json({'model': self.policy['model']['name'],
                         'usage': {'prompt_tokens': 10, 'completion_tokens': 2}}).encode()
            catalog = read_json(self.primary / 'model_access.json')['renewal']
            CostLedger(self.supervisor_root).record(CostEvent(
                'hermes', Decimal('0'), 'USD', 'actual', 'synthetic_usage_receipt', self.at,
                metadata={'synthetic': True, 'experiment_id': 'paired-test',
                          'policy_hash': _hash(self.policy), 'provider': 'nous',
                          'model': self.policy['model']['name'], 'review_id': review['review_id'],
                          'request_started_at': review['started_at'], 'input_tokens': 10, 'output_tokens': 2,
                          'pricing_verification': {
                              'source': catalog['catalog_source'], 'model': self.policy['model']['name'],
                              'verified_at': self.at.isoformat(), 'payment': 'verified_zero_catalog_price',
                              'catalog_sha256': catalog['catalog_sha256'], 'pricing': catalog['pricing']},
                          'raw_http_response_b64': base64.b64encode(raw).decode(),
                          'raw_http_sha256': hashlib.sha256(raw).hexdigest()}))
            lease.complete(review['review_id'], outcome='no_change', provider='nous',
                           model=self.policy['model']['name'], now=self.at)

    def import_usage(self, release=None):
        """Export native review/ledger coverage at the synthetic observation."""
        from hermes_trading.supervisor_usage_source import export_supervisor_usage
        from hermes_trading.supervisor_usage_feed import build_usage_request, install_supervisor_usage
        release = self.old_release if release is None else release
        with self.release_context(release):
            self.usage = export_supervisor_usage(self.supervisor_root, self.account, now=self.at)
            request = build_usage_request(self.usage, self.policy, self.account)
            return install_supervisor_usage(self.primary, request, env=PAPER_ENV, now=self.at)

    def _seed_supervisor_lifecycle(self):
        from hermes_trading.supervisor_lifecycle import admit_cycle
        with closing(_connect(self.supervisor_root / 'watcher_cycle.sqlite')) as cycle_db:
            cycle_db.commit()
            admitted = admit_cycle(self.supervisor_root, self.policy, self.old_release, now=self.at)
            if admitted.status != 'admitted':
                raise AssertionError('synthetic supervisor lifecycle was not admitted')

    async def advance_worker(self, release=None, *, seconds=60):
        """Resume native boot and pause after one complete common batch."""
        from hermes_trading.paired_worker import run_worker
        from venue_fixtures import venue_record
        release = self.old_release if release is None else release
        runtime = self.worker_root / 'worker_runtime.sqlite'
        previous_count = 0
        if runtime.exists():
            with closing(sqlite3.connect(runtime)) as db:
                previous_count = json.loads(db.execute(
                    'SELECT state FROM lifecycle WHERE id=1').fetchone()[0])['completed_batches']
        self.at += timedelta(seconds=seconds)
        snapshot = replace(self.snapshot,
                           price=dict(self.snapshot.price, received_at=self.at.isoformat()),
                           venue_rules=venue_record('ETH/EUR', self.at))
        self.market.collect.return_value = {'ETH/EUR': snapshot}
        async def pause(seconds):
            raise SyntheticCyclePaused('synthetic pause after native batch commit and heartbeat')
        async def worker(root):
            return await run_worker(root, collector_factory=lambda: self.market,
                                    clock=lambda: self.at, sleep=pause)
        with self.release_context(release), \
                patch('hermes_trading.paper_safety._utc_now', side_effect=lambda: self.at), \
                patch.object(self.boot, 'run_worker', side_effect=worker):
            try:
                result = await self.boot.run_paper(self.worker_root, env=PAPER_ENV)
            except SyntheticCyclePaused:
                with closing(sqlite3.connect(runtime)) as db:
                    state = json.loads(db.execute('SELECT state FROM lifecycle WHERE id=1').fetchone()[0])
                if state['completed_batches'] != previous_count + 1:
                    raise AssertionError('native worker did not complete exactly one new synthetic batch')
                return state
            raise AssertionError('native worker stopped before the synthetic pause: ' + repr(result))
