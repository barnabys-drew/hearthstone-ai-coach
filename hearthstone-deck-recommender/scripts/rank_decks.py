#!/usr/bin/env python3
"""Rank current Hearthstone meta decks by how easy they are to complete.

Given (1) your collection and (2) a set of current top Standard decks as
deckstrings, this computes the arcane dust needed to finish each deck and ranks
them cheapest-first, so you can see which competitive deck is easiest to build.

The script is dependency-free. It reuses the deckstring decoder from the sibling
`hearthstone-deck-builder` skill when available, and otherwise falls back to a
built-in decoder so it still works if copied on its own.
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

HJSON_LATEST_COLLECTIBLE = "https://api.hearthstonejson.com/v1/latest/enUS/cards.collectible.json"
USER_AGENT = "hearthstone-deck-recommender-skill/1.0 (+https://hearthstonejson.com)"

# Arcane dust to CRAFT a standard (non-golden) copy, by rarity.
CRAFT_COST = {
    "FREE": 0,
    "COMMON": 40,
    "RARE": 100,
    "EPIC": 400,
    "LEGENDARY": 1600,
}
# Core-set cards can't be crafted; they are granted for free by leveling.
UNCRAFTABLE_SETS = {"CORE"}


# --------------------------------------------------------------------------- #
# Deckstring decoding (imported from sibling skill, with a local fallback).
# --------------------------------------------------------------------------- #
def _load_sibling_decoder():
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent.parent / "hearthstone-deck-builder" / "scripts",
    ]
    for cand in candidates:
        script = cand / "build_deck_code.py"
        if script.exists():
            sys.path.insert(0, str(cand))
            try:
                from build_deck_code import decode_deckstring  # type: ignore

                return decode_deckstring
            except Exception:
                pass
    return None


def _fallback_read_varint(data: bytes, offset: int) -> tuple[int, int]:
    shift = 0
    value = 0
    while True:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte & 0x80 == 0:
            return value, offset
        shift += 7


def _fallback_decode(code: str) -> dict[str, Any]:
    data = base64.b64decode("".join(code.strip().split()))
    off = 0
    _, off = _fallback_read_varint(data, off)   # reserved
    _, off = _fallback_read_varint(data, off)   # version
    fmt, off = _fallback_read_varint(data, off)
    hero_count, off = _fallback_read_varint(data, off)
    heroes = []
    for _ in range(hero_count):
        h, off = _fallback_read_varint(data, off)
        heroes.append(h)
    cards: list[tuple[int, int]] = []
    for fixed in (1, 2):
        n, off = _fallback_read_varint(data, off)
        for _ in range(n):
            dbf, off = _fallback_read_varint(data, off)
            cards.append((dbf, fixed))
    n, off = _fallback_read_varint(data, off)
    for _ in range(n):
        dbf, off = _fallback_read_varint(data, off)
        cnt, off = _fallback_read_varint(data, off)
        cards.append((dbf, cnt))
    return {"format_id": fmt, "heroes": heroes, "cards": sorted(cards)}


_DECODE = _load_sibling_decoder() or _fallback_decode


def decode_deckstring(code: str) -> dict[str, Any]:
    return _DECODE(code)


# --------------------------------------------------------------------------- #
# Card data
# --------------------------------------------------------------------------- #
# Caps on fetched response sizes, so a misbehaving or hostile endpoint cannot
# exhaust memory. Real payloads are far smaller: HearthstoneJSON card data is
# ~15 MB, collection JSON well under 1 MB.
MAX_COLLECTION_RESPONSE_BYTES = 25 * 1024 * 1024
MAX_CARDS_RESPONSE_BYTES = 50 * 1024 * 1024


def read_limited(response: Any, max_bytes: int, what: str) -> bytes:
    data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(
            f"{what} response exceeded the {max_bytes // (1024 * 1024)} MB size limit; aborting"
        )
    return data


def fetch_text(url: str, *, cookie: str | None = None,
               max_bytes: int = MAX_COLLECTION_RESPONSE_BYTES) -> str:
    """Fetch text with a browser-like User-Agent.

    Some HSReplay collection URLs are private to the signed-in browser session.
    If a direct URL returns login HTML or 403, export/copy the JSON manually or
    supply a Cookie header copied from the browser via --collection-cookie-file
    or the HS_COLLECTION_COOKIE env var.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"}
    if cookie:
        headers["Cookie"] = cookie
    # file:// is supported deliberately (offline/local collection exports);
    # everything else is restricted to http(s).
    if not url.lower().startswith(("http://", "https://", "file://")):
        raise ValueError(f"collection URL must be http(s) or file, got: {url[:40]}")
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected -- user-supplied on their own CLI; scheme allowlisted above
        return read_limited(response, max_bytes, url).decode("utf-8")


