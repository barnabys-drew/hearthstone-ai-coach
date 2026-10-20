"""Phase 6b/6c/6d: advice adherence, quality metrics, and KB nominations.

Everything here runs OFFLINE over the retrieval log (6a `advice` events) and,
when a session directory is provided, the session's own Power.log. Nothing
touches the live turn path.

The adherence score is an honest PROXY, stated as such everywhere it appears:
- advised cards = card names from the game's own entity pool that appear in
  the advice steps' text (so "Whip" only counts if a Blackpaw's Whip actually
  existed in that game);
- executed cards = entity names in PLAY/ATTACK blocks during the advice
  turn's two raw turns (display turn T covers raw 2T-1 and 2T);
- score = |advised ∩ executed| / |advised|.
It measures CARD-SET adherence, not line adherence ("Whip into Vereesa" and
"Whip into face" both count Whip), and it does not side-filter plays. Sparse
human labels (`coach_publish.py --advice-feedback`) calibrate it: the report
prints proxy-vs-human agreement before anyone trusts the proxy.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .raglog import join_games

# Entity descriptors inside PLAY/ATTACK block headers, e.g.
# BLOCK_START BlockType=PLAY Entity=[entityName=Foxy Fraud id=55 ... ]
_BLOCK_RE = re.compile(
    r"BLOCK_START BlockType=(PLAY|ATTACK) Entity=\[entityName=([^\]]+?) id=\d+")
_NAME_RE = re.compile(r"entityName=([^\]]+?) id=\d+")
_TURN_RE = re.compile(r"TAG_CHANGE Entity=GameEntity tag=TURN value=(\d+)")
_CREATE_GAME = "CREATE_GAME"

FOLLOWED_THRESHOLD = 0.5  # proxy score at/above which advice counts as followed
LATENCY_PAIR_WINDOW = 180.0  # seconds an advice may trail its turn marker
TURN_DEADLINE_S = 15.0  # the coaching contract's response budget (SKILL.md)


def game_actions(session_dir: Path) -> dict[int, dict[str, Any]]:
    """Per game_no: {"pool": all entity names seen, "plays": {raw_turn:
    set(names in PLAY/ATTACK blocks)}}. One streaming pass over the session's
    Power logs — no snapshot reconstruction needed for a name-level proxy."""
    from .ragreplay import power_logs

    games: dict[int, dict[str, Any]] = {}
    game_no, raw_turn = 0, 0
    for path in power_logs(session_dir):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            if _CREATE_GAME in line:
                game_no += 1
                raw_turn = 0
                games[game_no] = {"pool": set(), "plays": {}}
            if not game_no:
                continue
            m = _TURN_RE.search(line)
            if m:
                raw_turn = max(raw_turn, int(m.group(1)))
            for name in _NAME_RE.findall(line):
                name = name.strip()
                if name and name not in ("GameEntity", "UNKNOWN ENTITY"):
                    games[game_no]["pool"].add(name)
            m = _BLOCK_RE.search(line)
            if m:
                name = m.group(2).strip()
                if name:
                    games[game_no]["plays"].setdefault(raw_turn, set()).add(name)
    return games


def advised_names(event: dict[str, Any], pool: set[str]) -> set[str]:
    """Card names from the game's real entity pool mentioned in the advice."""
    text = " ".join([*(event.get("steps") or []),
                     event.get("headline") or "",
                     event.get("discover") or "",
                     *(event.get("mulligan_cards") or [])]).lower()
    return {name for name in pool if len(name) >= 4 and name.lower() in text}


def adherence_score(event: dict[str, Any], actions: dict[str, Any]) -> dict[str, Any] | None:
    """Proxy adherence for one advice event, or None when unmeasurable
    (no turn, no advised cards recognized)."""
    turn = event.get("turn")
    if not isinstance(turn, int) or turn < 1:
        return None
    advised = advised_names(event, actions["pool"])
    if not advised:
        return None
    executed: set[str] = set()
    for raw in (2 * turn - 1, 2 * turn):
        executed |= actions["plays"].get(raw, set())
    hit = advised & executed
    return {"advised": sorted(advised), "executed_hits": sorted(hit),
            "score": round(len(hit) / len(advised), 3), "proxy": True}


