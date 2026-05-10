#!/usr/bin/env bash
# Heartbeat wrapper for long-running commands.
#
# Prints '.' every 500ms to keep terminal idle detection alive.
# When the wrapped command finishes, heartbeat stops and the terminal
# goes idle, triggering automatic completion notification.
#
# Usage:
#   ./run_with_heartbeat.sh python bootstrap_session.py --all
#   ./run_with_heartbeat.sh ssh arclight "cd /path && python extract.py"

set -e

if [ $# -eq 0 ]; then
    echo "Usage: $0 <command> [args...]" >&2
    exit 1
fi

# Launch command in background
"$@" &
PID=$!

# Kill child on Ctrl+C
trap 'kill $PID 2>/dev/null; wait $PID 2>/dev/null; exit 130' INT TERM

# Print heartbeat while child is running
while kill -0 "$PID" 2>/dev/null; do
    printf "."
    sleep 0.5
done

# Collect exit code
wait $PID
EXIT_CODE=$?

echo ""
echo "Command exited with code: $EXIT_CODE"
exit $EXIT_CODE
