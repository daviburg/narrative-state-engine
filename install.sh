#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Installs NseCoordinator as a Windows service using NSSM.

.DESCRIPTION
    Registers `python -m saas.orchestrator run` as a Windows service that
    auto-starts on boot, rotates logs at 10 MB, and restarts after a 10-second
    delay on failure.

.PARAMETER PythonPath
    Full path to the Python executable (e.g. .venv\Scripts\python.exe).
    Defaults to the venv in the repo root, then falls back to system python.

.PARAMETER AppDirectory
    Root directory of the narrative-state-engine-private repo.
    Defaults to the repo root derived from this script's location.

.PARAMETER LogPath
    Directory for NSSM stdout/stderr logs.
    Defaults to <AppDirectory>\saas\orchestrator\logs.

.PARAMETER EnvFile
    Path to the .env file whose KEY=VALUE lines are injected via
    AppEnvironmentExtra. Defaults to deploy\windows\coordinator.env.

.EXAMPLE
    .\install-service.ps1
    .\install-service.ps1 -PythonPath C:\Python312\python.exe -LogPath D:\logs\nse
#>
[CmdletBinding()]
param(
    [string]$PythonPath   = "",
    [string]$AppDirectory = "",
    [string]$LogPath      = "",
    [string]$EnvFile      = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ServiceName = "NseCoordinator"
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot    = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path

# --- Resolve AppDirectory ---
if (-not $AppDirectory) {
    $AppDirectory = $RepoRoot
}

# --- Resolve PythonPath ---
if (-not $PythonPath) {
    $candidates = @(
        (Join-Path $AppDirectory ".venv\Scripts\python.exe"),
        (Join-Path $AppDirectory "venv\Scripts\python.exe")
    )
    $systemPython = Get-Command python -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty Source
    if ($systemPython) { $candidates += $systemPython }

    foreach ($c in $candidates) {
        if ($c -and (Test-Path $c)) {
            $PythonPath = $c
            break
        }
    }
    if (-not $PythonPath) {
        Write-Error "Cannot locate python.exe. Use -PythonPath to specify it explicitly."
    }
}

# --- Resolve LogPath ---
if (-not $LogPath) {
    $LogPath = Join-Path $AppDirectory "saas\orchestrator\logs"
}
if (-not (Test-Path $LogPath)) {
    New-Item -ItemType Directory -Path $LogPath | Out-Null
    Write-Host "Created log directory: $LogPath"
}

# --- Resolve EnvFile ---
if (-not $EnvFile) {
    $EnvFile = Join-Path $AppDirectory "deploy\windows\coordinator.env"
}

# --- Check NSSM ---
$nssm = Get-Command nssm -ErrorAction SilentlyContinue
if (-not $nssm) {
    Write-Host ""
    Write-Host "NSSM not found. Install it first:" -ForegroundColor Yellow
    Write-Host "  choco install nssm      (requires Chocolatey)" -ForegroundColor Yellow
    Write-Host "  or download manually from https://nssm.cc/download" -ForegroundColor Yellow
    Write-Host "  and place nssm.exe somewhere on your PATH." -ForegroundColor Yellow
    Write-Host ""
    exit 1
}
$nssmExe = $nssm.Source
Write-Host "Using NSSM: $nssmExe"

# --- Remove existing service if present ---
& sc.exe query $ServiceName 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "Service '$ServiceName' already exists — removing it first"
    & $nssmExe stop   $ServiceName confirm 2>$null | Out-Null
    & $nssmExe remove $ServiceName confirm | Out-Null
}

# --- Install ---
Write-Host "Installing service '$ServiceName'..."
& $nssmExe install $ServiceName $PythonPath -m saas.orchestrator run
if ($LASTEXITCODE -ne 0) { Write-Error "nssm install failed (exit $LASTEXITCODE)." }

& $nssmExe set $ServiceName AppDirectory   $AppDirectory
& $nssmExe set $ServiceName AppStdout      (Join-Path $LogPath "coordinator-stdout.log")
& $nssmExe set $ServiceName AppStderr      (Join-Path $LogPath "coordinator-stderr.log")
& $nssmExe set $ServiceName AppRotateFiles 1
& $nssmExe set $ServiceName AppRotateBytesHigh 0
& $nssmExe set $ServiceName AppRotateBytes 10485760
& $nssmExe set $ServiceName AppThrottle    10000
& $nssmExe set $ServiceName Start          SERVICE_AUTO_START
& $nssmExe set $ServiceName DisplayName    "NSE Orchestrator Coordinator"
& $nssmExe set $ServiceName Description    "Narrative State Engine orchestrator coordinator service"

# --- Environment variables from .env file ---
if (Test-Path $EnvFile) {
    Write-Host "Loading environment from: $EnvFile"
    $envPairs = Get-Content $EnvFile |
        Where-Object { $_ -notmatch '^\s*#' -and $_ -match '=' } |
        ForEach-Object { $_.Trim() }
    if ($envPairs) {
        # NSSM AppEnvironmentExtra accepts newline-separated KEY=VALUE pairs
        $extraEnv = $envPairs -join "`n"
        & $nssmExe set $ServiceName AppEnvironmentExtra $extraEnv
    }
} else {
    Write-Host ""
    Write-Host "No .env file found at '$EnvFile'." -ForegroundColor Yellow
    Write-Host "Copy deploy\windows\coordinator.env.example to deploy\windows\coordinator.env" -ForegroundColor Yellow
    Write-Host "and fill in your credentials, then re-run this script." -ForegroundColor Yellow
}

# Start the service
Write-Host "Starting service..."
& $nssmExe start $ServiceName
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Service installed but failed to start. Check logs at: $LogPath"
} else {
    Start-Sleep -Seconds 3
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    Write-Host "Service status: $($svc.Status)" -ForegroundColor $(if ($svc.Status -eq 'Running') { 'Green' } else { 'Yellow' })
}

Write-Host ""
Write-Host "Service '$ServiceName' installed successfully." -ForegroundColor Green
Write-Host "  Python:  $PythonPath"
Write-Host "  WorkDir: $AppDirectory"
Write-Host "  Logs:    $LogPath"
