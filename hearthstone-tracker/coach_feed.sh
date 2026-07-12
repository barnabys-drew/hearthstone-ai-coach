#!/usr/bin/env bash
# Start (or cleanly restart) the SINGLE live-coach feed process.
#
# Guarantees, in order:
#   1. No other `hstracker live` process (owned by this user) survives — kills
#      zombies from any previous session; detached children outlive their
#      shell wrappers.
#   2. The feed writes to a FRESH per-run log file in append mode, so no
#      truncation races with a tail -F reader are possible. Logs live under a
#      per-user runtime dir (never world-writable /tmp — no symlink planting).
#   3. The feed is verified alive after startup; a dead-on-arrival feed
#      fails loudly with its output instead of silently doing nothing.
#
# Usage: ./coach_feed.sh [log-file]
# Prints FEED_PID=<pid>, LOG=<path> and CURRENT=<symlink> on success.
set -euo pipefail
cd "$(dirname "$0")"

RUN_DIR="${XDG_RUNTIME_DIR:-$HOME/.cache}/hstracker"
mkdir -p "$RUN_DIR"

# 1. Kill every existing feed owned by this user and verify zero remain.
FEED_PATTERN="hstracker live"
pkill -u "$USER" -f "$FEED_PATTERN" 2>/dev/null || true
for _ in 1 2 3 4 5 6 7 8 9 10; do
    pgrep -u "$USER" -f "$FEED_PATTERN" >/dev/null || break
    sleep 0.3
    pkill -9 -u "$USER" -f "$FEED_PATTERN" 2>/dev/null || true
done
if pgrep -u "$USER" -f "$FEED_PATTERN" >/dev/null; then
    echo "ERROR: could not kill existing hstracker live process(es):" >&2
    pgrep -u "$USER" -fa "$FEED_PATTERN" >&2
    exit 1
fi

# 2. Fresh log file, append-only writer. A stable symlink always points at
#    the CURRENT run's log so watchers (tail --follow=name) survive feed
#    restarts without being re-pointed at a new path.
LOG="${1:-$RUN_DIR/coach_$(date +%Y%m%d_%H%M%S).log}"
CURRENT_LINK="$RUN_DIR/coach_current.log"
if [ -e "$LOG" ] && [ ! -O "$LOG" ]; then
    echo "ERROR: refusing to write to $LOG — exists and is not owned by $USER" >&2
    exit 1
fi
: > "$LOG"
ln -sfn "$LOG" "$CURRENT_LINK"
PYTHONUNBUFFERED=1 nohup ./hst live >> "$LOG" 2>&1 &
FEED_PID=$!

# 3. Verify it survived startup and found the Hearthstone log directory.
sleep 2
if ! kill -0 "$FEED_PID" 2>/dev/null; then
    echo "ERROR: feed died at startup. Output:" >&2
    cat "$LOG" >&2
    exit 1
fi
if ! grep -q "^watching " "$LOG"; then
    echo "WARNING: feed running but no 'watching <dir>' line yet — is Hearthstone running?" >&2
fi

echo "FEED_PID=$FEED_PID"
echo "LOG=$LOG"
echo "CURRENT=$CURRENT_LINK"
