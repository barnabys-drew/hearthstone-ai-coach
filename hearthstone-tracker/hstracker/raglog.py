"""Retrieval telemetry for the lessons engine (progressive-RAG Phase 1).

Every your-turn snapshot logs what Tier-0 retrieval did — including when it
did nothing, because the misses are what justify (or kill) every later
retrieval tier. Events are appended to retrieval_log.jsonl next to games.db;
`hst rag-report` joins them into firing rates, dead knowledge, misses, and a
precision proxy.

Event types (all carry `ev` and `ts`; live events add `session`+`game_no`,
the weak game key — both come from the same `hst live` process, so the pair
joins exactly):

- corpus   once per game: every lesson id in the store at game start
- match    one per your-turn; `matched` may be empty (that IS the signal)
- outcome  at game over: result/deck, plus start_time when the DB re-parse ran
- ingest   a genuinely new lesson entered the store (cross-process, ts-joined)
- applied  the coach explicitly used a fired lesson (cross-process, ts-joined)

The lesson id — sha1 over whitespace-collapsed lowercased text, first 12 hex
chars — is a contract with the log file: change it and old events orphan.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from .config import DEFAULT_DB

RAG_LOG_PATH = DEFAULT_DB.parent / "retrieval_log.jsonl"

# Cross-process events carry only a timestamp; these windows bound the join.
APPLIED_JOIN_WINDOW = 2 * 3600  # advice published during a game, before its outcome
INGEST_JOIN_WINDOW = 30 * 60    # post-game coach records lessons after the outcome


def lesson_id(text: str) -> str:
    """Stable 12-hex id for a lesson: survives whitespace and casing edits."""
    norm = " ".join(text.split()).lower()
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


def append_event(event: dict[str, Any], path: Path | None = None) -> bool:
    """Append one JSON line; never raises — telemetry must not hurt the hot path.

    Single-line O_APPEND writes this small are atomic on Linux, so `hst live`
    and coach_publish.py can both append without locking.
    """
    try:
        path = path or RAG_LOG_PATH
        event.setdefault("ts", time.time())
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, separators=(",", ":")) + "\n")
        return True
    except Exception:
        return False


def read_events(path: Path | None = None) -> list[dict[str, Any]]:
    """Tolerant reader: a torn or corrupt line must not poison the whole log."""
    path = path or RAG_LOG_PATH
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


class RagTurnLogger:
    """Emits corpus/match/outcome events from the live loop.

    One match event per (game_no, raw_turn) on your turn, re-emitted only if
    the matched-id set changes mid-turn (a draw can change matches); the
    report unions per turn. Zero matches still logs — misses are the point.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or RAG_LOG_PATH
        self._last_key: tuple | None = None
        self._corpus_game: int | None = None
        self._outcome_games: set[int] = set()
        self._first_seen: dict[int, float] = {}

    def on_snapshot(self, snap: dict[str, Any], matched: list, corpus: list, *,
                    session: str, game_no: int) -> None:
        try:
            self._first_seen.setdefault(game_no, time.time())
            if self._corpus_game != game_no:
                self._corpus_game = game_no
                append_event({
                    "ev": "corpus", "session": session, "game_no": game_no,
                    "ids": [lesson_id(rec.lesson) for rec in corpus],
                    "count": len(corpus),
                    "untriggered": [lesson_id(rec.lesson) for rec in corpus
                                    if rec.trigger.condition_count() == 0],
                }, self.path)
            if snap.get("game_over") and game_no not in self._outcome_games:
                # Fallback: the CLI's dedicated game-over branch is skipped when
                # game over arrives together with a turn change; make sure an
                # outcome lands either way (on_game_over stays a no-op after).
                self._emit_outcome(snap, None, session=session, game_no=game_no,
                                   deck_name=None)
            if snap.get("whose_turn") != "me" or snap.get("phase") == "mulligan" \
                    or snap.get("game_over"):
                return
            ids = frozenset(lesson_id(rec.lesson) for rec in matched)
            key = (game_no, snap.get("raw_turn"), ids)
            if key == self._last_key:
                return
            self._last_key = key
            opp = snap.get("opp") or {}
            append_event({
                "ev": "match", "session": session, "game_no": game_no,
                "turn": snap.get("turn"), "raw_turn": snap.get("raw_turn"),
                "tiers": ["t0"],
                "matched": [{"id": lesson_id(rec.lesson), "tier": "t0",
                             "conds": rec.trigger.condition_count()}
                            for rec in matched],
                "opp_class": opp.get("class"),
                "corpus_count": len(corpus),
            }, self.path)
        except Exception:
            pass  # telemetry is display-only; never break the live loop

    def on_game_over(self, snap: dict[str, Any], record, *, session: str,
                     game_no: int, deck_name: str | None) -> None:
        try:
            self._emit_outcome(snap, record, session=session, game_no=game_no,
                               deck_name=deck_name)
        except Exception:
            pass

    def _emit_outcome(self, snap, record, *, session, game_no, deck_name) -> None:
        if game_no in self._outcome_games:
            return
        self._outcome_games.add(game_no)
        opp = snap.get("opp") or {}
        event = {
            "ev": "outcome", "session": session, "game_no": game_no,
            "result": getattr(record, "result", None) or snap.get("game_over"),
            "deck": getattr(record, "deck_name", None) or deck_name,
            "opp_class": getattr(record, "opponent_class", None) or opp.get("class"),
            "turns": getattr(record, "turns", None) or snap.get("turn"),
            "first_seen_ts": self._first_seen.get(game_no),
            "start_time": getattr(record, "start_time", None),
        }
        append_event(event, self.path)


