"""Turn-plan validator (#25): catch plans that can't be legal, before you act.

The live coach's worst real-game misses were mechanical, not strategic: a
lethal plan that sent an attacker face past a second taunt, spending more
mana than was available, naming a card that wasn't in hand, writing "dies"
against a Reborn minion. The tracker already has the board, so code can check
those, the same way `checks.py` decides which rules apply.

`check_plan(steps, snap)` reads the published steps against the live
snapshot and returns warnings. `coach_publish.py` runs it on every turn and
lethal publish: warnings print back to the coach (who can republish) and
go on the overlay card's warning line.

**Fail open, always.** Plan text is free-form, so every rule flags only what
it is sure of. A card it can't find anywhere a card could come from is
flagged; one that might have been drawn or generated mid-turn is not. A taunt
counts as handled if the plan names it anywhere before the face attacks. A
missed warning costs nothing new; a false alarm teaches people to ignore the
line.
"""
from __future__ import annotations

import re
from typing import Any

_PLAY_RE = re.compile(r"^\s*(?:\d+[.)]\s*)?(play|cast|equip)\s+(.+)$", re.I)
# Where a played card's name ends: its parenthetical, a target arrow, a
# preposition, punctuation, or the end of the step.
_NAME_END_RE = re.compile(r"\s*(?:\(|->|→|—|\s-\s|:|,|;|\bon\b|\binto\b|\bat\b|\bwith\b|"
                          r"\btargeting\b|\bto\b|\bface\b|\bfor\b|$)", re.I)
_ATTACK_FACE_RE = re.compile(r"^\s*(?:\d+[.)]\s*)?(attack|swing|hit)\b.*\b(face|their hero|enemy hero|opponent)\b", re.I)
_KILL_RE = re.compile(r"\b(kill|kills|killed|dies|die|destroy|destroys|clear|clears|finish|finishes|removes?)\b", re.I)
_FREE_RE = re.compile(r"\b(refund(ed|s)?|free|costs?\s*\(?0\)?|0 mana|reduced|discount(ed)?)\b", re.I)
# Things a plan "plays" that aren't hand cards with a cost.
_NOT_A_CARD = re.compile(r"^(the\s+)?(discovered|generated|drawn|new|it|them|that|this|"
                         r"remaining|rest|anything|whatever|a\s|an\s|another|any|card|minion|spell)\b", re.I)


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().strip("'\"*").lower())


def _played_names(step: str) -> list[str]:
    m = _PLAY_RE.match(step)
    if not m:
        return []
    rest = m.group(2)
    # First boundary after the first word, so "For Glory!" and "Call to Arms"
    # keep enough of their name to be found.
    end = next((e for e in _NAME_END_RE.finditer(rest) if e.start() > 0), None)
    rest = rest[: end.start()] if end else rest
    parts = re.split(r"\s+\+\s+|\s+and\s+|\s*&\s*", rest)
    return [p.strip() for p in parts if p.strip()]


def _find(cand: str, names: list[str]) -> str | None:
    """The known name `cand` refers to: exact, or a clear prefix either way
    ("Agent" for "Agent of the Old Ones"); None if nothing matches."""
    c = _norm(cand)
    if not c:
        return None
    for n in names:
        if _norm(n) == c:
            return n
    for n in names:
        nn = _norm(n)
        if len(c) >= 4 and (nn.startswith(c) or c.startswith(nn)):
            return n
    return None


def _mentions(name: str, text: str) -> bool:
    """Plan text names this minion: the full name, or any distinctive word of
    it (>= 5 letters), since plans shorten names ("clear Elemental" for
    "Fiery Elemental"). Generous on purpose: a false "handled" only means a
    missed warning."""
    t = text.lower()
    n = _norm(name)
    if n in t:
        return True
    return any(len(w) >= 5 and re.search(rf"\b{re.escape(w)}\b", t)
               for w in re.findall(r"[a-z']+", n))


def check_plan(steps: list[str], snap: dict[str, Any]) -> list[dict[str, str]]:
    """Warnings for a turn plan against the snapshot it was written for."""
    me, opp = snap.get("me") or {}, snap.get("opp") or {}
    hand = me.get("hand") or []
    hand_names = [c.get("name") or "" for c in hand]
    board_names = [c.get("name") or "" for c in me.get("board") or []]
    deck_names = [c.get("name") or "" for c in (me.get("deck_cards_left") or [])
                  + (me.get("deck_extra") or []) + (me.get("deck_full") or [])]
    weapon = (me.get("weapon") or {}).get("name")
    known_elsewhere = board_names + deck_names + ([weapon] if weapon else [])
    warnings: list[dict[str, str]] = []

    # --- cards that exist nowhere, and the mana bill for those in hand -------
    remaining = list(hand)  # each hand card pays once, even if named twice
    spent, coins = 0, 0
    for step in steps:
        for cand in _played_names(step):
            if _NOT_A_CARD.match(cand):
                continue
            if _norm(cand) in ("the coin", "coin"):
                coins += 1
                continue
            name = _find(cand, [c.get("name") or "" for c in remaining])
            if name:
                card = next(c for c in remaining if c.get("name") == name)
                remaining.remove(card)
                cost = card.get("cost")
                if isinstance(cost, int) and not _FREE_RE.search(step):
                    spent += cost
                continue
            if _find(cand, hand_names) or _find(cand, known_elsewhere):
                continue  # a second copy, a location/weapon, or a card that may be drawn
            if re.match(r"^[A-Z0-9'][\w'!,.\- ]{2,}$", cand):  # looks like a real card name
                warnings.append({"rule": "unknown card",
                                 "detail": f"'{cand}' isn't in hand, on the board, or in the deck"})
    mana = me.get("mana")
    if isinstance(mana, int) and spent > mana + coins:
        warnings.append({"rule": "mana",
                         "detail": f"plays from hand cost {spent} but only {mana + coins} mana is available"
                                   + (" (with The Coin)" if coins else "")})

    # --- face attacks past a taunt nobody dealt with ------------------------
    face_at = next((i for i, s in enumerate(steps) if _ATTACK_FACE_RE.search(s)), None)
    if face_at is not None:
        before = " ".join(steps[:face_at])
        for m in opp.get("board") or []:
            flags = set(m.get("flags") or [])
            if "taunt" in flags and not flags & {"stealth", "untargetable", "immune"} \
                    and not _mentions(m.get("name") or "", before):
                warnings.append({"rule": "taunt",
                                 "detail": f"{m.get('name')} has Taunt and isn't dealt with before the face attacks"})

    # --- "kills" a Reborn minion once and never budgets the second life -----
    for m in opp.get("board") or []:
        if "reborn" not in set(m.get("flags") or []):
            continue
        hits = [i for i, s in enumerate(steps) if _mentions(m.get("name") or "", s)]
        if hits and _KILL_RE.search(steps[hits[-1]]) and len(hits) == 1:
            warnings.append({"rule": "reborn",
                             "detail": f"{m.get('name')} has Reborn: it comes back at 1 Health after it dies"})
    return warnings


def format_warnings(warnings: list[dict[str, str]]) -> str:
    return "Plan check: " + "; ".join(w["detail"] for w in warnings)
