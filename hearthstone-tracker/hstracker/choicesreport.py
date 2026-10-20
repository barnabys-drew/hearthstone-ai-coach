"""Readouts for the choice decisions: who was right on mulligans and Discovers.

Two questions the logs can now answer, both offline:

- **Mulligans (#20).** Every mulligan advice event carries its keep/toss list
  (`mulligan_cards` + `mulligan_keep`), from the coach and from Jev, with the
  overlay's choices mode. `games.db` holds what the player actually kept. Per
  game: coach vs player, Jev vs player, coach vs Jev, and how games went when
  the player's keeps matched each one exactly. In `both` mode this is the
  live A/B the offline lab (`lab/mulligan-ab/`) could only approximate.
- **Discovers (#19).** Jev and coach Discover picks are logged as text
  ("Pick X — ..."); the session's Power.log records what was actually chosen
  (hslog `Choices` + `ChosenEntities`/`SendChoices`). Per source: how often the
  pick matched, and how games went when it was followed.

Granularity, stated plainly: mulligans are compared per distinct card name
(kept = at least one copy kept), so a duplicate kept once and tossed once
counts as kept. Agreement with the player is not correctness: these tables
say who plays like the player, and the outcome columns are a hint at small n.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Callable

_COPIES_RE = re.compile(r"\s+x\d+$")
_PICK_RE = re.compile(r"^\s*pick\s+(.+?)(?:\s+[—-]\s+|$)", re.I)


def card_key(label: str) -> str:
    """'Slam x2' / 'Slam' -> 'slam' (Jev labels duplicates with a count)."""
    return _COPIES_RE.sub("", (label or "").strip()).lower()


def advice_keeps(ev: dict[str, Any]) -> dict[str, bool] | None:
    """{card: keep} from a mulligan advice event, or None for events that
    predate `mulligan_keep` (no keep/toss recorded)."""
    cards, keeps = ev.get("mulligan_cards") or [], ev.get("mulligan_keep")
    if not cards or not isinstance(keeps, list) or len(keeps) != len(cards):
        return None
    out: dict[str, bool] = {}
    for card, keep in zip(cards, keeps):
        key = card_key(card)
        out[key] = out.get(key, False) or bool(keep)
    return out


def player_keeps(conn: sqlite3.Connection, start_time: str | None,
                 name_of: Callable[[str], str | None]) -> dict[str, bool] | None:
    """{card: kept} for the game that started at `start_time` (the outcome
    event carries it; it is part of games.db's unique key), or None."""
    if not start_time:
        return None
    row = conn.execute("SELECT id FROM games WHERE start_time = ? ORDER BY id DESC LIMIT 1",
                       (start_time,)).fetchone()
    if not row:
        return None
    out: dict[str, bool] = {}
    for card_id, offered, kept in conn.execute(
            "SELECT card_id, mull_offered, mull_kept FROM game_cards "
            "WHERE game_id = ? AND friendly = 1 AND mull_offered > 0", (row[0],)):
        name = name_of(card_id) or card_id
        if name == "The Coin":
            continue
        key = card_key(name)
        out[key] = out.get(key, False) or kept > 0
    return out or None


def agreement(a: dict[str, bool] | None, b: dict[str, bool] | None) -> tuple[int, int] | None:
    """(agreeing cards, shared cards), or None if either side is missing."""
    if not a or not b:
        return None
    shared = set(a) & set(b)
    return (sum(1 for k in shared if a[k] == b[k]), len(shared)) if shared else None


def _fmt(pair: tuple[int, int] | None) -> str:
    return f"{pair[0]}/{pair[1]}" if pair else "—"


def _pct(agree: int, total: int) -> float | None:
    return round(100 * agree / total, 1) if total else None


def mulligan_readout(games: dict[tuple, dict[str, Any]], conn: sqlite3.Connection,
                     name_of: Callable[[str], str | None]) -> dict[str, Any]:
    """Per-game coach / Jev / player mulligan comparison plus totals.

    `games` is raglog.join_games() output; each game's advice events and its
    outcome (result, start_time) are already joined there.
    """
    rows, totals = [], {"coach": [0, 0], "jev": [0, 0], "coach_vs_jev": [0, 0]}
    exact = {"coach": [0, 0], "jev": [0, 0]}  # [games matched exactly, of those won]
    for (session, game_no), g in sorted(games.items(), key=lambda kv: kv[0]):
        if (session, game_no) == ("", -1):
            continue
        mull = [ev for ev in g["advice_events"] if ev.get("kind") == "mulligan"]
        coach_ev = next((ev for ev in reversed(mull) if ev.get("variant") != "jev"
                         and advice_keeps(ev) is not None), None)
        jev_ev = next((ev for ev in reversed(mull) if ev.get("variant") == "jev"
                       and advice_keeps(ev) is not None), None)
        if not coach_ev and not jev_ev:
            continue
        coach = advice_keeps(coach_ev) if coach_ev else None
        jev = advice_keeps(jev_ev) if jev_ev else None
        player = player_keeps(conn, g.get("start_time"), name_of)
        c_p, j_p, c_j = agreement(coach, player), agreement(jev, player), agreement(coach, jev)
        for key, pair in (("coach", c_p), ("jev", j_p), ("coach_vs_jev", c_j)):
            if pair:
                totals[key][0] += pair[0]
                totals[key][1] += pair[1]
        for key, pair in (("coach", c_p), ("jev", j_p)):
            if pair and pair[0] == pair[1]:
                exact[key][0] += 1
                exact[key][1] += g.get("result") == "WON"
        mode = (coach_ev or jev_ev).get("choices_mode") or "?"
        rows.append({"session": (session or "")[-19:], "game": game_no,
                     "result": g.get("result") or "?", "mode": mode,
                     "coach": (coach_ev or {}).get("model") or ("—" if not coach_ev else "?"),
                     "coach_vs_you": _fmt(c_p), "jev_vs_you": _fmt(j_p),
                     "coach_vs_jev": _fmt(c_j)})
    summary = [
        {"source": "coach vs you", "cards": f"{totals['coach'][0]}/{totals['coach'][1]}",
         "agree_pct": _pct(*totals["coach"]),
         "games_matched_exactly": exact["coach"][0], "of_those_won": exact["coach"][1]},
        {"source": "Jev vs you", "cards": f"{totals['jev'][0]}/{totals['jev'][1]}",
         "agree_pct": _pct(*totals["jev"]),
         "games_matched_exactly": exact["jev"][0], "of_those_won": exact["jev"][1]},
        {"source": "coach vs Jev", "cards": f"{totals['coach_vs_jev'][0]}/{totals['coach_vs_jev'][1]}",
         "agree_pct": _pct(*totals["coach_vs_jev"]),
         "games_matched_exactly": None, "of_those_won": None},
    ]
    return {"games": rows, "summary": summary}


# ------------------------------------------------------------------ Discovers

def picked_name(text: str | None, options: list[str]) -> str | None:
    """The option a 'Pick X — reason' line names, matched against the real
    options (exact first, then the longest option named in the text)."""
    if not text:
        return None
    m = _PICK_RE.match(text)
    head = (m.group(1) if m else text).strip().lower()
    by_lower = {o.lower(): o for o in options}
    if head in by_lower:
        return by_lower[head]
    named = [o for o in options if o.lower() in text.lower()]
    return max(named, key=len) if named else None


def extract_discovers(choices: list[Any], chosen: list[Any],
                      name_of_entity: Callable[[int], str | None],
                      friendly_id: int | None,
                      controller_of: Callable[[int], int | None]) -> list[dict[str, Any]]:
    """Friendly Discover choices and what was picked, in log order.

    Pure over hslog-shaped packets: `choices` have .id, .type (GENERAL for a
    Discover), .source (entity id), .choices (entity ids); `chosen` have .id
    and .choices (the entity ids picked).
    """
    picked = {c.id: list(c.choices) for c in chosen}
    out = []
    for c in choices:
        if getattr(c.type, "name", c.type) != "GENERAL":
            continue
        if friendly_id is not None and controller_of(c.source) != friendly_id:
            continue
        options = [n for n in (name_of_entity(e) for e in c.choices) if n]
        pick_ids = picked.get(c.id) or []
        pick = name_of_entity(pick_ids[0]) if pick_ids else None
        if len(options) >= 2 and pick:
            out.append({"source": name_of_entity(c.source), "options": options, "chosen": pick})
    return out


def session_discovers(session_dir: Path, resolver: Any) -> dict[int, list[dict[str, Any]]]:
    """{game_no: [discover choices with the pick]} from a session's Power.log,
    numbering games by CREATE_GAME exactly as `hst live` does."""
    from hearthstone.enums import GameTag
    from hslog import LogParser
    from hslog.export import FriendlyPlayerExporter
    from hslog.packets import Choices, ChosenEntities, SendChoices

    from .capture import _CREATE_GAME_MARKER, TolerantExporter, power_logs

    games: dict[int, list[str]] = {}
    game_no = 0
    for path in power_logs(session_dir):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines(keepends=True):
            if _CREATE_GAME_MARKER in line:
                game_no += 1
            if game_no:
                games.setdefault(game_no, []).append(line)

    out: dict[int, list[dict[str, Any]]] = {}
    for no, lines in games.items():
        parser = LogParser()
        for line in lines:
            try:
                parser.read_line(line)
            except Exception:
                continue
        if not parser.games:
            continue
        tree = parser.games[-1]
        try:
            friendly_id = FriendlyPlayerExporter(tree).export()
            game = TolerantExporter(tree).export().game
        except Exception:
            continue

        def entity(eid: int) -> Any:
            try:
                return game.find_entity_by_id(eid)
            except Exception:
                return None

        def name_of(eid: int) -> str | None:
            e = entity(eid)
            return resolver.name(getattr(e, "card_id", None)) if e is not None else None

        def controller_of(eid: int) -> int | None:
            e = entity(eid)
            return e.tags.get(GameTag.CONTROLLER) if e is not None else None

        chosen = [*tree.recursive_iter(ChosenEntities), *tree.recursive_iter(SendChoices)]
        out[no] = extract_discovers(list(tree.recursive_iter(Choices)), chosen,
                                    name_of, friendly_id, controller_of)
    return out


def discover_readout(games: dict[tuple, dict[str, Any]],
                     actual: dict[int, list[dict[str, Any]]],
                     session: str) -> dict[str, Any]:
    """Score Jev's and the coach's Discover picks against what was chosen.

    Advice events are matched to the game's real choices in order: Jev's by
    its logged option set, the coach's by the option its line names. Only
    games from `session` are scored (game_no is per-session).
    """
    stats: dict[str, dict[str, int]] = {}
    rows = []
    for (sess, game_no), g in sorted(games.items(), key=lambda kv: kv[0]):
        if sess != session or game_no not in actual:
            continue
        # Each source answers each real choice at most once (the coach may
        # republish the same pick; Jev never answers twice).
        remaining = {"jev": list(actual[game_no]), "coach": list(actual[game_no])}
        for ev in g["advice_events"]:
            if ev.get("kind") != "discover" or not ev.get("discover"):
                continue
            src = "jev" if ev.get("variant") == "jev" else "coach"
            jev_opts = sorted((ev.get("jev") or {}).get("options") or [])
            match = None
            for choice in remaining[src]:
                if jev_opts and sorted(choice["options"]) == jev_opts:
                    match = choice
                    break
                if not jev_opts and picked_name(ev["discover"], choice["options"]):
                    match = choice
                    break
            if not match:
                continue
            remaining[src].remove(match)
            pick = picked_name(ev["discover"], match["options"])
            followed = pick == match["chosen"]
            s = stats.setdefault(src, {"picks": 0, "followed": 0, "followed_won": 0,
                                       "overridden": 0, "overridden_won": 0})
            s["picks"] += 1
            won = g.get("result") == "WON"
            if followed:
                s["followed"] += 1
                s["followed_won"] += won
            else:
                s["overridden"] += 1
                s["overridden_won"] += won
            rows.append({"game": game_no, "source": src, "options": " / ".join(match["options"]),
                         "advised": pick or "?", "you_picked": match["chosen"],
                         "result": g.get("result") or "?"})
    summary = [{"source": src, **s, "agree_pct": _pct(s["followed"], s["picks"])}
               for src, s in sorted(stats.items())]
    return {"picks": rows, "summary": summary}
