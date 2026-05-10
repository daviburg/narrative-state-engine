#!/usr/bin/env python3
"""Heartbeat wrapper for long-running commands.

Prints '.' to stderr every 500ms to keep terminal idle detection alive.
When the wrapped command finishes, heartbeat stops and the terminal
goes idle, triggering automatic completion notification.

Usage:
    python run_with_heartbeat.py <command> [args...]
    python run_with_heartbeat.py python bootstrap_session.py --all
    python run_with_heartbeat.py ssh arclight "cd /path && python extract.py"
"""

import subprocess
import sys
import time


def main():
    if len(sys.argv) < 2:
        print("Usage: python run_with_heartbeat.py <command> [args...]", file=sys.stderr)
        sys.exit(1)

    # Launch command as subprocess directly (no shell)
    proc = subprocess.Popen(
        sys.argv[1:],
        stdout=sys.stdout,
        stderr=sys.stderr,
    )

    # Print heartbeat while process is running
    try:
        while proc.poll() is None:
            sys.stderr.write(".")
            sys.stderr.flush()
            time.sleep(0.5)
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait()
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)

    exit_code = proc.returncode
    print(f"\nCommand exited with code: {exit_code}", file=sys.stderr)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
