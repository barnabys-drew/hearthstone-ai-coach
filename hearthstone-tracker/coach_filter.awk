# Live-coach event filter: pass ONLY the lines the coach must react to,
# so each notification is an actionable decision point and the model isn't
# queued behind opponent-turn chatter.
#
# Emits:
#   - == MULLIGAN block (with indented detail lines)
#   - == TURN blocks ONLY for "your turn" (with detail lines)
#   - == EXTRA TURN blocks
#   - == DISCOVER PENDING lines (options are inline)
#   - == JEV MULLIGAN / == JEV DISCOVER lines (Jev already answered; HS_JEV=1)
#   - == UPDATE lines ONLY when cards were added to MY hand mid-turn
#     (discover results / generated cards — e.g. a bomb discounted to 0),
#     or when board minion stats changed DURING MY TURN (so the coach sees
#     post-spell values instead of doing math on turn-start numbers)
#   - == GAME OVER
#   - !! stale-feed warnings, Tracebacks, Errors
# Everything else (opponent-turn updates, hp ticks, play-history lines) is
# dropped: the your-turn block always carries the full current state anyway.

/^== TURN/      { inblk = ($0 ~ /your turn/); myturn = inblk; if (inblk) { print; fflush() } next }
/^== MULLIGAN/  { inblk = 1; print; fflush(); next }
/^== EXTRA TURN/ { inblk = 1; print; fflush(); next }
/^== DISCOVER PENDING/ { print; fflush(); next }
/^== JEV /      { print; fflush(); next }
/^== GAME OVER/ { inblk = 0; print; fflush(); next }
/^== UPDATE/    { inblk = 0; if ($0 ~ /my hand \+/ || (myturn && $0 ~ /board: /)) { print; fflush() } next }
/^==/           { inblk = 0; next }
/^!!/           { print; fflush(); next }
/Traceback|Error/ { print; fflush(); next }
/^   /          { if (inblk) { print; fflush() } next }
