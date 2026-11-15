<#
.SYNOPSIS
Run a bounded eight-account paper pilot from Windows PowerShell or PowerShell 7.
.DESCRIPTION
Wraps the existing Python pilot without changing its strategies or safeguards.
Use -Install for the initial locked uv installation. ReplayArchive must refer
to a prepared public archive; its provenance is validated by the Python CLI.
#>
[CmdletBinding()]
param(
    [string]$Root,
    [string]$ExperimentId,
    [string]$ReplayArchive,
    [ValidateRange(1, 500)][int]$MaxCycles = 1,
    [ValidateRange(1, 604800)][int]$MaxRuntimeSeconds = 120,
    [ValidateRange(1, 86400)][int]$IntervalSeconds = 60,
    [switch]$Install
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($ExperimentId)) {
    $ExperimentId = 'pilot-' + [Guid]::NewGuid().ToString('N')
}
if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = Join-Path $projectRoot ('.test-tmp-pilot-' + [Guid]::NewGuid().ToString('N'))
} elseif (-not [IO.Path]::IsPathRooted($Root)) {
    $Root = Join-Path $projectRoot $Root
}
$Root = [IO.Path]::GetFullPath($Root)
if (Test-Path -LiteralPath $Root) {
    throw 'The pilot requires a fresh root. Choose a new .test-tmp* directory.'
}
if (-not (($Root -split '[\\/]') | Where-Object { $_.StartsWith('.test-tmp') })) {
    throw 'The pilot root must be inside a .test-tmp* directory.'
}
if ($ReplayArchive) {
    if (-not (Test-Path -LiteralPath $ReplayArchive -PathType Leaf)) {
        throw 'The replay archive does not exist. Prepare the public proof archive first.'
    }
    $ReplayArchive = (Resolve-Path -LiteralPath $ReplayArchive).ProviderPath
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'uv is required. Install uv before running this launcher.'
}

function Invoke-PilotUv {
    param([string[]]$CommandArguments)
    & uv @CommandArguments
    if ($LASTEXITCODE -ne 0) {
        throw "uv failed with exit code $LASTEXITCODE. Pilot execution stopped."
    }
}

Push-Location -LiteralPath $projectRoot
try {
    if ($Install) {
        Invoke-PilotUv -CommandArguments @('sync', '--python', '3.11', '--locked')
    }
    $common = @('run', '--no-sync', '--python', '3.11', 'python', '-m',
                'hermes_trading.pilot_paper', '--root', $Root,
                '--experiment-id', $ExperimentId)
    if ($ReplayArchive) {
        $common += @('--replay-archive', $ReplayArchive)
    }
    Invoke-PilotUv -CommandArguments ($common + @('--init-only'))
    Invoke-PilotUv -CommandArguments ($common + @('--run',
        '--max-cycles', [string]$MaxCycles,
        '--max-runtime-seconds', [string]$MaxRuntimeSeconds,
        '--interval-seconds', [string]$IntervalSeconds))
    Write-Host "Session finished. Inspect the JSON result and $Root\pilot_session.json."
    Write-Host 'A session with zero trades is inconclusive; process success alone is not trading evidence.'
} finally {
    Pop-Location
}
