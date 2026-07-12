"""Offline retrieval replay over historical Power.logs (progressive-RAG Phase 1).

Runs the CURRENT lesson store against past games, turn by turn, so any change
to the store or the matcher can be regression-tested against real games before
it goes live: replay, change something, replay again, diff the --json output.

Why not LiveGameTail.poll(): over a fully-written file one poll keeps only the
last game's buffer (it resets on every CREATE_GAME). Replay splits games and
turns itself, then reuses LiveGameTail.snapshot() verbatim on each buffer.

Snapshot timing: each buffer is the end-of-raw-turn state, which matches the
live logger's per-turn union semantics (live re-emits when mid-turn draws
change the matched set; the report unions per turn). Slightly over-matches a
single live first-poll snapshot; that bias is constant across replays, so
diffs stay meaningful.

Replay NEVER writes the live retrieval log; events carry "replay": true and
omit ts so repeated runs are byte-identical for diffing.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from .capture import _CREATE_GAME_MARKER, power_logs
from .lessons import Lesson, match_lessons
from .live import LiveGameTail
from .raglog import lesson_id

_TURN_RE = re.compile(r"TAG_CHANGE Entity=GameEntity tag=TURN value=(\d+)")


def iter_turn_buffers(lines: Iterator[str]) -> Iterator[tuple[int, int | None, list[str]]]:
    """Yield (game_no, ended_raw_turn, buffer) at each turn boundary.

    The buffer is the game's lines up to but not including the TURN-advance
    line — i.e. the completed turn's final state. Each game also yields one
    final (game_no, None, buffer) with everything, for game-over detection.
    Turn values appear in both GameState and PowerTaskList lines; only the
    first advance to a new value triggers a yield.
    """
    game_no = 0
    buffer: list[str] = []
    last_turn = 0
    for line in lines:
        if not line.strip():
            continue
        if _CREATE_GAME_MARKER in line:
            if buffer:
                yield game_no, None, list(buffer)
            game_no += 1
            buffer = []
            last_turn = 0
        m = _TURN_RE.search(line)
        if m and buffer and game_no:
            value = int(m.group(1))
            if value > last_turn:
                if last_turn:  # value 1 is the game starting, nothing ended yet
                    yield game_no, last_turn, list(buffer)
                last_turn = value
        if game_no:
            buffer.append(line if line.endswith("\n") else line + "\n")
    if buffer:
        yield game_no, None, list(buffer)


def replay_session(session_dir: Path, lessons: list[Lesson], resolver) -> list[dict[str, Any]]:
    """Replay every game in a session dir against the given store.

    Returns corpus/match/outcome events in the retrieval-log schema, tagged
    "replay": true, ts-free and deterministic.
    """
    def all_lines() -> Iterator[str]:
        for path in power_logs(session_dir):  # Power_old.log first, then Power.log
            try:
                yield from path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue

    session = session_dir.name
    tail = LiveGameTail(session_dir / "Power.log")  # path unused; we drive .lines
    events: list[dict[str, Any]] = []
    corpus_games: set[int] = set()
    seen_turns: set[tuple[int, int]] = set()

    for game_no, ended_turn, buffer in iter_turn_buffers(all_lines()):
        tail.lines = buffer
        # deck_counts only feeds the display fields (deck_full/deck_extra);
        # no trigger condition reads them, so matching fidelity is unaffected.
        snap = tail.snapshot(resolver, deck_counts=None)
        if not snap:
            continue
        if game_no not in corpus_games:
            corpus_games.add(game_no)
            events.append({
                "ev": "corpus", "session": session, "game_no": game_no,
                "ids": [lesson_id(rec.lesson) for rec in lessons],
                "count": len(lessons),
                "untriggered": [lesson_id(rec.lesson) for rec in lessons
                                if rec.trigger.condition_count() == 0],
                "replay": True,
            })
        if ended_turn is None:
            if snap.get("game_over"):
                opp = snap.get("opp") or {}
                events.append({
                    "ev": "outcome", "session": session, "game_no": game_no,
                    "result": snap["game_over"], "deck": None,
                    "opp_class": opp.get("class"), "turns": snap.get("turn"),
                    "replay": True,
                })
            continue
        if snap.get("whose_turn") != "me" or snap.get("phase") == "mulligan" \
                or snap.get("game_over"):
            continue
        key = (game_no, snap.get("raw_turn") or ended_turn)
        if key in seen_turns:
            continue
        seen_turns.add(key)
        matched = match_lessons(snap, lessons)
        opp = snap.get("opp") or {}
        events.append({
            "ev": "match", "session": session, "game_no": game_no,
            "turn": snap.get("turn"), "raw_turn": snap.get("raw_turn"),
            "tiers": ["t0"],
            "matched": [{"id": lesson_id(rec.lesson), "tier": "t0",
                         "conds": rec.trigger.condition_count()}
                        for rec in matched],
            "opp_class": opp.get("class"),
            "corpus_count": len(lessons),
            "replay": True,
        })
    return events


def replay_report(events: list[dict[str, Any]], store: list[Lesson],
                  emit: Callable[[str], None]) -> None:
    """Human-readable replay summary: per-game table + the same firing/dead
    sections rag-report computes, over the replayed events only."""
    from . import stats as stats_mod
    from .raglog import dead_rows, fire_rows, join_games

    games = join_games(events)
    rows = []
    for (session, game_no), g in sorted(games.items()):
        if (session, game_no) == ("", -1):
            continue
        fired = sorted(g["fired_ids"])
        rows.append({
            "game": game_no,
            "result": g["result"] or "(incomplete)",
            "opp_class": g["opp_class"],
            "your_turns": len(g["turn_events"]),
            "turns_matched": sum(1 for ids in g["turn_events"].values() if ids),
            "fired": " ".join(fired) if fired else "",
        })
    for title, table in (
        ("Replayed games", rows),
        ("Per-lesson firing (replayed)", fire_rows(games, store)),
        ("Dead knowledge (replayed)", dead_rows(games, store)),
    ):
        emit(f"## {title}")
        emit(stats_mod.format_table(table))
        emit("")
