"""Audit exact case coverage without rewriting the failed original full result."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

RUN = Path(__file__).resolve().parent
VERIFY = RUN/'verification'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def case_id(label):
    match = re.fullmatch(r'\w+ \(([\w.]+)\)',label)
    if not match:raise ValueError('unexpected unittest case label')
    return match[1]


if __name__=='__main__':
    output = RUN/'composed-verification.json'
    assert not output.exists(),'preserve original verification artifacts'
    full = read(VERIFY/'full-frozen-windows-v1.result.json')
    catalog = read(VERIFY/'full-frozen-windows-v1.catalog.json')
    outcomes = read(VERIFY/'full-frozen-windows-v1.outcomes.json')
    assert len(catalog)==len(set(catalog))==full['catalog_cases']==full['tests_run']==1712
    assert set(catalog)==set(outcomes) and not full['unrun_cases'] and not full['changed_sources']
    assert not full['failures'] and full['status']=='failed'
    assert sha(VERIFY/'full-frozen-windows-v1.log')==full['log_sha256']
    passed = {key for key,status in outcomes.items() if status=='passed'}
    errors = {key for key,status in outcomes.items() if status=='error'}
    skipped = {key for key,status in outcomes.items() if status=='skipped'}
    assert (len(passed),len(errors),len(skipped))==(1698,9,5)
    assert {case_id(label) for label,_ in full['errors']}==errors
    assert {case_id(label) for label,_ in full['skipped']}==skipped
    for _,trace in full['errors']:
        assert 'FileNotFoundError' in trace
        assert any(group in trace for group in ('approved-cost-reserve-v1','current-venue-execution-v1'))
    recovered = read(VERIFY/'restored-fixtures-windows-v1.result.json')
    recovered_catalog = read(VERIFY/'restored-fixtures-windows-v1.catalog.json')
    recovered_outcomes = read(VERIFY/'restored-fixtures-windows-v1.outcomes.json')
    assert recovered['tests_run']==recovered['passed']==len(recovered_catalog)==11
    assert set(recovered_catalog)==set(recovered_outcomes)
    assert all(status=='passed' for status in recovered_outcomes.values())
    assert not any(recovered[key] for key in ('errors','failures','skipped','unrun_cases','resource_warnings','changed_sources'))
    assert sha(VERIFY/'restored-fixtures-windows-v1.log')==recovered['log_sha256']
    recovered_ids = set(recovered_catalog)
    assert errors<=recovered_ids and len(recovered_ids & passed)==2
    linux = read(RUN/'linux-verification-v5.json')
    assert linux['passed'] and linux['exit_code']==0 and not linux['changed_package_files']
    assert linux['structured_stdout'][-1]==dict(tests=16,skipped=0,failures=0,errors=0)
    assert linux['fifo_subprocess_timeout_seconds']==2
    linux_log = RUN/'linux-tests-v5.log'
    assert sha(linux_log)==linux['log_sha256']
    linux_ids = re.findall(r'^\w+ \(([\w.]+)\) \.\.\.',linux_log.read_text(encoding='utf-8'),re.MULTILINE)
    assert len(linux_ids)==len(set(linux_ids))==16
    assert set(linux_ids)==recovered_ids|skipped
    exact = read(RUN/'exact-package-verification-v4.json')
    assert exact['passed'] and exact['tests_passed']==20 and not exact['changed_sources']
    assert sha(RUN/'exact-package-tests-v4.log')==exact['log_sha256']
    metadata = read(RUN/'candidate-package-verification-v4.json')
    package = Path(metadata['export'])
    raw = read(package.with_name(package.name+'.raw-sha256.json'))
    old_metadata = read(RUN/'candidate-package-verification-v3.json')
    old_package = Path(old_metadata['export'])
    old_raw = read(old_package.with_name(old_package.name+'.raw-sha256.json'))
    assert len(raw)==667 and len(old_raw)==608 and set(old_raw)<=set(raw)
    assert all(raw[name]==digest and sha(old_package/name)==digest for name,digest in old_raw.items())
    assert all(sha(package/name)==digest for name,digest in raw.items())
    assert read(VERIFY/'restored-fixtures-windows-v1.source-sha256.json')==raw
    full_pins = read(VERIFY/'full-frozen-windows-v1.source-sha256.json')
    assert len(full_pins)==376 and all(raw[name]==digest for name,digest in full_pins.items())
    fixture = read(RUN/'historical-fixture-inventory.json')['files']
    assert len(fixture)==57 and sum(row['bytes'] for row in fixture.values())==67167
    assert all(raw[name]==row['sha256'] for name,row in fixture.items())
    assert set(raw)-set(old_raw)==set(fixture)|{'.gitattributes','research/runs/paper-supervisor-retry-v1/historical-fixture-inventory.json'}
    cleanup = read(RUN/'missing-fixture-cleanup-negative-control.json')
    observations = cleanup['result']['observations']
    assert observations[0]['resource_warnings']==1 and observations[0]['matches_owned_temp_cleanup']
    assert observations[1]['resource_warnings']==0
    assert all(row['owned_directory_removed'] for row in observations)
    current_windows = passed|recovered_ids
    assert len(current_windows)==1707 and current_windows|set(linux_ids)==set(catalog)
    evidence = {}
    for key in sorted(catalog):
        if key in errors:proof='restored_fixtures_windows'
        elif key in skipped:proof='platform_case_linux'
        else:proof='original_full_windows'
        evidence[key]=dict(status='passed',proof=proof)
    value = dict(checked_at=datetime.now(timezone.utc).isoformat(),candidate_commit=metadata['candidate_commit'],
        scope='composed local correctness evidence; not one fresh passing full-suite run',
        catalog_cases=1712,distinct_windows_passes=1707,windows_platform_skips=5,
        windows_skips_passed_on_linux=5,catalog_cases_with_pass_evidence=len(evidence),
        uncovered_cases=[],original_full_suite_pass=False,original_full_result_preserved=True,
        original_full_counts=dict(passed=1698,errors=9,skipped=5,unrun=0),
        original_full_resource_warnings=full['resource_warnings'],
        missing_fixture_cleanup_warning_reproduced_in_isolation=True,
        historical_warning_object_identity_proven=False,restored_fixture_resource_warnings=[],
        recovered_windows_cases=11,linux_selected_cases=16,final_exact_package_windows_cases=20,
        frozen_package_files=667,prior608_original_raw_files_unchanged=True,
        historical_fixture_files=57,historical_fixture_bytes=67167,program_bytes_unchanged=True,
        runtime_files=129,runtime_delta_from_deployed=['supervisor_sync.py'],
        new_release_hash=metadata['new_release_hash'],new_runtime_map_hash=metadata['new_runtime_map_hash'],
        watcher_native_unchanged=True,deadline_or_risk_limits_relaxed=False,
        linux_v4_failed_result_preserved=True,linux_v5_child_import_path_corrected=True,
        deployed=False,actual_pc_runtime_installed=False,entry_halt_cleared=False,
        paid_calls=0,market_backtest=False,training=False,profitability_qualified=False)
    (RUN/'composed-case-evidence.json').write_text(json.dumps(evidence,indent=2)+'\n',encoding='utf-8')
    output.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(value,indent=2))
