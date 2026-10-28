"""Summarize saved native evidence and reverify the completed E: backup."""
from pathlib import Path
from datetime import datetime,timezone
import json,sys,time
RUN=Path(__file__).resolve().parent;LIVE=RUN/'live-cutover';c=json.loads((LIVE/'config.json').read_text())
load=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
worker=load(LIVE/'observe_final.native-result.json')['result'];pc=load(sorted(LIVE.glob('*-pc-installed.json'))[-1])
ready=load(LIVE/'release_successor_readiness.native-result.json')['result'];baseline=load(LIVE/'closed-observation-baseline.json')
assert worker['heartbeat']['status']=='ok' and worker['owned_worker_count']==1 and all(worker['environment'].values())
assert all(worker['cohort'][key] is True for key in ('all_eight_account_hashes_match','all_eight_common_observation_time','all_eight_match_latest_settlement','current_journal_matches_heartbeat','heartbeat_stable_during_read'))
assert worker['cohort']['new_completed_since_closed_baseline']>=5
assert worker['supervisor_safety']['supervisor']['entry_halt'] is True
assert all(x['status']=='observed' and x['orders']==0 and not x['positions'] for x in worker['trading'].values())
assert pc['native_launch_valid'] and pc['lifecycle']['model_attempts_reserved']==0 and pc['post_readiness_counts'].get('completed',0)>=3
assert all(row['status']=='completed' for row in pc['latest_runs'][-3:])
assert worker['reporter']['confirmed_cycle_windows']>int(baseline['reporter']['cadences']['cycle'])
backup=load(Path(c['pc_project'])/'state/paired-supervisor/paper-beta-v1-20261021-6c9d520/backup-last.stdout.json')
assert backup['status']=='completed' and backup['off_host_verified'] is True
assert datetime.fromisoformat(backup['captured_at'])>datetime.fromisoformat(ready['ready_at'])
sys.path.insert(0,c['pc_project']);from hermes_trading.paper_backup import _verified
deadline=time.monotonic()+30
def check():
    if time.monotonic()>deadline:raise TimeoutError('backup verification deadline')
verified=_verified(Path(backup['path']),backup['source_identity'],check,500_000_000)
assert verified['sha256']==backup['sha256']
gap=(datetime.fromisoformat(ready['ready_at'])-datetime.fromisoformat(baseline['heartbeat']['observed_at'])).total_seconds()
value=dict(observed_at=datetime.now(timezone.utc).isoformat(),status='paper_correction_deployed_and_operational_checks_completed',
    candidate=c['candidate'],deployment='dd6ebf42-7cf9-4d85-addd-8b4605edf423',instance='b295439f-053d-4911-8b55-5db4e01f00cc',
    transition_id=ready['transition_id'],ready_at=ready['ready_at'],original_opening_hash=ready['original_opening_hash'],
    previous_observed_at=baseline['heartbeat']['observed_at'],observation_gap_to_readiness_seconds=gap,
    original_clocks_history_wallets_and_halt_preserved=True,runtime=worker['runtime'],environment=worker['environment'],owned_worker_count=1,
    new_completed_batches=worker['cohort']['new_completed_since_closed_baseline'],all_eight_settlements_verified=True,
    supervisor=pc,first_failed_cycle='retained; access renewal and usage bootstrap committed; exact exception unavailable because native wrapper discards child stderr',
    discord=dict(confirmed_cycle_windows_before=int(baseline['reporter']['cadences']['cycle']),confirmed_cycle_windows_after=worker['reporter']['confirmed_cycle_windows'],counters=worker['reporter']['counters'],
        evidence='local validated sender part acknowledgments; no retained Discord message IDs or remote body screenshot'),
    backup=backup,primary_wallet=worker['accounts']['adaptive_200'],all_orders_zero_and_positions_empty=True,
    tests={p:load(RUN/(p+'-package.json'))['results'] for p in ('windows','linux')},
    limitations=['Affected package checks, not a full-suite pass.','One first scheduled failure followed by three successes; long-run reliability unproved.',
        'The overnight preparation window expired; gap retained, never replayed.','Original halt remains; live acknowledgment is outside current approval.',
        'No close alert or LLM steering observed because no trades have closed.','Profitability and minimum profitable wallet remain unproved.'])
with (LIVE/'OPERATIONAL_ACCEPTANCE.json').open('x',encoding='utf-8') as f:json.dump(value,f,indent=2);f.write('\n')
print(json.dumps({k:value[k] for k in ('status','candidate','ready_at','observation_gap_to_readiness_seconds','new_completed_batches','all_eight_settlements_verified')}))
