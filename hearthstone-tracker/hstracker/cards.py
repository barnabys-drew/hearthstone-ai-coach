"""Map hero card ids to player classes.

Uses a cached HearthstoneJSON dump when available (covers skins and
Battlegrounds heroes); falls back to the classic HERO_NN numbering offline.
"""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

CARDS_URL = "https://api.hearthstonejson.com/v1/latest/enUS/cards.json"
CACHE = Path.home() / ".cache" / "hearthstone-tracker" / "cards.json"
MAX_CARDS_BYTES = 100 * 1024 * 1024

_HERO_NN_CLASS = {
    "01": "WARRIOR", "02": "SHAMAN", "03": "ROGUE", "04": "PALADIN",
    "05": "HUNTER", "06": "DRUID", "07": "WARLOCK", "08": "MAGE",
    "09": "PRIEST", "10": "DEMONHUNTER", "11": "DEATHKNIGHT",
}


def _download_cards() -> list[dict] | None:
    try:
        req = urllib.request.Request(CARDS_URL, headers={"User-Agent": "hearthstone-tracker/0.1"})
        with urllib.request.urlopen(req, timeout=60) as resp:  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected -- CARDS_URL is a module constant
            data = resp.read(MAX_CARDS_BYTES)
        cards = json.loads(data)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_bytes(data)
        return cards
    except Exception:
        return None


class HeroClassResolver:
    def __init__(self, *, allow_fetch: bool = True) -> None:
        self._by_card_id: dict[str, str] = {}
        self._names: dict[str, str] = {}
        self._cards: dict[str, dict] = {}
        cards = None
        if CACHE.exists():
            try:
                cards = json.loads(CACHE.read_text(encoding="utf-8"))
            except Exception:
                cards = None
        if cards is None and allow_fetch:
            cards = _download_cards()
        self._by_dbf: dict[int, dict] = {}
        for card in cards or []:
            cid, cls = card.get("id"), card.get("cardClass")
            if not cid:
                continue
            self._cards[cid] = card
            if isinstance(card.get("dbfId"), int):
                self._by_dbf[card["dbfId"]] = card
            if cls:
                self._by_card_id[cid] = cls
            if card.get("name"):
                self._names[cid] = card["name"]

    def name(self, card_id: str | None) -> str | None:
        if not card_id:
            return None
        return self._names.get(card_id)

    def card(self, card_id: str | None) -> dict:
        """Full HearthstoneJSON card dict (name, cost, text, ...), or {}."""
        if not card_id:
            return {}
        return self._cards.get(card_id, {})

    def card_by_dbf(self, dbf_id: int) -> dict:
        return self._by_dbf.get(dbf_id, {})

    def player_class(self, hero_card_id: str | None) -> str | None:
        if not hero_card_id:
            return None
        cls = self._by_card_id.get(hero_card_id)
        if cls and cls not in ("NEUTRAL",):
            return cls
        m = re.match(r"HERO_(\d\d)", hero_card_id)
        if m:
            return _HERO_NN_CLASS.get(m.group(1))
        return cls
