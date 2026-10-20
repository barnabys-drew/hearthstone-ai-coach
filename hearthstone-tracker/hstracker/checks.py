"""Tier-0 retrieval over the *instruction* corpus.

The lessons engine retrieves past misplays. This module retrieves **rules** —
which of the coach's per-turn checks are actually live given the board in front
of it.

The problem it solves: `hearthstone-live-coach/SKILL.md` imposes an
unconditional 11-step checklist plus 15 known traps on every turn. Turn 1
against an empty enemy board still makes the coach reason through "enumerate
EVERY taunt minion before totaling face damage", "check lethal", "deathrattle
caution" and "random-splash goes last" — all vacuous, all still deliberated.
Telemetry put the median advice latency at ~30s against a 15s turn timer, with
early turns no faster than late ones, and latency uncorrelated with payload
size or answer length. The cost is deliberation, not tokens and not retrieval.

So: the tracker already parses the board deterministically. It can decide which
checks apply and say so, turning the model's *self-audit* (expensive, and the
source of the worst shipped bug — a lethal plan that counted a blocked
attacker's damage as face because it missed a second taunt) into a
*precomputation* (free, exact). The model then runs the 0-4 checks that fired
instead of re-deriving the applicable rule set from scratch every turn.

This is the roadmap's own thesis applied one level up: RAG != embeddings;
retrieval starts with structure. Same discipline as `match_lessons()` — pure,
in-process, zero-LLM, microseconds, safe on the hot path.

**Fail-open is the rule.** Every gate is deliberately generous: when in doubt,
emit the check. A spurious check costs the coach a few seconds; a suppressed
one costs the game. Nothing here removes information from the marker — the full
board is still printed, and the taunt check *names* the taunts the coach was
previously asked to enumerate by hand, so a gated marker strictly dominates an
ungated one.
"""
from __future__ import annotations

import os
import re
from typing import Any

# Direct damage that a spell/hero-power can point at the enemy face. Used only
# to decide whether the lethal check is worth running, never to compute a plan
# — the coach still does the real arithmetic from the card text.
_DAMAGE_RE = re.compile(r"deal\s+\$?(\d+)\s+damage", re.I)

# ...unless the text says it can only hit minions. Kept narrow on purpose: a
# missed lethal is a lost game, so anything ambiguous counts as face-capable.
# Judged per sentence, and a sentence that also names a hero or character
# ("to an enemy minion or hero") still counts: "minion" appearing somewhere
# in it is not the same as "minions only".
_MINION_ONLY_RE = re.compile(
    r"to\s+(a|an|another|all|each|any)\s+[^.]*?minion|"
    r"to\s+(a|an)\s+(friendly|damaged|random\s+friendly)",
    re.I,
)
_FACE_RE = re.compile(r"\b(hero|heroes|character|characters|face)\b", re.I)

# The random-splash trap is specifically about randomness that can DESTROY YOUR
# INTENDED TARGET before a later numbered step reaches it (Erupting Volcano
# killing the minion that step 2 was going to finish off). Randomness that only
# *generates* cards — "Discover a spell", "Get two random 4-Cost spells",
# "Summon two random 1-Cost minions" — cannot disrupt a sequence and must not
# fire this check. Requiring damage/destroy next to the randomness is what
# separates the two: a bare /random/ match fired on 83% of real turns, almost
# all of them generation effects.
_RANDOM_RE = re.compile(
    r"deal\s+\$?\d+\s+damage[^.]{0,40}random|"
    r"randomly\s+split|"
    r"destroy[^.]{0,30}random",
    re.I,
)
_DEATHRATTLE_RE = re.compile(r"deathrattle", re.I)

# Board-wide damage. The hazard is that it also hits YOUR minions, and that it
# can kill one of THEIR low-HP minions you were counting on trading into (real
# miss: Searing Fissure for 1 would have killed their own 2/1 Vendor).
_AOE_RE = re.compile(
    r"damage\s+to\s+all|to\s+all\s+minions|all\s+enemy\s+minions|"
    r"damage\s+to\s+(all|each)\s+", re.I,
)

# Flags worth calling out when present. Ordered by how badly the coach has
# historically misplayed them.
_NOTABLE_FLAGS = (
    "poisonous", "divine shield", "reborn", "frozen",
    "stealth", "immune", "untargetable", "windfury", "lifesteal", "rush",
)


def gated_enabled() -> bool:
    """Lab-gated like Tiers 1/2 and budgeting: opt in with HS_COACH_GATED=1."""
    return os.environ.get("HS_COACH_GATED", "").strip() in {"1", "true", "yes"}


def _attackers(side: dict[str, Any]) -> list[dict[str, Any]]:
    """Board entities that can actually swing. Locations never attack."""
    return [c for c in side.get("board") or [] if not c.get("location")]


def _effective_hp(side: dict[str, Any]) -> int:
    return int(side.get("hp") or 0) + int(side.get("armor") or 0)


def _board_damage(side: dict[str, Any]) -> int:
    """Face damage the board could throw. Windfury swings twice.

    Exhausted minions are counted anyway: for the incoming (survival) check
    they will be ready on the opponent's turn, and for the outgoing check
    over-counting only makes the lethal check fire more often, which is the
    safe direction.
    """
    total = 0
    for c in _attackers(side):
        atk = int(c.get("atk") or 0)
        if "windfury" in (c.get("flags") or []):
            atk *= 2
        total += atk
    return total


