"""Who answers the choice decisions (mulligans and Discovers), picked in the overlay.

The overlay's picker writes `settings.json` in the shared overlay folder;
`hst live` re-reads it whenever it changes, so a switch applies to the next
mulligan with no restart. Four modes:

    jev     Jev answers and its card goes on the overlay; the coach model waits
    coach   the coach model answers, as it did before Jev
    both    both answer: the coach's card is the main one, Jev's shows as a
            second opinion, and both are logged for a live comparison
    off     nobody advises; you decide alone (your own baseline)

Turn plans are not part of this: they belong to the coaching session's model,
which the overlay can show but not change.

With no settings file, `HS_JEV=1` means `jev` and anything else means `coach`,
which is how the stack behaved before the picker existed. Jev modes need
`TYPESAFE_API_KEY`; without it they fall back to `coach` and the feed says so.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

MODES = ("jev", "coach", "both", "off")
SETTINGS_FILE = "settings.json"
_NEEDS_JEV = ("jev", "both")


def default_mode() -> str:
    return "jev" if os.environ.get("HS_JEV") == "1" else "coach"


def read_mode(overlay_dir: str | os.PathLike[str] | None) -> str:
    """The picked mode, or the default when the file is missing or invalid."""
    if overlay_dir:
        try:
            data = json.loads((Path(overlay_dir) / SETTINGS_FILE).read_text(encoding="utf-8"))
            mode = data.get("choices") if isinstance(data, dict) else None
            if mode in MODES:
                return mode
        except (OSError, ValueError):
            pass
    return default_mode()


def write_mode(overlay_dir: str | os.PathLike[str], mode: str) -> Path:
    """Atomically record the picked mode, keeping any other settings keys."""
    if mode not in MODES:
        raise ValueError(f"unknown choices mode {mode!r}; expected one of {', '.join(MODES)}")
    path = Path(overlay_dir) / SETTINGS_FILE
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(current, dict):
            current = {}
    except (OSError, ValueError):
        current = {}
    current.update({"choices": mode, "updated": time.time()})
    from .overlay import atomic_write_json  # lazy: keeps pydantic off read-only imports
    return atomic_write_json(path, current)


def effective_mode(mode: str, jev_available: bool) -> str:
    """What actually runs: a Jev mode without a Jev key degrades to `coach`."""
    return "coach" if mode in _NEEDS_JEV and not jev_available else mode


def uses_jev(mode: str) -> bool:
    return mode in _NEEDS_JEV


class ModeWatcher:
    """mtime-cached reader for the live loop: one stat() per poll."""

    def __init__(self, overlay_dir: str | os.PathLike[str] | None) -> None:
        self.overlay_dir = overlay_dir
        self._mtime: float | None = -1.0
        self.mode = read_mode(overlay_dir)

    def poll(self) -> tuple[str, bool]:
        """(mode, changed since the last poll)."""
        try:
            mtime: float | None = (Path(self.overlay_dir) / SETTINGS_FILE).stat().st_mtime \
                if self.overlay_dir else None
        except OSError:
            mtime = None
        if mtime == self._mtime:
            return self.mode, False
        self._mtime = mtime
        mode = read_mode(self.overlay_dir)
        changed, self.mode = mode != self.mode, mode
        return mode, changed


def status_line(mode: str, jev_available: bool) -> str:
    """The `== CHOICES MODE` feed line the coach reads its instructions from."""
    eff = effective_mode(mode, jev_available)
    note = {
        "jev": "Jev answers mulligans and Discovers; wait for the == JEV line, don't publish them",
        "coach": "you answer mulligans and Discovers",
        "both": "you answer and publish as usual; Jev's answer shows beside yours as a second opinion",
        "off": "don't advise mulligans or Discovers; the player decides alone",
    }[eff]
    fallback = (f" ({mode} picked, but TYPESAFE_API_KEY is not set)"
                if eff != mode else "")
    return f"== CHOICES MODE: {eff}{fallback} — {note}"
