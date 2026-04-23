param(
    [string]$Session = "sessions/session-import",
    [string]$TranscriptFile = "",
    [int]$SegmentSize = 100,
    [string]$Model = "",
    [string]$LogsDir = "run-logs",
    [switch]$Overwrite
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "python was not found on PATH."
}

if (-not (Test-Path -Path "tools/bootstrap_session.py")) {
    throw "Run this script from the repository root (tools/bootstrap_session.py not found)."
}

New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logPath = Join-Path $LogsDir "extract-$timestamp.log"
$errPath = Join-Path $LogsDir "extract-$timestamp.err.log"
$pidPath = Join-Path $LogsDir "extract-$timestamp.pid"
$cmdPath = Join-Path $LogsDir "extract-$timestamp.cmd.txt"

$args = @("tools/bootstrap_session.py", "--session", $Session)

if (-not $TranscriptFile) {
    throw "-TranscriptFile is required (path to full transcript import file)."
}

if (-not (Test-Path -Path $TranscriptFile)) {
    throw "Transcript file not found: $TranscriptFile"
}

$args += @("--file", $TranscriptFile)

if ($SegmentSize -gt 0) {
    $args += @("--segment-size", $SegmentSize)
}

if ($Model) {
    $args += @("--model", $Model)
}

if ($Overwrite) {
    $args += "--overwrite"
}

$process = Start-Process -FilePath "python" -ArgumentList $args -RedirectStandardOutput $logPath -RedirectStandardError $errPath -PassThru

$process.Id | Set-Content -Path $pidPath

function Format-QuotedArg {
    param([string]$Value)
    return "'" + $Value.Replace("'", "''") + "'"
}

$argString = ($args | ForEach-Object { Format-QuotedArg $_ }) -join " "
@(
    "python $argString",
    "",
    "Monitor/status:",
    "  powershell -ExecutionPolicy Bypass -File tools/watch_extraction_detached.ps1 -PidFile '$pidPath'",
    "  powershell -ExecutionPolicy Bypass -File tools/watch_extraction_detached.ps1 -PidFile '$pidPath' -Follow",
    "",
    "Stop process:",
    "  powershell -ExecutionPolicy Bypass -File tools/stop_extraction_detached.ps1 -PidFile '$pidPath'"
) | Set-Content -Path $cmdPath

Write-Host "Detached extraction launched."
Write-Host "  PID:      $($process.Id)"
Write-Host "  Log:      $logPath"
Write-Host "  Err log:  $errPath"
Write-Host "  PID file: $pidPath"
Write-Host "  Cmd file: $cmdPath"