def _match_ts_index(events: list[dict[str, Any]]) -> dict[tuple, float]:
    """(session, game_no, display_turn) -> earliest match-event ts, for
    latency pairing."""
    index: dict[tuple, float] = {}
    for ev in events:
        if ev.get("ev") != "match" or ev.get("replay"):
            continue
        key = (ev.get("session"), ev.get("game_no"), ev.get("turn"))
        ts = ev.get("ts") or 0
        if ts and (key not in index or ts < index[key]):
            index[key] = ts
    return index


def latency_detail_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-advice latency, tagged with the turn and the experiment arm.

    A single global median is what let a ~30s median hide behind a 15s target
    for weeks: it answered "how slow are we" but never "slow *where*". Keeping
    turn and variant on each row is what makes the gated-vs-full A/B and the
    early/mid/late split fall out for free.
    """
    markers = sorted(
        ((ev.get("ts") or 0, ev.get("turn")) for ev in events
         if ev.get("ev") == "match" and not ev.get("replay") and ev.get("ts")),
    )
    out: list[dict[str, Any]] = []
    for ev in events:
        if ev.get("ev") != "advice" or not ev.get("ts"):
            continue
        if ev.get("variant") == "jev":
            # Jev answers inside the tracker in ~0.2s; counting it here would
            # pull the coach's median toward Jev's and hide the very latency
            # this report exists to expose. Jev logs its own latency per event.
            continue
        ts, turn = ev["ts"], ev.get("turn")
        best = None
        for mts, mturn in markers:
            if mts > ts:
                break
            if mturn == turn and ts - mts <= LATENCY_PAIR_WINDOW:
                best = ts - mts
        if best is not None:
            out.append({"turn": turn, "secs": round(best, 1),
                        "variant": ev.get("variant") or "(untagged)",
                        "kind": ev.get("kind")})
    return out


def latency_rows(events: list[dict[str, Any]]) -> list[float]:
    """Seconds from a turn's first marker to its advice publish."""
    return [r["secs"] for r in latency_detail_rows(events)]


def _bucket(turn: Any) -> str:
    if not isinstance(turn, int):
        return "?"
    if turn <= 4:
        return "early (1-4)"
    if turn <= 8:
        return "mid (5-8)"
    return "late (9+)"


def _latency_stats(secs: list[float]) -> dict[str, Any]:
    s = sorted(secs)

    def pct(p: float) -> float | None:
        return s[int(p * (len(s) - 1))] if s else None

    over = sum(1 for x in s if x > TURN_DEADLINE_S)
    return {"n": len(s), "median_s": pct(0.5), "p90_s": pct(0.9),
            "over_deadline": f"{over}/{len(s)}" if s else "0/0"}


