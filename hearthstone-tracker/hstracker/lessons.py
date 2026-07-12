"""Trigger-matched coaching lessons.

Past misplays become structured records with concrete trigger conditions
(card names, keyword flags, opponent class, hand size). A deterministic
matcher checks every your-turn snapshot against the store and the hits are
inlined into the turn marker — the coach sees the relevant lesson next to
the exact board state that makes it relevant, at zero added latency and
with no LLM/embedding calls (the corpus is small and triggers are concrete
game entities).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from .config import DEFAULT_DB

STORE_PATH = DEFAULT_DB.parent / "lesson_store.json"


class LessonTrigger(BaseModel):
    """AND-combined conditions; a lesson fires when every set condition holds.

    Card-name lists are case-insensitive exact matches and fire when ANY
    listed name is present in that zone (OR within a list, AND across
    fields).
    """

    enemy_board: list[str] = Field(default_factory=list)
    my_board: list[str] = Field(default_factory=list)
    my_hand: list[str] = Field(default_factory=list)
    enemy_flags: list[str] = Field(default_factory=list)  # e.g. "poisonous", "taunt"
    opp_class: str | None = None  # e.g. "WARRIOR"
    opp_hand_min: int | None = None  # fires when their hand size >= N

    @field_validator("enemy_board", "my_board", "my_hand", "enemy_flags", mode="after")
    @classmethod
    def _lower(cls, value: list[str]) -> list[str]:
        return [v.strip().lower() for v in value if v and v.strip()]

    @field_validator("opp_class", mode="before")
    @classmethod
    def _upper_class(cls, value: Any) -> str | None:
        text = str(value).strip().upper() if value else None
        return text or None

    def condition_count(self) -> int:
        return sum([
            bool(self.enemy_board), bool(self.my_board), bool(self.my_hand),
            bool(self.enemy_flags), self.opp_class is not None,
            self.opp_hand_min is not None,
        ])


class Lesson(BaseModel):
    """One recorded misplay and the better line, with its firing trigger."""

    lesson: str
    title: str | None = None  # short display form for the overlay panel (~50 chars)
    headline: bool = False  # the ONE synthesized cross-game insight (newest wins)
    trigger: LessonTrigger = Field(default_factory=LessonTrigger)
    cost: str | None = None  # what the mistake cost, e.g. "7 face damage"
    deck: str | None = None  # which of MY decks this is about; None = general principle
    matchup: str | None = None  # freeform context, e.g. "Aya Rogue vs Warrior"
    date: str | None = None  # ISO date of the game it came from
    source: str = "coach"  # coach | post-game | user

    @field_validator("lesson", mode="after")
    @classmethod
    def _clip(cls, value: str) -> str:
        value = " ".join(value.split())
        return value[:239] + "…" if len(value) > 240 else value

    @field_validator("title", mode="before")
    @classmethod
    def _clip_title(cls, value):
        if value is None:
            return None
        text = " ".join(str(value).split())
        return (text[:59] + "…" if len(text) > 60 else text) or None


def load_store(path: Path | None = None) -> list[Lesson]:
    path = path or STORE_PATH
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    lessons = []
    for item in raw.get("lessons", []):
        try:
            lessons.append(Lesson.model_validate(item))
        except ValueError:
            continue  # one bad record must not poison the store
    return lessons


def append_lesson(lesson: Lesson | dict[str, Any], path: Path | None = None) -> Path:
    path = path or STORE_PATH
    record = lesson if isinstance(lesson, Lesson) else Lesson.model_validate(lesson)
    existing = load_store(path)
    if any(rec.lesson == record.lesson for rec in existing):
        return path  # exact duplicate; keep the store clean
    payload = {"ts": time.time(),
               "lessons": [rec.model_dump(mode="json") for rec in [record, *existing]][:200]}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    if path == STORE_PATH:
        # New knowledge entered the real store — tell the retrieval log so
        # rag-report can spot games with misplays that retrieval missed.
        # Gated to the default path so tests and ad-hoc stores stay silent.
        try:
            from .raglog import append_event, lesson_id
            append_event({"ev": "ingest", "lesson_id": lesson_id(record.lesson),
                          "source": record.source, "deck": record.deck,
                          "matchup": record.matchup,
                          "conds": record.trigger.condition_count()})
        except Exception:
            pass
    mirror_store(path)
    return path


def mirror_store(path: Path | None = None) -> Path | None:
    """Copy the structured store into the overlay folder so the lessons panel
    can group by deck. Best-effort: the store is authoritative, the mirror is
    display-only."""
    from .overlay import atomic_write_json, resolve_overlay_dir
    path = path or STORE_PATH
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return atomic_write_json(resolve_overlay_dir() / "lesson_store.json", raw)
    except OSError:
        return None


def _names(cards: list[dict[str, Any]]) -> set[str]:
    return {str(c.get("name", "")).lower() for c in cards}


def _flags(cards: list[dict[str, Any]]) -> set[str]:
    return {str(f).lower() for c in cards for f in (c.get("flags") or [])}


def match_lessons(snapshot: dict[str, Any], lessons: list[Lesson], cap: int = 3) -> list[Lesson]:
    """Lessons whose every trigger condition holds for this snapshot.

    Most specific (condition count) first, then newest; capped so markers
    stay scannable on the turn timer.
    """
    me, opp = snapshot.get("me") or {}, snapshot.get("opp") or {}
    enemy_names = _names(opp.get("board") or [])
    my_names = _names(me.get("board") or [])
    hand_names = _names(me.get("hand") or [])
    enemy_flags = _flags(opp.get("board") or [])
    opp_class = str(opp.get("class") or "").upper()
    opp_hand = int(opp.get("hand_hidden") or 0) + len(opp.get("hand") or [])

    matched = []
    for lesson in lessons:
        t = lesson.trigger
        if t.condition_count() == 0:
            continue  # untriggered lessons live in the overlay panel, not markers
        if t.enemy_board and not (set(t.enemy_board) & enemy_names):
            continue
        if t.my_board and not (set(t.my_board) & my_names):
            continue
        if t.my_hand and not (set(t.my_hand) & hand_names):
            continue
        if t.enemy_flags and not (set(t.enemy_flags) & enemy_flags):
            continue
        if t.opp_class and t.opp_class != opp_class:
            continue
        if t.opp_hand_min is not None and opp_hand < t.opp_hand_min:
            continue
        matched.append(lesson)

    matched.sort(key=lambda rec: (rec.trigger.condition_count(), rec.date or ""), reverse=True)
    return matched[:cap]


class StoreWatcher:
    """mtime-cached store loader so the live loop never re-parses needlessly."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or STORE_PATH
        self._mtime: float | None = None
        self._lessons: list[Lesson] = []

    def lessons(self) -> list[Lesson]:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            self._mtime, self._lessons = None, []
            return self._lessons
        if mtime != self._mtime:
            self._mtime = mtime
            self._lessons = load_store(self.path)
        return self._lessons
