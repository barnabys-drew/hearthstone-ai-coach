"""Bridge files for the Hearthstone coach overlay.

The live tracker runs in WSL, but the native always-on-top overlay runs on the
Windows side.  This module writes tiny JSON files to a shared directory that a
Windows Electron process can poll without needing a server.

The advice payload is validated through pydantic models so every writer
(CLI flags, raw JSON, the coach agent) produces the same normalized shape.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import DEFAULT_DB

_SYSTEM_WINDOWS_USERS = {
    "all users",
    "default",
    "default user",
    "defaultuser0",
    "desktop.ini",
    "public",
    "wsiaccount",
}
_VALID_KINDS = {"idle", "turn", "mulligan", "lethal", "update", "gameover"}
_GAME_OVER_STATES = {"WON", "LOST", "TIED", "UNKNOWN"}


def _truncate(text: str, max_len: int) -> str:
    text = text.strip()
    if len(text) > max_len:
        return text[: max_len - 1].rstrip() + "…"
    return text


class MulliganRow(BaseModel):
    """One keep/toss row on the mulligan card."""

    model_config = ConfigDict(populate_by_name=True)

    card: str = Field(validation_alias="name", max_length=200)
    keep: bool = False
    reason: str | None = None

    @field_validator("card", mode="after")
    @classmethod
    def _clip_card(cls, value: str) -> str:
        return _truncate(value, 80)

    @field_validator("reason", mode="before")
    @classmethod
    def _clip_reason(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return _truncate(text, 180) if text else None


class Lethal(BaseModel):
    """Lethal flag + the arithmetic shown on the red banner."""

    is_lethal: bool = False
    math: str | None = None

    @field_validator("math", mode="before")
    @classmethod
    def _clip_math(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return _truncate(text, 160) if text else None


class Advice(BaseModel):
    """The full advice card, as rendered by the overlay panels."""

    ts: float = Field(default_factory=time.time)
    kind: str = "turn"
    turn: int | None = None
    headline: str | None = None
    why: str | None = None
    steps: list[str] = Field(default_factory=list)
    warning: str | None = None
    lethal: Lethal | None = None
    mulligan: list[MulliganRow] = Field(default_factory=list)
    game_over: str | None = None
    discover: str | None = None
    lessons: list[str] = Field(default_factory=list)

    @field_validator("kind", mode="before")
    @classmethod
    def _valid_kind(cls, value: Any) -> str:
        kind = str(value or "turn").lower()
        return kind if kind in _VALID_KINDS else "turn"

    @field_validator("turn", mode="before")
    @classmethod
    def _coerce_turn(cls, value: Any) -> int | None:
        if value is None or not str(value).strip():
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @field_validator("headline", "why", "warning", "game_over", "discover", mode="before")
    @classmethod
    def _clip_text(cls, value: Any, info) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        limits = {"headline": 120, "why": 900, "warning": 240, "game_over": 20, "discover": 240}
        return _truncate(text, limits[info.field_name])

    @field_validator("steps", "lessons", mode="before")
    @classmethod
    def _coerce_lines(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw = value.splitlines()
        elif isinstance(value, (list, tuple)):
            raw = [str(item) for item in value]
        else:
            raw = [str(value)]
        lines = [_truncate(text, 240) for text in (t.strip() for t in raw) if text]
        return lines[:12]

    @field_validator("lethal", mode="before")
    @classmethod
    def _coerce_lethal(cls, value: Any) -> Any:
        if value is None or isinstance(value, (dict, Lethal)):
            return value
        if isinstance(value, bool):
            return {"is_lethal": value}
        return {"is_lethal": True, "math": str(value)}

    @field_validator("mulligan", mode="before")
    @classmethod
    def _coerce_mulligan(cls, value: Any) -> list[Any]:
        if not value:
            return []
        if not isinstance(value, list):
            value = [value]
        rows = []
        for item in value[:10]:
            if isinstance(item, (dict, MulliganRow)):
                rows.append(item)
            else:
                text = str(item).strip()
                if text:
                    rows.append({"card": text})
        return rows

    @model_validator(mode="after")
    def _kind_consistency(self) -> "Advice":
        if self.lethal and self.lethal.is_lethal is False and self.kind == "lethal":
            self.lethal.is_lethal = True
        if self.kind == "lethal" and self.lethal is None:
            self.lethal = Lethal(is_lethal=True)
        if self.game_over and self.game_over.upper() in _GAME_OVER_STATES:
            self.game_over = self.game_over.upper()
        if self.kind == "gameover" and not self.game_over:
            self.game_over = "UNKNOWN"
        if not self.headline:
            self.headline = _DEFAULT_HEADLINES.get(self.kind, "Do this now")
        self.lessons = self.lessons[:4]
        return self


_DEFAULT_HEADLINES = {
    "idle": "Waiting for live coach",
    "turn": "Do this now",
    "mulligan": "Mulligan advice",
    "lethal": "LETHAL",
    "update": "Update",
    "gameover": "Game over",
}


class LessonsFile(BaseModel):
    """Coaching lessons accumulated across games (lessons.json)."""

    ts: float = Field(default_factory=time.time)
    lessons: list[str] = Field(default_factory=list)

    @field_validator("lessons", mode="before")
    @classmethod
    def _coerce_lessons(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        lines = [_truncate(str(item), 240) for item in value if str(item).strip()]
        return lines


def default_overlay_dir() -> Path:
    """Best-effort default shared folder for a WSL tracker + Windows overlay."""
    env_user = os.environ.get("HS_WINDOWS_USER")
    users_root = Path("/mnt/c/Users")
    if env_user:
        return users_root / env_user / "hs-overlay"
    if users_root.is_dir():
        candidates = [
            path for path in users_root.iterdir()
            if path.is_dir() and path.name.lower() not in _SYSTEM_WINDOWS_USERS and not path.name.startswith(".")
        ]
        if len(candidates) == 1:
            return candidates[0] / "hs-overlay"
        # Several Windows users: prefer one whose name resembles the WSL user.
        wsl_user = os.environ.get("USER", "").lower()
        for path in candidates:
            name = path.name.lower()
            if wsl_user and (name == wsl_user or name in wsl_user or wsl_user in name):
                return path / "hs-overlay"
    return DEFAULT_DB.parent / "overlay"


def resolve_overlay_dir(override: str | os.PathLike[str] | None = None) -> Path:
    raw = override or os.environ.get("HS_OVERLAY_DIR")
    return Path(raw).expanduser() if raw else default_overlay_dir()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
    return path


def normalize_advice(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate/normalize an advice payload into the overlay's JSON shape."""
    return Advice.model_validate(payload or {}).model_dump(mode="json")


