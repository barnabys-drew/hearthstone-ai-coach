"""Parse Decks.log to learn which of your decks each game was played with.

Decks.log (enabled via the [Decks] section in log.config) logs blocks like:

    D 21:27:23.4954000 Finding Game With Deck:
    D 21:27:23.4954000 ### Big Spell Mage
    D 21:27:23.4964000 # Class: Mage
    D 21:27:23.4964000 # ...card comment lines...
    D 21:27:23.4974000 AAECAf0EBMABqwS0BJYFDU2KAcABwQPmBJYF...

"Finding Game With Deck" fires when you queue; "Deck Contents Received" when a
deck is loaded. Either way the block carries the deck name and deckstring.
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

from .capture import _combine_date, session_start

# Severity prefix is I/D/W/E depending on log level.
_EVENT_RE = re.compile(
    r"^[IDWE] ([\d:]+\.\d+)\s+(?:.*- )?(Finding Game With Deck|Deck Contents Received|Starting Arena Game With Deck)"
)
_NAME_RE = re.compile(r"^[IDWE] [\d:]+\.\d+\s+### (.+?)\s*$")
_CODE_RE = re.compile(r"^[IDWE] ([\d:]+\.\d+)\s+([A-Za-z0-9+/]{16,}={0,2})\s*$")

# How long before a game's start we still trust the last deck event. Queue plus
# matchmaking rarely exceeds a few minutes.
MATCH_WINDOW = timedelta(minutes=30)


@dataclass
class DeckEvent:
    when: datetime
    name: str | None
    deckstring: str


def _looks_like_deckstring(code: str) -> bool:
    try:
        raw = base64.b64decode(code, validate=True)
    except Exception:
        return False
    # Deckstrings start with a 0x00 reserved byte, version 1.
    return len(raw) > 4 and raw[0] == 0 and raw[1] == 1


def deckstring_format(code: str) -> int | None:
    """FormatType id (1 Wild, 2 Standard, ...) from a deckstring, or None.

    The header is three varints — reserved (0), version (1), format — and all
    three are below 128, so the format is simply the third byte."""
    try:
        data = base64.b64decode("".join(code.split()))
    except Exception:
        return None
    return data[2] if len(data) > 2 and not data[2] & 0x80 else None


def decode_deckstring_counts(code: str) -> dict[int, int]:
    """{dbfId: count} decoded from a deckstring; {} if it can't be decoded."""
    try:
        data = base64.b64decode("".join(code.split()))
    except Exception:
        return {}
    pos = 0

    def varint() -> int:
        nonlocal pos
        shift = value = 0
        while True:
            byte = data[pos]
            pos += 1
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value
            shift += 7

    try:
        varint(), varint(), varint()  # reserved, version, format
        for _ in range(varint()):     # heroes
            varint()
        counts: dict[int, int] = {}
        for fixed_count in (1, 2):
            for _ in range(varint()):
                dbf = varint()
                counts[dbf] = counts.get(dbf, 0) + fixed_count
        for _ in range(varint()):
            dbf, n = varint(), varint()
            counts[dbf] = counts.get(dbf, 0) + n
        return counts
    except Exception:
        return {}


def _parse_log_time(text: str) -> datetime | None:
    # Log fractions have 7 digits; datetime accepts at most 6.
    hms, _, frac = text.partition(".")
    try:
        return datetime.strptime(f"{hms}.{frac[:6]}", "%H:%M:%S.%f")
    except ValueError:
        return None


class DeckLogParser:
    """Line-fed parser usable for both whole files and live tailing."""

    def __init__(self, base_datetime: datetime | None) -> None:
        self.events: list[DeckEvent] = []
        self._base: date = (base_datetime or datetime.now()).date()
        self._last_dt: datetime | None = base_datetime
        self._pending_ts: datetime | None = None
        self._pending_name: str | None = None

    def feed(self, line: str) -> None:
        m = _EVENT_RE.match(line)
        if m:
            t = _parse_log_time(m.group(1))
            self._pending_ts = t
            self._pending_name = None
            return
        if self._pending_ts is None:
            return
        m = _NAME_RE.match(line)
        if m:
            self._pending_name = m.group(1)
            return
        m = _CODE_RE.match(line)
        if m and _looks_like_deckstring(m.group(2)):
            when = _combine_date(self._pending_ts.time(), self._base, self._last_dt)
            self._last_dt = when
            self.events.append(DeckEvent(when, self._pending_name, m.group(2)))
            # A "Deck Contents Received" block can list several decks
            # (### name / # Deck ID / code, repeated), so stay in the block.
            self._pending_name = None


def parse_decks_log(path: Path, *, base_datetime: datetime | None = None) -> list[DeckEvent]:
    parser = DeckLogParser(base_datetime or session_start(path.parent))
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            parser.feed(line)
    return parser.events


def deck_logs(session_dir: Path) -> Iterable[Path]:
    for name in ("Decks_old.log", "Decks.log"):
        p = session_dir / name
        if p.exists():
            yield p


def match_deck(events: list[DeckEvent], game_start: datetime) -> DeckEvent | None:
    """The most recent deck event before (or right at) the game's start."""
    best: DeckEvent | None = None
    for ev in events:
        if ev.when <= game_start + timedelta(seconds=30):
            if best is None or ev.when > best.when:
                best = ev
    if best and game_start - best.when <= MATCH_WINDOW:
        return best
    return None


# Modes with no player-built 30-card deck; a lingering deck event from an
# earlier constructed queue must not be attached to these games.
_NO_DECK_MODES = ("BATTLEGROUNDS", "MERCENARIES")


def attach_decks(records, events: list[DeckEvent]) -> None:
    """Set deck_name/deckstring on game records that plausibly used a deck."""
    for record in records:
        if record.game_type and any(m in record.game_type for m in _NO_DECK_MODES):
            continue
        event = match_deck(events, datetime.fromisoformat(record.start_time))
        if event:
            record.deck_name = event.name
            record.deckstring = event.deckstring