def latency_splits(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Latency broken out by turn bucket and by experiment arm."""
    rows = latency_detail_rows(events)
    by_bucket, by_variant = {}, {}
    for r in rows:
        by_bucket.setdefault(_bucket(r["turn"]), []).append(r["secs"])
        by_variant.setdefault(r["variant"], []).append(r["secs"])
    order = {"early (1-4)": 0, "mid (5-8)": 1, "late (9+)": 2, "?": 3}
    return {
        "by_bucket": [dict(bucket=k, **_latency_stats(v))
                      for k, v in sorted(by_bucket.items(),
                                         key=lambda kv: order.get(kv[0], 9))],
        "by_variant": [dict(variant=k, **_latency_stats(v))
                       for k, v in sorted(by_variant.items())],
    }


def contradictions(advice_events: list[dict[str, Any]],
                   pool: set[str] | None = None) -> int:
    """Same-turn advice pairs whose card sets conflict (neither a subset of
    the other) — re-advice that flips the plan. Discover merges are exempt:
    they intentionally extend the standing plan."""
    by_turn: dict[int, list[set[str]]] = {}
    for ev in advice_events:
        if ev.get("kind") not in ("turn", "lethal"):
            continue
        turn = ev.get("turn")
        if not isinstance(turn, int):
            continue
        names = (advised_names(ev, pool) if pool
                 else {s.lower() for s in ev.get("steps") or []})
        if names:
            by_turn.setdefault(turn, []).append(names)
    count = 0
    for sets in by_turn.values():
        for i, a in enumerate(sets):
            for b in sets[i + 1:]:
                if not (a <= b or b <= a):
                    count += 1
    return count


def coach_report(events: list[dict[str, Any]],
                 session_dir: Path | None = None) -> dict[str, Any]:
    """The 6c scorecard (and 6d nominations when a session is provided)."""
    games = join_games(events)
    real = {k: g for k, g in games.items() if k != ("", -1)}
    actions_by_game = game_actions(session_dir) if session_dir else {}

    volume = []
    followed_results: list[tuple[bool, str | None]] = []
    nominations = []
    feedback_pairs = []
    for (session, game_no), g in sorted(real.items()):
        advice = g["advice_events"]
        if not advice:
            continue
        models = sorted({ev.get("model") for ev in advice if ev.get("model")})
        kinds: dict[str, int] = {}
        for ev in advice:
            kinds[ev.get("kind") or "?"] = kinds.get(ev.get("kind") or "?", 0) + 1
        row = {"game": game_no, "result": g["result"] or "?",
               "advice": len(advice),
               "kinds": " ".join(f"{k}:{n}" for k, n in sorted(kinds.items())),
               "models": " ".join(models) or "(untagged)"}
        # Adherence only for games from the session dir actually provided —
        # game_no alone is not unique across sessions in the telemetry log.
        actions = (actions_by_game.get(game_no)
                   if session_dir and session == session_dir.name else None)
        if actions:
            scores = []
            for ev in advice:
                adh = adherence_score(ev, actions)
                if adh is None:
                    continue
                scores.append(adh["score"])
                followed = adh["score"] >= FOLLOWED_THRESHOLD
                followed_results.append((followed, g["result"]))
                for fb in g["advice_feedback"]:
                    if fb.get("turn") == ev.get("turn"):
                        feedback_pairs.append((followed, bool(fb.get("followed"))))
                if not followed and g["result"] == "WON":
                    # 6d: the user overrode the coach and won — their line is
                    # lesson material. Nomination only; prose is the coach's.
                    nominations.append({
                        "game": game_no, "turn": ev.get("turn"),
                        "advised": " ".join(adh["advised"]),
                        "user_played_instead": " ".join(sorted(
                            set(actions["plays"].get(2 * ev["turn"] - 1, set())
                                | actions["plays"].get(2 * ev["turn"], set()))
                            - set(adh["advised"]))[:6]),
                    })
            if scores:
                row["adherence_avg"] = round(sum(scores) / len(scores), 2)
                row["measured"] = len(scores)
        row["contradictions"] = contradictions(
            advice, actions["pool"] if actions else None)
        volume.append(row)

    latencies = sorted(latency_rows(events))

    def pct(p: float) -> float | None:
        return latencies[int(p * (len(latencies) - 1))] if latencies else None

    followed_won = sum(1 for f, r in followed_results if f and r == "WON")
    followed_n = sum(1 for f, _ in followed_results if f)
    overrode_won = sum(1 for f, r in followed_results if not f and r == "WON")
    overrode_n = sum(1 for f, _ in followed_results if not f)
    agree = sum(1 for a, b in feedback_pairs if a == b)

    splits = latency_splits(events)
    return {
        "games": volume,
        "latency": {"paired": len(latencies), "median_s": pct(0.5),
                    "p90_s": pct(0.9)},
        "latency_by_bucket": splits["by_bucket"],
        "latency_by_variant": splits["by_variant"],
        "outcomes": {
            "followed": followed_n, "followed_won": followed_won,
            "overrode": overrode_n, "overrode_won": overrode_won,
        } if followed_results else None,
        "calibration": {"labels": len(feedback_pairs), "proxy_agrees": agree}
        if feedback_pairs else None,
        "nominations": nominations,
        "unjoined_advice": len(games.get(("", -1), {}).get("advice_events", [])),
    }
