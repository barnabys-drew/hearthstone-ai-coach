#!/usr/bin/env bash
# The one-line coach watcher: follow the CURRENT feed log through the
# coach_filter, surviving feed restarts.
#
# `tail --follow=name` follows the coach_current.log symlink BY NAME, so when
# coach_feed.sh retargets it at a new per-run log, the watcher reopens it
# automatically — no stale monitors after a feed restart. A liveness watchdog
# shouts if the feed process itself dies, so silence never masquerades as a
# quiet game. Files live in a per-user runtime dir, not /tmp.
#
# Usage: ./coach_watch.sh            (agent: run this under a Monitor)
set -euo pipefail
cd "$(dirname "$0")"

RUN_DIR="${XDG_RUNTIME_DIR:-$HOME/.cache}/hstracker"
CURRENT_LINK="$RUN_DIR/coach_current.log"
FEED_PATTERN="hstracker live"

if [ ! -e "$CURRENT_LINK" ]; then
    echo "!! No feed log at $CURRENT_LINK — run ./coach_feed.sh first" >&2
    exit 1
fi
if [ -L "$CURRENT_LINK" ] && [ ! -O "$(readlink -f "$CURRENT_LINK")" ]; then
    echo "!! Refusing to follow $CURRENT_LINK — target not owned by $USER" >&2
    exit 1
fi

{
    tail -n0 --follow=name --retry "$CURRENT_LINK" &
    TAIL_PID=$!
    trap 'kill "$TAIL_PID" 2>/dev/null' EXIT
    while sleep 30; do
        pgrep -u "$USER" -f "$FEED_PATTERN" >/dev/null || {
            echo "!! FEED PROCESS DIED — coaching is blind until coach_feed.sh is rerun"
            break
        }
    done
} | awk -f ./coach_filter.awk
