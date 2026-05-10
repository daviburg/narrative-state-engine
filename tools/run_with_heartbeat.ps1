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

# Parse command and arguments
$parts = $Command -split ' ', 2
$exe = $parts[0]
$arguments = if ($parts.Length -gt 1) { $parts[1] } else { $null }

# Start process directly (not via job/Invoke-Expression)
$procArgs = @{
    FilePath    = $exe
    NoNewWindow = $true
    PassThru    = $true
}
if ($arguments) { $procArgs.ArgumentList = $arguments }
$proc = Start-Process @procArgs
$null = $proc.Handle   # Pin native handle so ExitCode is available after exit

# Heartbeat while process is running
try {
    while (-not $proc.HasExited) {
        [Console]::Error.Write(".")
        Start-Sleep -Milliseconds 500
    }
}
finally {
    if (-not $proc.HasExited) {
        Stop-Process -Id $proc.Id -Force
    }
}

$proc.WaitForExit()
$exitCode = $proc.ExitCode

[Console]::Error.WriteLine("")
[Console]::Error.WriteLine("Command exited with code: $exitCode")
exit $exitCode