def join_games(events: list[dict[str, Any]]) -> dict[tuple, dict[str, Any]]:
    """Group events into per-game aggregates.

    match/corpus/outcome join exactly on (session, game_no); applied joins to
    the game in progress when it was published (earliest outcome after it);
    ingest joins to the game it reviews (latest outcome before it). Unjoined
    cross-process events land in the special key ("", -1).
    """
    games: dict[tuple, dict[str, Any]] = {}

    def agg(key: tuple) -> dict[str, Any]:
        return games.setdefault(key, {
            "result": None, "deck": None, "opp_class": None, "turns": None,
            "outcome_ts": None, "corpus_ids": set(), "turn_events": {},
            "fired_ids": set(), "applied_ids": set(), "ingested_ids": set(),
        })

    for ev in events:
        kind = ev.get("ev")
        if kind not in ("match", "corpus", "outcome"):
            continue
        key = (ev.get("session"), ev.get("game_no"))
        g = agg(key)
        if kind == "corpus":
            g["corpus_ids"] |= set(ev.get("ids") or [])
        elif kind == "match":
            ids = {m.get("id") for m in (ev.get("matched") or [])}
            g["turn_events"].setdefault(ev.get("raw_turn"), set()).update(ids)
            g["fired_ids"] |= ids
        else:
            g["result"] = ev.get("result")
            g["deck"] = ev.get("deck")
            g["opp_class"] = ev.get("opp_class")
            g["turns"] = ev.get("turns")
            g["outcome_ts"] = ev.get("ts")

    outcomes = sorted(
        ((g["outcome_ts"], key) for key, g in games.items() if g["outcome_ts"]),
    )
    unjoined = agg(("", -1))

    for ev in events:
        kind, ts = ev.get("ev"), ev.get("ts") or 0
        if kind == "applied":
            # The game in progress: earliest outcome at-or-after publish time.
            target = next((key for ots, key in outcomes
                           if ots >= ts and ots - ts <= APPLIED_JOIN_WINDOW), None)
            (games[target] if target else unjoined)["applied_ids"] |= set(
                ev.get("lesson_ids") or [])
        elif kind == "ingest":
            # The game being reviewed: latest outcome at-or-before ingest time.
            target = next((key for ots, key in reversed(outcomes)
                           if ots <= ts and ts - ots <= INGEST_JOIN_WINDOW), None)
            lid = ev.get("lesson_id")
            if lid:
                (games[target] if target else unjoined)["ingested_ids"].add(lid)

    if not any(unjoined[k] for k in ("applied_ids", "ingested_ids", "fired_ids",
                                     "corpus_ids", "turn_events")) \
            and unjoined["result"] is None:
        games.pop(("", -1), None)
    return games


def _real_games(games: dict[tuple, dict]) -> dict[tuple, dict]:
    return {k: g for k, g in games.items() if k != ("", -1)}


def summary_rows(games: dict[tuple, dict]) -> list[dict[str, Any]]:
    real = _real_games(games)
    turns = sum(len(g["turn_events"]) for g in real.values())
    hits = sum(1 for g in real.values() for ids in g["turn_events"].values() if ids)
    return [{
        "games": sum(1 for g in real.values() if g["result"]),
        "your_turns": turns,
        "turns_matched": hits,
        "match_rate_pct": round(100 * hits / turns) if turns else 0,
        "ingests": sum(len(g["ingested_ids"]) for g in games.values()),
        "applied": sum(len(g["applied_ids"]) for g in games.values()),
    }]


