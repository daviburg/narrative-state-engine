param(
    [string]$PidFile = "",
    [string]$LogsDir = "run-logs",
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $PidFile) {
    if (-not (Test-Path -Path $LogsDir)) {
        throw "Logs directory not found: $LogsDir. Start a detached run first or pass -PidFile."
    }
    $PidFile = Get-ChildItem -Path $LogsDir -Filter "extract-*.pid" -File |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1 |
        ForEach-Object { $_.FullName }
}

if (-not $PidFile -or -not (Test-Path -Path $PidFile)) {
    throw "No PID file found. Pass -PidFile or ensure run-logs contains extract-*.pid files."
}

$pidText = (Get-Content -Path $PidFile -Raw).Trim()
if (-not $pidText) {
    throw "PID file is empty: $PidFile"
}

$runPid = [int]$pidText
$proc = Get-Process -Id $runPid -ErrorAction SilentlyContinue

if (-not $proc) {
    Write-Host "Process $runPid is already not running."
    Write-Host "PID file: $PidFile"
    exit 0
}

if ($Force) {
    Stop-Process -Id $runPid -Force
} else {
    Stop-Process -Id $runPid
}

Write-Host "Stopped extraction process $runPid"
Write-Host "PID file: $PidFile"
