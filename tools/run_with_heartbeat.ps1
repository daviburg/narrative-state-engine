<#
.SYNOPSIS
    Heartbeat wrapper for long-running commands.

.DESCRIPTION
    Executes a command and prints a '.' to stderr every 500ms to keep terminal
    idle detection alive. When the command finishes, heartbeat stops and the
    terminal goes idle, triggering automatic completion notification.

.PARAMETER Command
    The command string to execute.

.EXAMPLE
    .\run_with_heartbeat.ps1 -Command "python bootstrap_session.py --all"
    .\run_with_heartbeat.ps1 -Command "ssh arclight 'cd /path && python extract.py'"
#>
param(
    [Parameter(Mandatory=$true)]
    [string]$Command
)

$ErrorActionPreference = "Stop"

# Start the command as a job
$job = Start-Job -ScriptBlock {
    param($cmd)
    Invoke-Expression $cmd
} -ArgumentList $Command

# Print heartbeat while job is running
try {
    while ($job.State -eq 'Running') {
        [Console]::Error.Write(".")
        Start-Sleep -Milliseconds 500
    }
}
finally {
    # Ensure job is cleaned up on Ctrl+C
    if ($job.State -eq 'Running') {
        Stop-Job -Job $job
    }
}

# Collect output and display it
$output = Receive-Job -Job $job
if ($output) {
    [Console]::Error.WriteLine("")
    $output | ForEach-Object { Write-Host $_ }
}

# Get exit code from job
$exitCode = 0
if ($job.State -eq 'Failed') {
    $exitCode = 1
    $jobError = $job.ChildJobs[0].JobStateInfo.Reason
    if ($jobError) {
        Write-Host ""
        Write-Host "Error: $jobError" -ForegroundColor Red
    }
}

Remove-Job -Job $job -Force

Write-Host ""
Write-Host "Command exited with code: $exitCode"
exit $exitCode