def fire_rows(games: dict[tuple, dict], store: list) -> list[dict[str, Any]]:
    """Per-lesson firing stats. Denominator = your-turns in games whose corpus
    contained the lesson (fallback: all turns, for events predating corpus)."""
    real = _real_games(games)
    titles = {lesson_id(rec.lesson): (rec.title or rec.lesson[:60]) for rec in store}
    seen: set[str] = set()
    for g in real.values():
        seen |= g["corpus_ids"] | g["fired_ids"]
    rows = []
    for lid in seen:
        eligible = fires = games_fired = 0
        for g in real.values():
            in_corpus = not g["corpus_ids"] or lid in g["corpus_ids"]
            if in_corpus:
                eligible += len(g["turn_events"])
            n = sum(1 for ids in g["turn_events"].values() if lid in ids)
            fires += n
            games_fired += bool(n)
        rows.append({
            "id": lid,
            "title": titles.get(lid, "(no longer in store)"),
            "fires": fires,
            "games_fired": games_fired,
            "fire_rate_pct": round(100 * fires / eligible) if eligible else 0,
        })
    rows.sort(key=lambda r: (-r["fires"], r["id"]))
    return rows


def dead_rows(games: dict[tuple, dict], store: list) -> list[dict[str, Any]]:
    """Current-store lessons that never fired; conds==0 can never fire in Tier 0."""
    real = _real_games(games)
    fired: set[str] = set()
    for g in real.values():
        fired |= g["fired_ids"]
    rows = []
    for rec in store:
        lid = lesson_id(rec.lesson)
        if lid in fired:
            continue
        rows.append({
            "id": lid,
            "title": rec.title or rec.lesson[:60],
            "conds": rec.trigger.condition_count(),
            "games_in_corpus": sum(1 for g in real.values() if lid in g["corpus_ids"]),
            "note": "untriggerable (no conditions)" if rec.trigger.condition_count() == 0 else "",
        })
    rows.sort(key=lambda r: (r["conds"], -r["games_in_corpus"]))
    return rows


def miss_rows(games: dict[tuple, dict]) -> list[dict[str, Any]]:
    """Games where the post-game coach recorded a misplay but retrieval was silent."""
    rows = []
    for (session, game_no), g in _real_games(games).items():
        if g["ingested_ids"] and not g["fired_ids"]:
            rows.append({
                "session": session, "game": game_no,
                "result": g["result"], "deck": g["deck"],
                "your_turns": len(g["turn_events"]),
                "ingested": " ".join(sorted(g["ingested_ids"])),
            })
    return rows


def precision_rows(games: dict[tuple, dict], store: list) -> tuple[list[dict], bool]:
    """Per fired lesson: precision = games(fired ∧ applied ∧ won) / games(fired).

    Returns (rows, has_applied). Without any applied events the proxy degrades
    to win-rate-when-fired — labeled by the caller.
    """
    real = _real_games(games)
    has_applied = any(g["applied_ids"] for g in games.values())
    titles = {lesson_id(rec.lesson): (rec.title or rec.lesson[:60]) for rec in store}
    fired_ids: set[str] = set()
    for g in real.values():
        fired_ids |= g["fired_ids"]
    rows = []
    for lid in fired_ids:
        fired_games = [g for g in real.values() if lid in g["fired_ids"]]
        won = sum(1 for g in fired_games if g["result"] == "WON")
        applied_won = sum(1 for g in fired_games
                          if lid in g["applied_ids"] and g["result"] == "WON")
        row = {
            "id": lid,
            "title": titles.get(lid, "(no longer in store)"),
            "games_fired": len(fired_games),
            "won_when_fired": won,
        }
        if has_applied:
            row["applied"] = sum(1 for g in fired_games if lid in g["applied_ids"])
            row["precision_pct"] = round(100 * applied_won / len(fired_games)) if fired_games else 0
        else:
            row["win_rate_pct"] = round(100 * won / len(fired_games)) if fired_games else 0
        rows.append(row)
    rows.sort(key=lambda r: (-r["games_fired"], r["id"]))
    return rows, has_applied


def unjoined_counts(games: dict[tuple, dict]) -> dict[str, int]:
    stray = games.get(("", -1)) or {"applied_ids": set(), "ingested_ids": set()}
    return {"applied": len(stray["applied_ids"]), "ingested": len(stray["ingested_ids"])}
