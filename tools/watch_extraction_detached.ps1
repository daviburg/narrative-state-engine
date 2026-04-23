param(
    [string]$PidFile = "",
    [string]$LogsDir = "run-logs",
    [int]$Tail = 80,
    [switch]$Follow
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
$stem = [System.IO.Path]::GetFileNameWithoutExtension($PidFile)
$logPath = Join-Path (Split-Path -Parent $PidFile) ("$stem.log")
$errPath = Join-Path (Split-Path -Parent $PidFile) ("$stem.err.log")

$proc = Get-Process -Id $runPid -ErrorAction SilentlyContinue
$state = if ($proc) { "running" } else { "not-running" }

Write-Host "Extraction run status"
Write-Host "  PID:      $runPid"
Write-Host "  State:    $state"
Write-Host "  PID file: $PidFile"
Write-Host "  Log:      $logPath"
Write-Host "  Err log:  $errPath"

if (-not (Test-Path -Path $logPath)) {
    Write-Host "`nstdout log not found yet: $logPath"
} elseif ($Follow) {
    Write-Host "`n--- stdout (tail $Tail, follow) ---"
    Get-Content -Path $logPath -Tail $Tail -Wait
} else {
    Write-Host "`n--- stdout (tail $Tail) ---"
    Get-Content -Path $logPath -Tail $Tail
}

if (Test-Path -Path $errPath) {
    Write-Host "`n--- stderr (tail $Tail) ---"
    Get-Content -Path $errPath -Tail $Tail
}
