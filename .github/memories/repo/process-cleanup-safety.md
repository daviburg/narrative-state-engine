# Process Cleanup Safety Rules

## Lesson Learned (2026-05-10)
Killed an extraction-related terminal process during "safe" cleanup, disrupting an active run16 extraction.

## Root Cause
Used a **blocklist** approach: checked for child processes as a point-in-time snapshot and assumed "no children = idle = safe to kill." A process between operations (sleeping, polling, waiting for next batch) appears childless but is still active.

## Rules for Terminal Process Cleanup

1. **Allowlist, not blocklist**: Only kill processes you can positively prove belong to a *finished* session or *closed* window. Never kill processes you merely can't prove are active.
2. **Point-in-time child checks are unreliable**: A process sleeping between polling cycles, between extraction batches, or waiting for network I/O will have zero children but is still needed.
3. **Protect by window, not by process**: If a VS Code window is in-use, ALL its terminals are protected — even idle-looking ones. Map every PID to its window first.
4. **When in doubt, don't kill**: If you can't determine a process's purpose with certainty, leave it alive. Memory savings from killing one questionable process are never worth disrupting active work.
5. **Ask the user**: For terminals associated with active work, ask the user to confirm which specific terminals are safe to close rather than making autonomous kill decisions.