def clear_advice(turn: int | None = None) -> dict[str, Any]:
    return normalize_advice({
        "kind": "idle",
        "turn": turn,
        "headline": "Waiting for live coach",
        "why": "Start a Hearthstone game and publish turn advice to populate this panel.",
    })


def write_advice(payload: dict[str, Any], overlay_dir: str | os.PathLike[str] | None = None) -> Path:
    directory = resolve_overlay_dir(overlay_dir)
    advice = normalize_advice(payload)
    if advice.get("lessons"):
        append_lessons(advice["lessons"], overlay_dir)
    return atomic_write_json(directory / "advice.json", advice)


def write_clear_advice(overlay_dir: str | os.PathLike[str] | None = None, *, turn: int | None = None) -> Path:
    directory = resolve_overlay_dir(overlay_dir)
    return atomic_write_json(directory / "advice.json", clear_advice(turn))


def append_lessons(lessons: list[str], overlay_dir: str | os.PathLike[str] | None = None) -> Path | None:
    """Accumulate lessons in lessons.json across games (newest first, deduped)."""
    incoming = LessonsFile(lessons=lessons).lessons
    if not incoming:
        return None
    directory = resolve_overlay_dir(overlay_dir)
    path = directory / "lessons.json"
    try:
        existing = LessonsFile.model_validate(json.loads(path.read_text(encoding="utf-8"))).lessons
    except (OSError, json.JSONDecodeError, ValueError):
        existing = []
    merged = list(dict.fromkeys(incoming + existing))[:30]
    return atomic_write_json(path, LessonsFile(lessons=merged).model_dump(mode="json"))


def write_discover(text: str, overlay_dir: str | os.PathLike[str] | None = None) -> Path:
    """Merge a Discover pick into the current advice card without clobbering it.

    Discovers fire mid-turn while the turn plan is still on screen; the pick
    gets its own slot so both stay visible. The next full advice write
    (which omits `discover`) clears the slot naturally.
    """
    directory = resolve_overlay_dir(overlay_dir)
    path = directory / "advice.json"
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        current = {}
    current["discover"] = text
    current["ts"] = time.time()
    return atomic_write_json(path, normalize_advice(current))


def mirror_live_snapshot(snapshot: dict[str, Any], overlay_dir: str | os.PathLike[str] | None = None) -> Path:
    directory = resolve_overlay_dir(overlay_dir)
    return atomic_write_json(directory / "live.json", snapshot)
