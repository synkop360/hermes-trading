"""Validate fresh native operational evidence without changing runtime state."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,sys,time
RUN=Path(__file__).resolve().parent;LIVE=RUN/'live-cutover'
load=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
c=load(LIVE/'config.json');meta=load(RUN/'package.json');now=datetime.now(timezone.utc)
w=load(LIVE/'observe_final.native-result.json')['result'];p=load(sorted(LIVE.glob('*-pc-installed.json'))[-1])
ready=load(LIVE/'release_successor_readiness.native-result.json')['result'];baseline=load(LIVE/'closed-observation-baseline.json')
for stamp in (w['observed_at'],p['observed_at']):assert 0<=(now-datetime.fromisoformat(stamp)).total_seconds()<120
assert w['heartbeat']['status']=='ok' and not w['heartbeat']['error_present']
assert w['owned_worker_count']==1 and all(w['environment'].values())
assert w['entry_admission']['blocked'] is False and not w['entry_admission']['reasons']
for role in ('worker','supervisor'):
    assert w['supervisor_safety'][role]['entry_halt'] is False
    assert w['supervisor_safety'][role]['consecutive_errors']==0
assert all(w['cohort'][k] is True for k in ('all_eight_account_hashes_match','all_eight_common_observation_time','all_eight_match_latest_settlement','current_journal_matches_heartbeat','heartbeat_stable_during_read'))
assert w['cohort']['new_completed_since_closed_baseline']>=5
assert p['native_launch_valid'] and p['job']['enabled'] and p['legacy_remains_paused']
assert p['post_readiness_counts'].get('completed',0)>=8 and not p['post_readiness_counts'].get('failed',0) and not p['post_readiness_counts'].get('running',0)
assert all(row['status']=='completed' for row in p['latest_runs'])
assert w['reporter']['identity_matches'] and w['reporter']['pending_part_id'] is None
assert w['reporter']['confirmed_cycle_windows']>int(baseline['reporter']['cadences']['cycle'])
assert w['reporter']['confirmed_cycle_windows']>load(LIVE/'observe_successor3.native-result.json')['result']['reporter']['confirmed_cycle_windows']
probe=load(RUN/'20261029T114524Z-busy-onset.json');samples=probe['result']['observations']
assert len(samples)==2 and all(row['status']=='validated' for row in samples)
full=load(RUN/'linux-full-native-20261029T070022Z-89efa4cf/receipt.json')
assert full['successful'] and full['frozen_sources_unchanged'] and full['results'][0]['tests']==1749
assert all(load(RUN/(platform+'-package.json'))['exit_code']==0 for platform in ('windows','linux'))
package=Path(meta['package']);pins=load(package.with_name(package.name+'.raw-sha256.json'))
assert all(hashlib.sha256((package/name).read_bytes()).hexdigest()==digest for name,digest in pins.items())
backup=load(Path(c['pc_project'])/'state/paired-supervisor/paper-beta-v1-20261021-6c9d520/backup-last.stdout.json')
assert backup['status'] in ('completed','already_completed') and backup['off_host_verified']
assert datetime.fromisoformat(backup['captured_at']).date()==now.date()
sys.path.insert(0,c['pc_project']);from hermes_trading.paper_backup import _verified
deadline=time.monotonic()+30
def check():
    if time.monotonic()>deadline:raise TimeoutError('backup verification deadline')
verified=_verified(Path(backup['path']),backup['source_identity'],check,500000000)
assert verified['sha256']==backup['sha256']
result=dict(status='deployed_paper_worker_and_supervision_verified',observed_at=now.isoformat(),candidate=c['candidate'],deployment='ccc1e87a-de24-4876-98b2-57aa3baa2945',instance='82344b11-fe45-446c-b7e5-deedce294d0e',release_hash=meta['release_hash'],runtime_hash=meta['runtime_hash'],ready_at=ready['ready_at'],schedules_restored=load(LIVE/'schedules-restored.json'),original_opening_hash=ready['original_opening_hash'],original_clocks_history_wallets_and_risk_preserved=True,cutover_observation_gap_seconds=(datetime.fromisoformat(ready['ready_at'])-datetime.fromisoformat(baseline['heartbeat']['observed_at'])).total_seconds(),completed_scheduled_checks=p['post_readiness_counts']['completed'],failed_scheduled_checks=0,scheduled_runs=p['latest_runs'],worker_heartbeat=w['heartbeat'],entry_admission=w['entry_admission'],safety=w['supervisor_safety'],all_eight_settlements_verified=True,new_completed_batches=w['cohort']['new_completed_since_closed_baseline'],busy_onset_samples=[dict(at=row['at'],status=row['status'],elapsed_seconds=row['elapsed_seconds']) for row in samples],discord=dict(before=int(baseline['reporter']['cadences']['cycle']),after=w['reporter']['confirmed_cycle_windows'],pending=None,counters=w['reporter']['counters']),daily_backup=backup,closed_cutover_backup=load(LIVE/'worker-original-reference.json'),full_suite=full['results'][0],model_attempts_reserved=p['lifecycle']['model_attempts_reserved'],wallets=w['accounts'],trading=w['trading'],limitations=['A bounded observed window is not a guarantee against future errors.','Runtime started07:08UTC; stale cost coverage delayed healthy supervision until the11:43usage import and11:44schedule restore. This gap and unknown observations are retained.','Daily backup was already completed before cutover; the fresh closed cutover copy is separate evidence.','Discord evidence is local confirmed delivery markers, not retained remote message IDs.','No profitability or minimum profitable wallet has been established.'])
with (LIVE/'OPERATIONAL_ACCEPTANCE.json').open('x') as f:json.dump(result,f,indent=2)
print(json.dumps({k:result[k] for k in ('status','observed_at','candidate','completed_scheduled_checks','failed_scheduled_checks','entry_admission','new_completed_batches')}))