def load_json_from_url(url: str, *, cookie: str | None = None,
                       max_bytes: int = MAX_COLLECTION_RESPONSE_BYTES) -> Any:
    text = fetch_text(url, cookie=cookie, max_bytes=max_bytes)
    stripped = text.lstrip()
    if not stripped.startswith(("{", "[")):
        preview = stripped[:120].replace("\n", " ")
        raise ValueError(
            "URL did not return JSON. If this is a private HSReplay page/API, "
            "copy the JSON response manually or supply a Cookie header via "
            "--collection-cookie-file or the HS_COLLECTION_COOKIE env var. "
            f"Response started with: {preview!r}"
        )
    return json.loads(text)


def load_cards(path: str | None, *, allow_fetch: bool) -> list[dict[str, Any]]:
    if path:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    if not allow_fetch:
        return []
    return load_json_from_url(HJSON_LATEST_COLLECTIBLE, max_bytes=MAX_CARDS_RESPONSE_BYTES)


def index_cards(cards: Iterable[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    by_dbf: dict[int, dict[str, Any]] = {}
    for card in cards:
        dbf = card.get("dbfId")
        if isinstance(dbf, int):
            by_dbf[dbf] = card
    return by_dbf


# --------------------------------------------------------------------------- #
# Collection normalization
# --------------------------------------------------------------------------- #
def _owned_from_value(value: Any) -> int:
    """A collection entry can be an int, or a list like [normal, golden, ...].

    Any premium copy (golden/diamond/signature) still satisfies the deck slot,
    so owned = sum of all copies across finishes.
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, list):
        return sum(int(x) for x in value if isinstance(x, (int, float)))
    if isinstance(value, dict):
        keys = ("count", "owned", "ownedTotal", "total", "normal")
        total = 0
        for k in ("normal", "golden", "diamond", "signature"):
            if isinstance(value.get(k), (int, float)):
                total += int(value[k])
        if total:
            return total
        for k in keys:
            if isinstance(value.get(k), (int, float)):
                return int(value[k])
    return 0


def normalize_collection(raw: Any) -> dict[int, int]:
    """Return {dbfId: owned_count} from many collection export shapes."""
    owned: dict[int, int] = defaultdict(int)

    # HSReplay "collection/mine" JSON: {"collection": {"<dbf>": [n,g,d,s]}}
    if isinstance(raw, dict) and isinstance(raw.get("collection"), dict):
        raw = raw["collection"]

    if isinstance(raw, dict):
        for key, value in raw.items():
            try:
                dbf = int(key)
            except (TypeError, ValueError):
                # Skip non-card keys (e.g. metadata).
                continue
            owned[dbf] += _owned_from_value(value)
        return dict(owned)

    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            dbf = item.get("dbfId") or item.get("dbf_id") or item.get("dbf")
            if dbf is None:
                continue
            owned[int(dbf)] += _owned_from_value(item if "dbfId" not in item else item.get("count", item))
        return dict(owned)

    raise ValueError("Unrecognized collection format")


def normalize_collection_text(text: str) -> dict[int, int]:
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return normalize_collection(json.loads(text))
    # CSV fallback: needs a dbfId column and a count/owned column.
    owned: dict[int, int] = defaultdict(int)
    reader = csv.DictReader(text.splitlines())
    fields = {f.lower(): f for f in (reader.fieldnames or [])}
    dbf_key = next((fields[k] for k in ("dbfid", "dbf_id", "dbf") if k in fields), None)
    if not dbf_key:
        raise ValueError("CSV collection needs a dbfId column")
    count_key = next(
        (fields[k] for k in ("ownedtotal", "owned", "count", "total", "normal") if k in fields),
        None,
    )
    for row in reader:
        try:
            dbf = int(row[dbf_key])
        except (TypeError, ValueError):
            continue
        owned[dbf] += int(float(row.get(count_key, 1) or 0)) if count_key else 1
    return dict(owned)


def load_collection(path: str) -> dict[int, int]:
    return normalize_collection_text(Path(path).read_text(encoding="utf-8"))


def load_collection_url(url: str, *, cookie: str | None = None) -> dict[int, int]:
    return normalize_collection(load_json_from_url(url, cookie=cookie))


def load_collection_source(path: str | None, url: str | None, *, cookie: str | None = None) -> dict[int, int]:
    if bool(path) == bool(url):
        raise ValueError("Provide exactly one of --collection or --collection-url")
    if url:
        return load_collection_url(url, cookie=cookie)
    assert path is not None
    return load_collection(path)


def read_cookie_file(path: str) -> str:
    """Read a Cookie header from a file, or stdin when path is '-'.

    Keeping cookies in files/env vars avoids putting them directly in shell
    history or long-lived terminal scrollback. The caller is still responsible
    for protecting the file (chmod 600 is recommended).
    """
    if path == "-":
        return sys.stdin.read().strip()
    return Path(path).read_text(encoding="utf-8").strip()


def resolve_collection_cookie(
    *,
    cookie_arg: str | None = None,
    cookie_file: str | None = None,
    env: dict[str, str] | None = None,
) -> str | None:
    """Resolve a private collection Cookie header without printing it.

    Precedence is explicit safe file, file env var, cookie env var, then the
    deprecated raw CLI argument for backwards compatibility.
    """
    env = env if env is not None else os.environ
    source = cookie_file or env.get("HS_COLLECTION_COOKIE_FILE")
    if source:
        return read_cookie_file(source)
    return env.get("HS_COLLECTION_COOKIE") or cookie_arg


# --------------------------------------------------------------------------- #
# Meta deck loading
# --------------------------------------------------------------------------- #
def load_decks(path: str) -> list[dict[str, Any]]:
    text = Path(path).read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        data = json.loads(text)
        decks = data.get("decks", data) if isinstance(data, dict) else data
        return list(decks)
    # Plain text: lines of deckstrings, optional "# Name" comment before each.
    decks: list[dict[str, Any]] = []
    pending_name: str | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            pending_name = line.lstrip("#").strip() or pending_name
            continue
        decks.append({"name": pending_name or f"Deck {len(decks) + 1}", "deckstring": line})
        pending_name = None
    return decks


# --------------------------------------------------------------------------- #
# Core ranking
# --------------------------------------------------------------------------- #
def rarity_cost(card: dict[str, Any]) -> int:
    if str(card.get("set", "")).upper() in UNCRAFTABLE_SETS:
        return 0
    return CRAFT_COST.get(str(card.get("rarity", "")).upper(), 0)


def evaluate_deck(
    deck: dict[str, Any],
    owned: dict[int, int],
    by_dbf: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    code = deck.get("deckstring") or deck.get("code")
    if not code:
        raise ValueError(f"Deck {deck.get('name')!r} has no deckstring")
    decoded = decode_deckstring(code)

    total_cards = 0
    owned_cards = 0
    dust = 0
    free_missing = 0
    missing: list[dict[str, Any]] = []
    missing_by_rarity: dict[str, int] = defaultdict(int)

    for dbf, count in decoded["cards"]:
        total_cards += count
        have = min(owned.get(dbf, 0), count)
        owned_cards += have
        need = count - have
        if need <= 0:
            continue
        card = by_dbf.get(dbf, {})
        name = card.get("name", f"dbfId {dbf}")
        rarity = str(card.get("rarity", "UNKNOWN")).upper()
        cost = rarity_cost(card)
        if cost == 0:
            free_missing += need
        line_dust = cost * need
        dust += line_dust
        missing_by_rarity[rarity] += need
        missing.append(
            {
                "dbfId": dbf,
                "name": name,
                "rarity": rarity,
                "need": need,
                "cost_each": cost,
                "dust": line_dust,
                "cost": card.get("cost"),
                "type": str(card.get("type", "")).upper(),
                "cardClass": str(card.get("cardClass", "")).upper(),
                "race": str(card.get("race", "")).upper() if card.get("race") else None,
                "mechanics": [str(m).upper() for m in card.get("mechanics", [])],
            }
        )

    missing.sort(key=lambda m: (-m["dust"], m["name"]))
    result = dict(deck)
    result.update(
        {
            "decoded_cards": decoded["cards"],
            "decoded_format": decoded.get("format") or decoded.get("format_id"),
            "total_cards": total_cards,
            "owned_cards": owned_cards,
            "percent_owned": round(100 * owned_cards / total_cards, 1) if total_cards else 0.0,
            "dust_needed": dust,
            "free_cards_missing": free_missing,
            "missing_by_rarity": dict(missing_by_rarity),
            "missing_legendaries": missing_by_rarity.get("LEGENDARY", 0),
            "missing_epics": missing_by_rarity.get("EPIC", 0),
            "missing": missing,
        }
    )
    return result


def build_owned_pool(owned: dict[int, int], by_dbf: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """Materialize owned cards into flat records for substitute matching.

    Returns a list of dicts with: dbfId, name, cardClass, type, cost (mana), race,
    mechanics, rarity, owned_count. All string fields are upper-cased.
    """
    pool: list[dict[str, Any]] = []
    for dbf, count in owned.items():
        if count <= 0:
            continue
        card = by_dbf.get(dbf)
        if not card:
            continue
        pool.append(
            {
                "dbfId": dbf,
                "name": card.get("name", f"dbfId {dbf}"),
                "cardClass": str(card.get("cardClass", "")).upper(),
                "type": str(card.get("type", "")).upper(),
                "cost": card.get("cost"),  # mana cost, not dust cost
                "race": str(card.get("race", "")).upper() if card.get("race") else None,
                "mechanics": [str(m).upper() for m in card.get("mechanics", [])],
                "rarity": str(card.get("rarity", "")).upper(),
                "owned_count": count,
            }
        )
    return pool


def find_substitutes(
    missing_entry: dict[str, Any],
    *,
    deck_class: str | None,
    decoded_cards: list[tuple[int, int]],
    owned_pool: list[dict[str, Any]],
    cost_window: int = 1,
    max_substitutes: int = 3,
) -> list[dict[str, Any]]:
    """Find owned cards that could substitute for a missing card.

    Filters by legality (class), type match, mana cost window, and copy headroom,
    then scores by tribe/mechanics overlap. Returns top N candidates sorted by
    score (highest first), or [] if no matches.

    Scores: +2 for matching race, +1 per shared mechanic.
    Tie-breaks: lower cost distance, then alphabetical name.
    """
    legal_classes = {"NEUTRAL"}
    if deck_class:
        legal_classes.add(deck_class.strip().upper())

    missing_type = missing_entry.get("type", "").upper()
    missing_cost = missing_entry.get("cost")
    missing_race = missing_entry.get("race")
    missing_mechanics = set(missing_entry.get("mechanics", []))

    deck_counts = dict(decoded_cards)
    missing_dbf = missing_entry.get("dbfId")

    candidates: list[tuple[int, int, dict[str, Any]]] = []

    for card in owned_pool:
        card_class = card.get("cardClass", "").upper()
        card_type = card.get("type", "").upper()

        if card_class and card_class not in legal_classes:
            continue
        if card_type != missing_type:
            continue

        card_cost = card.get("cost")
        if card_cost is not None and missing_cost is not None:
            if abs(card_cost - missing_cost) > cost_window:
                continue

        if card.get("dbfId") == missing_dbf:
            continue

        max_copies = 1 if card.get("rarity") == "LEGENDARY" else 2
        current_in_deck = deck_counts.get(card.get("dbfId"), 0)
        headroom = min(card.get("owned_count", 0), max_copies) - current_in_deck
        if headroom <= 0:
            continue

        card_mechanics = set(card.get("mechanics", []))
        score = 0
        if missing_race and card.get("race") == missing_race:
            score += 2
        score += len(missing_mechanics & card_mechanics)

        cost_distance = 0
        if card_cost is not None and missing_cost is not None:
            cost_distance = abs(card_cost - missing_cost)

        candidates.append((score, cost_distance, card))

    candidates.sort(key=lambda x: (-x[0], x[1], x[2].get("name", "")))

    # The same card name can exist under several dbfIds (Core vs. legacy
    # printings). Keep only the best-scoring printing per name so the top-N
    # suggestions are N distinct cards.
    results: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for score, _cost_distance, card in candidates:
        name = card.get("name", "")
        if name in seen_names:
            continue
        seen_names.add(name)
        results.append(
            {
                "dbfId": card["dbfId"],
                "name": card["name"],
                "cost": card["cost"],
                "rarity": card["rarity"],
                "race": card["race"],
                "mechanics": card["mechanics"],
                "match_score": score,
                "owned_count": card["owned_count"],
            }
        )
        if len(results) >= max_substitutes:
            break
    return results


def attach_substitutes(
    deck: dict[str, Any],
    owned_pool: list[dict[str, Any]],
    *,
    limit: int,
    cost_window: int = 1,
    max_substitutes: int = 3,
) -> None:
    """Mutate deck['missing'][:limit] in place, adding a 'substitutes' key.

    Only runs on the deck actually shown to the user, not on every ranked candidate.
    """
    deck_class = deck.get("class") or deck.get("hero_class")
    decoded_cards = deck.get("decoded_cards", [])

    for m in deck.get("missing", [])[:limit]:
        m["substitutes"] = find_substitutes(
            m,
            deck_class=deck_class,
            decoded_cards=decoded_cards,
            owned_pool=owned_pool,
            cost_window=cost_window,
            max_substitutes=max_substitutes,
        )


def _winrate(deck: dict[str, Any]) -> float:
    for key in ("winrate", "win_rate", "wr"):
        v = deck.get(key)
        if isinstance(v, (int, float)):
            return float(v)
    return -1.0


def rank(results: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    if sort == "value":
        # Cheapest, then highest winrate. Fully-owned decks float up.
        return sorted(results, key=lambda d: (d["dust_needed"], -_winrate(d)))
    if sort == "completion":
        return sorted(results, key=lambda d: (-d["percent_owned"], d["dust_needed"]))
    if sort == "meta":
        return sorted(results, key=_competitiveness_key)
    return sorted(results, key=lambda d: (d["dust_needed"], -_winrate(d)))


def format_report(results: list[dict[str, Any]], *, top_missing: int) -> str:
    lines: list[str] = []
    lines.append(f"{'#':>2}  {'Dust':>7}  {'Own%':>5}  {'Leg':>3} {'Epic':>4}  Deck")
    lines.append("-" * 72)
    for i, deck in enumerate(results, 1):
        wr = _winrate(deck)
        wr_str = f"  ({wr:.1f}% WR)" if wr >= 0 else ""
        cls = deck.get("class") or deck.get("hero_class") or ""
        cls_str = f" [{cls}]" if cls else ""
        lines.append(
            f"{i:>2}  {deck['dust_needed']:>7}  {deck['percent_owned']:>4.0f}%  "
            f"{deck['missing_legendaries']:>3} {deck['missing_epics']:>4}  "
            f"{deck.get('name', 'Deck')}{cls_str}{wr_str}"
        )
    lines.append("")
    lines.append("Legend: Dust = arcane dust to complete, Own% = cards you already have,")
    lines.append("        Leg/Epic = missing legendaries/epics.")

    if results:
        best = results[0]
        lines.append("")
        lines.append(f"Easiest to build: {best.get('name', 'Deck')} "
                     f"({best['dust_needed']} dust, {best['percent_owned']:.0f}% owned).")
        if best.get("free_cards_missing"):
            lines.append(f"  Note: {best['free_cards_missing']} missing card(s) are Core/free "
                         f"(earned by leveling, not crafting).")
        shown = best["missing"][:top_missing]
        if shown:
            lines.append("  Missing cards:")
            for m in shown:
                cost = f"{m['dust']} dust" if m["dust"] else "free (Core)"
                lines.append(f"    - {m['need']}x {m['name']} ({m['rarity'].title()}, {cost})")
                subs = m.get("substitutes", [])
                if subs:
                    sub_strs = [f"{s['name']} ({s['cost']}-mana {s['rarity'].title()})" for s in subs]
                    lines.append(f"        owned alternatives (attribute match only, not verified): {', '.join(sub_strs)}")
            if len(best["missing"]) > top_missing:
                lines.append(f"    ... and {len(best['missing']) - top_missing} more")
    return "\n".join(lines)

# --------------------------------------------------------------------------- #
# Recommendation summaries / visual tiers
# --------------------------------------------------------------------------- #
def _meta_rank(deck: dict[str, Any]) -> int:
    for key in ("meta_rank", "source_rank", "rank"):
        value = deck.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return 10_000


def _competitiveness_key(deck: dict[str, Any]) -> tuple[int, float, int]:
    """Sort key for "best" when we have only deck-site ordering.

    Lower source/meta rank means the deck appeared earlier in the fetched list.
    Higher win rate breaks ties when present; lower dust breaks final ties.
    """
    return (_meta_rank(deck), -_winrate(deck), deck.get("dust_needed", 0))


def choose_recommendations(
    results: list[dict[str, Any]],
    *,
    available_dust: int | None = None,
    close_dust: int = 3200,
) -> dict[str, dict[str, Any] | None]:
    if not results:
        return {"best_overall": None, "best_affordable": None, "best_close": None, "cheapest": None}
    best_overall = sorted(results, key=_competitiveness_key)[0]
    cheapest = sorted(results, key=lambda d: (d["dust_needed"], _meta_rank(d)))[0]
    affordable = [d for d in results if available_dust is not None and d["dust_needed"] <= available_dust]
    close = [d for d in results if d["dust_needed"] <= close_dust]
    return {
        "best_overall": best_overall,
        "best_affordable": sorted(affordable, key=_competitiveness_key)[0] if affordable else None,
        "best_close": sorted(close, key=_competitiveness_key)[0] if close else cheapest,
        "cheapest": cheapest,
    }


def _deck_line(deck: dict[str, Any] | None) -> str:
    if not deck:
        return "n/a"
    cls = deck.get("class") or deck.get("hero_class") or "?"
    rank_s = f"site #{_meta_rank(deck)}" if _meta_rank(deck) < 10_000 else "site rank n/a"
    legends = deck.get("missing_legendaries", 0)
    epics = deck.get("missing_epics", 0)
    return (
        f"{deck.get('name', 'Deck')} [{cls}] — {deck.get('dust_needed', '?')} dust, "
        f"{deck.get('percent_owned', 0):.0f}% owned, missing {legends} leg/{epics} epic ({rank_s})"
    )


def dust_tier(dust: int) -> tuple[str, str]:
    if dust == 0:
        return "✅ Ready now", "0 dust"
    if dust <= 1600:
        return "🟢 Easy craft", "1–1,600 dust"
    if dust <= 4000:
        return "🟡 Close", "1,601–4,000 dust"
    if dust <= 8000:
        return "🔵 Moderate", "4,001–8,000 dust"
    if dust <= 12000:
        return "🟠 Expensive", "8,001–12,000 dust"
    return "🔴 Very expensive", "12,001+ dust"


def tier_results(results: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    tiers: dict[str, list[dict[str, Any]]] = {}
    for deck in sorted(results, key=lambda d: (d["dust_needed"], _meta_rank(d))):
        label, _ = dust_tier(deck["dust_needed"])
        tiers.setdefault(label, []).append(deck)
    return tiers


def format_visual_report(
    results: list[dict[str, Any]],
    *,
    available_dust: int | None = None,
    close_dust: int = 3200,
    tier_limit: int = 4,
) -> str:
    picks = choose_recommendations(results, available_dust=available_dust, close_dust=close_dust)
    lines: list[str] = []
    lines.append("# Hearthstone deck recommendation")
    lines.append("")
    if available_dust is not None:
        lines.append(f"Available dust detected/provided: {available_dust}")
    lines.append(f"Close-craft threshold: {close_dust} dust")
    lines.append("")
    lines.append("## Picks")
    lines.append(f"🏆 Best overall from fetched meta sample: {_deck_line(picks['best_overall'])}")
    if available_dust is not None:
        lines.append(f"✅ Best deck you can afford: {_deck_line(picks['best_affordable'])}")
    lines.append(f"🎯 Best close/easy craft: {_deck_line(picks['best_close'])}")
    lines.append(f"💸 Cheapest deck: {_deck_line(picks['cheapest'])}")
    lines.append("")
    chosen = picks["best_close"] or picks["cheapest"] or picks["best_affordable"] or picks["best_overall"]
    if chosen:
        lines.append("## Suggested first build")
        lines.append(_deck_line(chosen))
        if chosen.get("free_cards_missing"):
            lines.append(f"Note: {chosen['free_cards_missing']} missing card(s) are Core/free unlocks, not craft dust.")
        missing = chosen.get("missing", [])[:10]
        if missing:
            lines.append("Missing highlights:")
            for m in missing:
                cost = f"{m['dust']} dust" if m.get("dust") else "free/Core"
                lines.append(f"  - {m['need']}x {m['name']} ({m['rarity'].title()}, {cost})")
                subs = m.get("substitutes", [])
                if subs:
                    sub_strs = [f"{s['name']} ({s['cost']}-mana {s['rarity'].title()})" for s in subs]
                    lines.append(f"      owned alternatives: {', '.join(sub_strs)}")
        lines.append("")
    lines.append("## Dust tiers")
    for label, decks in tier_results(results).items():
        _, range_label = dust_tier(decks[0]["dust_needed"])
        lines.append(f"{label} ({range_label})")
        for deck in decks[:tier_limit]:
            cls = deck.get("class") or deck.get("hero_class") or "?"
            lines.append(
                f"  - {deck.get('name', 'Deck')} [{cls}] — {deck['dust_needed']} dust, "
                f"{deck['percent_owned']:.0f}% owned, {_meta_rank(deck) if _meta_rank(deck) < 10_000 else 'n/a'} site rank"
            )
        if len(decks) > tier_limit:
            lines.append(f"  ... {len(decks) - tier_limit} more")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rank current Hearthstone meta decks by dust needed to complete.",
    )
    parser.add_argument("--collection", help="Path to your collection (HSReplay/HDT/Firestone export, or {dbfId: count})")
    parser.add_argument("--collection-url", help="URL returning collection JSON, such as the HSReplay account_lo JSON response")
    parser.add_argument("--collection-cookie-file", help="File holding the Cookie header for private collection URLs ('-' reads stdin); also settable via HS_COLLECTION_COOKIE_FILE or HS_COLLECTION_COOKIE")
    # Deprecated: a raw cookie on the command line leaks via shell history and
    # process listings. Kept hidden for backwards compatibility.
    parser.add_argument("--collection-cookie", help=argparse.SUPPRESS)
    parser.add_argument("--decks", required=True, help="Meta decks: JSON list of {name,class,deckstring,winrate?} or a text file of deck codes")
    parser.add_argument("--cards-json", help="Local HearthstoneJSON cards.collectible.json (avoids network)")
    parser.add_argument("--no-fetch", action="store_true", help="Do not fetch HearthstoneJSON")
    parser.add_argument("--sort", choices=["value", "dust", "completion", "meta"], default="value",
                        help="value/dust: cheapest first; completion: most-owned first; meta: fetched site order")
    parser.add_argument("--budget", type=int, help="Only show decks completable within this much dust")
    parser.add_argument("--max-results", type=int, default=15)
    parser.add_argument("--top-missing", type=int, default=8, help="How many missing cards to list for the top deck")
    parser.add_argument("--view", choices=["table", "visual", "both"], default="table", help="Text output style")
    parser.add_argument("--available-dust", type=int, help="Dust available for affordable/best-build recommendations")
    parser.add_argument("--close-dust", type=int, default=3200, help="Dust threshold for close/easy craft picks")
    parser.add_argument("--suggest-substitutes", action="store_true", help="Suggest owned cards as substitutes for missing cards")
    parser.add_argument("--substitute-cost-window", type=int, default=1, help="Mana cost window for substitute matching (default 1)")
    parser.add_argument("--max-substitutes", type=int, default=3, help="Max substitute suggestions per missing card (default 3)")
    parser.add_argument("--json", action="store_true", help="Emit full JSON instead of the text report")
    args = parser.parse_args(argv)

    try:
        if args.collection_cookie:
            print("WARNING: --collection-cookie is deprecated; use --collection-cookie-file "
                  "or the HS_COLLECTION_COOKIE env var to keep the cookie out of shell history.",
                  file=sys.stderr)
        cookie = resolve_collection_cookie(
            cookie_arg=args.collection_cookie,
            cookie_file=args.collection_cookie_file,
        )
        owned = load_collection_source(args.collection, args.collection_url, cookie=cookie)
        decks = load_decks(args.decks)
        if not decks:
            raise ValueError("No decks found in --decks input")
        by_dbf = index_cards(load_cards(args.cards_json, allow_fetch=not args.no_fetch))
        if not by_dbf:
            print("WARNING: no card data; dust costs/names unavailable. Provide --cards-json or allow fetch.", file=sys.stderr)

        results = []
        for i, d in enumerate(decks, 1):
            d = dict(d)
            d.setdefault("source_rank", i)
            results.append(evaluate_deck(d, owned, by_dbf))
        if args.budget is not None:
            results = [d for d in results if d["dust_needed"] <= args.budget]
        results = rank(results, args.sort)[: args.max_results]

        if args.suggest_substitutes and results:
            owned_pool = build_owned_pool(owned, by_dbf)
            attach_substitutes(
                results[0],
                owned_pool,
                limit=args.top_missing,
                cost_window=args.substitute_cost_window,
                max_substitutes=args.max_substitutes,
            )

        if args.json:
            print(json.dumps(results, indent=2))
        else:
            if args.view in {"visual", "both"}:
                print(format_visual_report(results, available_dust=args.available_dust, close_dust=args.close_dust))
            if args.view == "both":
                print("\n" + "=" * 72 + "\n")
            if args.view in {"table", "both"}:
                print(format_report(results, top_missing=args.top_missing))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