def _weapon_damage(side: dict[str, Any]) -> int:
    weapon = side.get("weapon") or {}
    return int(weapon.get("atk") or 0)


def _hand_burn(side: dict[str, Any]) -> int:
    """Upper bound on face damage sitting in hand. Generous by design."""
    total = 0
    for c in side.get("hand") or []:
        for sentence in re.split(r"[.;]", c.get("text") or ""):
            if _MINION_ONLY_RE.search(sentence) and not _FACE_RE.search(sentence):
                continue
            for m in _DAMAGE_RE.finditer(sentence):
                total += int(m.group(1))
    return total


def _named(minions: list[dict[str, Any]]) -> str:
    return ", ".join(
        f"{c.get('name')} {c.get('atk')}/{c.get('health')}" for c in minions
    )


def live_checks(snap: dict[str, Any]) -> dict[str, Any]:
    """Which per-turn checks the current board actually warrants.

    Returns `{"checks": [{"name", "detail"}...], "klass": ...}` where klass is
    CRITICAL (lethal live in either direction), TRIVIAL (nothing fired — a
    pure curve/tempo decision) or STANDARD.
    """
    me, opp = snap.get("me") or {}, snap.get("opp") or {}
    checks: list[dict[str, str]] = []

    def add(name: str, detail: str = "") -> None:
        checks.append({"name": name, "detail": detail})

    # --- lethal, both directions. These set the CRITICAL class. -------------
    my_ceiling = _board_damage(me) + int(me.get("attack") or 0) \
        + _weapon_damage(me) + _hand_burn(me)
    opp_effective = _effective_hp(opp)
    lethal = my_ceiling >= opp_effective and opp_effective > 0

    # Their swing back. Their weapon counts; so does everything on their board.
    incoming = _board_damage(opp) + _weapon_damage(opp)
    my_effective = _effective_hp(me)
    survival = incoming >= my_effective and my_effective > 0

    if lethal:
        add("lethal", f"your ceiling {my_ceiling} vs their {opp_effective} eff. HP")
    if survival:
        add("survival", f"their board swings {incoming} vs your {my_effective} eff. HP")

    # --- taunt: the check that names its own answer -------------------------
    # This is the one that shipped a bug (0c3e262): the coach cleared one taunt,
    # then counted the attacker blocked by a SECOND taunt as face damage. So we
    # do not merely say "check for taunts" — we enumerate them, which is the
    # step the coach was demonstrably unreliable at doing from memory.
    taunts = [c for c in _attackers(opp) if "taunt" in (c.get("flags") or [])]
    if taunts:
        add("taunt", f"{len(taunts)} on their board: {_named(taunts)}")

    # --- the rest: fire only when the board contains the thing ---------------
    if int(me.get("attack") or 0) > 0:
        add("hero-attack", f"your hero can swing for {me['attack']}")

    if any((c.get("name") or "") == "The Coin" for c in me.get("hand") or []):
        add("coin", "The Coin is in hand")

    rattlers = [
        c for c in (_attackers(me) + _attackers(opp))
        if _DEATHRATTLE_RE.search(c.get("text") or "")
    ]
    if rattlers:
        add("deathrattle", _named(rattlers))

    # Hand AND board: a Location (Erupting Volcano) is activated from the board
    # and splashes exactly like the spell version, so scanning only the hand
    # misses it — caught on a real snapshot where Volcano sat on board as 0/1.
    splashers = [
        c.get("name") for c in (list(me.get("hand") or []) + list(me.get("board") or []))
        if _RANDOM_RE.search(c.get("text") or "")
    ]
    if splashers:
        add("random-splash", ", ".join(dict.fromkeys(str(n) for n in splashers)))

    aoe = [
        c.get("name") for c in (list(me.get("hand") or []) + list(me.get("board") or []))
        if _AOE_RE.search(c.get("text") or "")
    ]
    if aoe:
        low = [c for c in _attackers(opp) if 0 < int(c.get("health") or 0) <= 2]
        detail = ", ".join(dict.fromkeys(str(n) for n in aoe))
        if low:
            detail += f" — their low-HP minions: {_named(low)}"
        add("aoe", detail)

    flagged: list[str] = []
    for c in _attackers(me) + _attackers(opp):
        hits = [f for f in (c.get("flags") or []) if f in _NOTABLE_FLAGS]
        if hits:
            flagged.append(f"{c.get('name')} [{', '.join(hits)}]")
    if flagged:
        add("flags", "; ".join(flagged))

    if lethal or survival:
        klass = "CRITICAL"
    elif not checks:
        klass = "TRIVIAL"
    else:
        klass = "STANDARD"
    return {"checks": checks, "klass": klass}


def format_checks(result: dict[str, Any]) -> list[str]:
    """The marker lines. Indented to match the rest of format_snapshot."""
    checks = result["checks"]
    lines = [f"   decision class: {result['klass']}"]
    if not checks:
        lines.append("   checks live (0) — none apply: curve/tempo decision only")
        return lines
    lines.append(f"   checks live ({len(checks)}) — run ONLY these:")
    for c in checks:
        detail = f" — {c['detail']}" if c["detail"] else ""
        lines.append(f"     {c['name']}{detail}")
    return lines
