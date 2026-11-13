"""Exercise the Windows launcher without a market, model or installed uv."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / 'scripts' / 'run_paper_pilot.ps1'
POWERSHELL = shutil.which('powershell.exe')


def ps_string(value):
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(POWERSHELL, 'Windows PowerShell is required')
class PaperPilotLauncherTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix='.test-tmp-launcher-', dir=ROOT))
        self.addCleanup(shutil.rmtree, self.base)
        self.calls = self.base / 'calls.jsonl'

    def run_launcher(self, options='', failure=''):
        self.assertTrue(LAUNCHER.is_file(), 'PowerShell pilot launcher is missing')
        harness = self.base / 'harness.ps1'
        harness.write_text(f"""
$ErrorActionPreference = 'Stop'
$global:pilotTestCallLog = {ps_string(self.calls)}
$global:pilotTestFailure = {ps_string(failure)}
function uv {{
    $values = @($args | ForEach-Object {{ [string]$_ }})
    ConvertTo-Json -InputObject $values -Compress | Add-Content -LiteralPath $global:pilotTestCallLog -Encoding UTF8
    $global:LASTEXITCODE = 0
    if (($global:pilotTestFailure -eq 'install' -and $values[0] -eq 'sync') -or
        ($global:pilotTestFailure -eq 'bootstrap' -and $values -contains '--init-only') -or
        ($global:pilotTestFailure -eq 'run' -and $values -contains '--run')) {{
        $global:LASTEXITCODE = 23
    }}
}}
try {{
    & {ps_string(LAUNCHER)} {options}
}} catch {{
    Write-Output $_.Exception.Message
    exit 1
}}
""", encoding='utf-8-sig')
        result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive',
                                 '-ExecutionPolicy', 'Bypass', '-File', str(harness)],
                                capture_output=True, text=True, timeout=25)
        calls = ([json.loads(line) for line in self.calls.read_text(encoding='utf-8-sig').splitlines()]
                 if self.calls.exists() else [])
        return result, calls

    def test_default_bootstraps_then_runs_same_unique_session_with_bounds(self):
        result, calls = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(calls), 2)
        self.assertIn('--init-only', calls[0])
        self.assertIn('--run', calls[1])
        for option in ('--root', '--experiment-id'):
            self.assertEqual(calls[0][calls[0].index(option)+1], calls[1][calls[1].index(option)+1])
        self.assertIn('--no-sync', calls[0])
        for option, value in (('--python','3.11'), ('--max-cycles','1'),
                              ('--max-runtime-seconds','120'), ('--interval-seconds','60')):
            self.assertEqual(calls[1][calls[1].index(option)+1], value)

    def test_install_precedes_bootstrap_and_uses_lockfile(self):
        result, calls = self.run_launcher('-Install')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls[0], ['sync','--python','3.11','--locked'])
        self.assertEqual(len(calls), 3)

    def test_replay_with_spaces_is_forwarded_unchanged_to_both_commands(self):
        archive = self.base / 'public proof archive.json'
        archive.write_text('{}')
        result, calls = self.run_launcher(f'-ReplayArchive {ps_string(archive)} -MaxCycles 23 -MaxRuntimeSeconds 604800 -IntervalSeconds 3600')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for call in calls:
            self.assertEqual(call[call.index('--replay-archive')+1], str(archive))
        self.assertEqual(calls[1][calls[1].index('--max-cycles')+1], '23')

    def test_failed_install_never_bootstraps(self):
        result, calls = self.run_launcher('-Install', 'install')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)

    def test_failed_bootstrap_never_runs(self):
        result, calls = self.run_launcher(failure='bootstrap')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)

    def test_failed_run_is_not_reported_as_success(self):
        result, calls = self.run_launcher(failure='run')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 2)

    def test_existing_root_is_refused_before_any_process(self):
        result, calls = self.run_launcher(f'-Root {ps_string(self.base)}')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_missing_replay_archive_is_refused_before_any_process(self):
        result, calls = self.run_launcher(f'-ReplayArchive {ps_string(self.base / "missing.json")}')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])
