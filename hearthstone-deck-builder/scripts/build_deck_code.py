#!/usr/bin/env python3
"""Build and verify Hearthstone deck import codes.

The script is deliberately dependency-free so AI CLIs can use it from a copied
skill folder. It implements the Hearthstone deckstring varint/base64 encoding
used by common deck import codes and can resolve card names against
HearthstoneJSON's latest cards.collectible.json.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import textwrap
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

HJSON_LATEST_COLLECTIBLE = "https://api.hearthstonejson.com/v1/latest/enUS/cards.collectible.json"

FORMAT_IDS = {
    "wild": 1,
    "standard": 2,
    "classic": 3,
    "twist": 4,
}
FORMAT_NAMES = {value: key for key, value in FORMAT_IDS.items()}

# Stable default constructed heroes. These are used only when the input omits
# hero_dbf_id. Users can always override with an exact hero DBF ID.
DEFAULT_HERO_DBF_IDS = {
    "death knight": 78065,
    "demon hunter": 56550,
    "druid": 274,
    "hunter": 31,
    "mage": 637,
    "paladin": 671,
    "priest": 813,
    "rogue": 930,
    "shaman": 1066,
    "warlock": 893,
    "warrior": 7,
}

CLASS_NAMES = {name.lower(): name for name in DEFAULT_HERO_DBF_IDS}
CLASS_ALIASES = {
    "dk": "death knight",
    "dh": "demon hunter",
}

RARITY_LEGENDARY = "LEGENDARY"


@dataclass(frozen=True)
class CardRef:
    dbf_id: int
    count: int
    name: str | None = None
    owner_dbf_id: int | None = None
    owner_name: str | None = None


def normalize_name(value: str) -> str:
    return " ".join(value.casefold().replace("’", "'").split())


def normalize_class(value: str | None) -> str | None:
    if not value:
        return None
    v = normalize_name(value).replace("_", "-").replace(" ", " ")
    return CLASS_ALIASES.get(v, v)


def read_varint(data: bytes, offset: int) -> tuple[int, int]:
    shift = 0
    value = 0
    while True:
        if offset >= len(data):
            raise ValueError("unexpected end of data while reading varint")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte & 0x80 == 0:
            return value, offset
        shift += 7
        if shift > 63:
            raise ValueError("varint is too long")


def write_varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("varint cannot encode negative numbers")
    out = bytearray()
    while True:
        to_write = value & 0x7F
        value >>= 7
        if value:
            out.append(to_write | 0x80)
        else:
            out.append(to_write)
            return bytes(out)


def encode_deckstring(
    *,
    cards: list[tuple[int, int]],
    heroes: list[int],
    format_id: int,
    sideboards: list[tuple[int, int, int]] | None = None,
) -> str:
    """Encode a Hearthstone deckstring.

    cards: list of (dbf_id, count) for main-deck cards.
    heroes: list of hero DBF IDs, normally one.
    sideboards: optional list of (dbf_id, count, owner_dbf_id) entries used by
      cards such as E.T.C., Band Manager. The sideboard extension mirrors the
      public deckstrings library: a sideboard-present flag followed by the same
      single/double/n-copy buckets, with owner DBF IDs after each sideboard card.
    """
    by_count: dict[int, list[int]] = defaultdict(list)
    ncopy: list[tuple[int, int]] = []
    for dbf_id, count in sorted(cards):
        if count == 1:
            by_count[1].append(dbf_id)
        elif count == 2:
            by_count[2].append(dbf_id)
        else:
            ncopy.append((dbf_id, count))

    raw = bytearray()
    for value in (0, 1, format_id, len(heroes), *sorted(heroes)):
        raw += write_varint(value)

    raw += write_varint(len(by_count[1]))
    for dbf_id in sorted(by_count[1]):
        raw += write_varint(dbf_id)

    raw += write_varint(len(by_count[2]))
    for dbf_id in sorted(by_count[2]):
        raw += write_varint(dbf_id)

    raw += write_varint(len(ncopy))
    for dbf_id, count in sorted(ncopy):
        raw += write_varint(dbf_id)
        raw += write_varint(count)

    sideboards = sideboards or []
    if sideboards:
        raw += write_varint(1)
        side_by_count: dict[int, list[tuple[int, int]]] = defaultdict(list)
        side_ncopy: list[tuple[int, int, int]] = []
        for dbf_id, count, owner_dbf_id in sorted(sideboards, key=lambda x: (x[2], x[0], x[1])):
            if count == 1:
                side_by_count[1].append((dbf_id, owner_dbf_id))
            elif count == 2:
                side_by_count[2].append((dbf_id, owner_dbf_id))
            else:
                side_ncopy.append((dbf_id, count, owner_dbf_id))

        raw += write_varint(len(side_by_count[1]))
        for dbf_id, owner_dbf_id in sorted(side_by_count[1], key=lambda x: (x[1], x[0])):
            raw += write_varint(dbf_id)
            raw += write_varint(owner_dbf_id)

        raw += write_varint(len(side_by_count[2]))
        for dbf_id, owner_dbf_id in sorted(side_by_count[2], key=lambda x: (x[1], x[0])):
            raw += write_varint(dbf_id)
            raw += write_varint(owner_dbf_id)

        raw += write_varint(len(side_ncopy))
        for dbf_id, count, owner_dbf_id in sorted(side_ncopy, key=lambda x: (x[2], x[0])):
            raw += write_varint(dbf_id)
            raw += write_varint(count)
            raw += write_varint(owner_dbf_id)
    else:
        raw += write_varint(0)

    return base64.b64encode(bytes(raw)).decode("ascii")


def decode_deckstring(code: str) -> dict[str, Any]:
    code = "".join(code.strip().split())
    data = base64.b64decode(code)
    offset = 0
    reserved, offset = read_varint(data, offset)
    version, offset = read_varint(data, offset)
    format_id, offset = read_varint(data, offset)

    hero_count, offset = read_varint(data, offset)
    heroes = []
    for _ in range(hero_count):
        hero, offset = read_varint(data, offset)
        heroes.append(hero)

    cards: list[tuple[int, int]] = []
    single_count, offset = read_varint(data, offset)
    for _ in range(single_count):
        dbf_id, offset = read_varint(data, offset)
        cards.append((dbf_id, 1))

    double_count, offset = read_varint(data, offset)
    for _ in range(double_count):
        dbf_id, offset = read_varint(data, offset)
        cards.append((dbf_id, 2))

    ncopy_count, offset = read_varint(data, offset)
    for _ in range(ncopy_count):
        dbf_id, offset = read_varint(data, offset)
        count, offset = read_varint(data, offset)
        cards.append((dbf_id, count))

    sideboards: list[tuple[int, int, int]] = []
    if offset < len(data):
        has_sideboard, offset = read_varint(data, offset)
        if has_sideboard == 1:
            for bucket_count in (1, 2, None):
                entry_count, offset = read_varint(data, offset)
                for _ in range(entry_count):
                    dbf_id, offset = read_varint(data, offset)
                    if bucket_count is None:
                        count, offset = read_varint(data, offset)
                    else:
                        count = bucket_count
                    owner, offset = read_varint(data, offset)
                    sideboards.append((dbf_id, count, owner))
        elif has_sideboard != 0:
            raise ValueError(f"unsupported sideboard flag {has_sideboard}")

    if offset != len(data):
        raise ValueError(f"{len(data) - offset} undecoded bytes remain")

    return {
        "reserved": reserved,
        "version": version,
        "format_id": format_id,
        "format": FORMAT_NAMES.get(format_id, str(format_id)),
        "heroes": heroes,
        "cards": sorted(cards),
        "sideboards": sideboards,
    }


# Cap on the fetched HearthstoneJSON payload, so a misbehaving or hostile
# endpoint cannot exhaust memory. The real card set is ~15 MB.
MAX_CARDS_RESPONSE_BYTES = 50 * 1024 * 1024


def load_cards(path: str | None, *, allow_fetch: bool) -> list[dict[str, Any]]:
    if path:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    if not allow_fetch:
        return []
    request = urllib.request.Request(
        HJSON_LATEST_COLLECTIBLE,
        headers={"User-Agent": "hearthstone-deck-builder-skill/1.0 (+https://hearthstonejson.com)"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected -- HJSON_LATEST_COLLECTIBLE is a module constant
        data = response.read(MAX_CARDS_RESPONSE_BYTES + 1)
    if len(data) > MAX_CARDS_RESPONSE_BYTES:
        raise ValueError(
            f"HearthstoneJSON response exceeded the {MAX_CARDS_RESPONSE_BYTES // (1024 * 1024)} MB size limit; aborting"
        )
    return json.loads(data.decode("utf-8"))


def build_indexes(cards: Iterable[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], dict[int, dict[str, Any]]]:
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_dbf: dict[int, dict[str, Any]] = {}
    for card in cards:
        dbf_id = card.get("dbfId")
        name = card.get("name")
        if isinstance(dbf_id, int):
            by_dbf[dbf_id] = card
        if name:
            by_name[normalize_name(name)].append(card)
    return by_name, by_dbf


def choose_card(
    *,
    item: dict[str, Any],
    by_name: dict[str, list[dict[str, Any]]],
    expected_class: str | None,
    label: str,
) -> tuple[int, str | None, dict[str, Any] | None]:
    for key in ("dbfId", "dbf_id"):
        if key in item:
            return int(item[key]), item.get("name"), None
    name = item.get("name") or item.get("card")
    if not name:
        raise ValueError(f"{label} is missing name/dbfId: {item!r}")
    matches = by_name.get(normalize_name(str(name)), [])
    if not matches:
        raise ValueError(f"Could not resolve {label} name {name!r}; supply dbfId or a current cards JSON")

    expected = normalize_class(expected_class)
    filtered = []
    for card in matches:
        card_class = normalize_class(str(card.get("cardClass", "")))
        if not expected or card_class in {expected, "neutral"}:
            filtered.append(card)
    if filtered:
        matches = filtered

    # If duplicates are exactly the same DBF ID after filtering, collapse them.
    unique: dict[int, dict[str, Any]] = {int(c["dbfId"]): c for c in matches if isinstance(c.get("dbfId"), int)}
    if len(unique) == 1:
        card = next(iter(unique.values()))
        return int(card["dbfId"]), card.get("name"), card

    details = ", ".join(
        f"{c.get('name')} dbfId={c.get('dbfId')} id={c.get('id')} set={c.get('set')} class={c.get('cardClass')}"
        for c in matches[:12]
    )
    raise ValueError(
        f"Ambiguous {label} name {name!r}; supply dbfId/card ID. Candidates: {details}"
    )


def parse_card_list(value: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for raw in value.replace("\n", ";").split(";"):
        raw = raw.strip()
        if not raw:
            continue
        count = 1
        name = raw
        if "x " in raw[:5].lower():
            left, right = raw.split("x", 1)
            if left.strip().isdigit():
                count = int(left.strip())
                name = right.strip()
        elif raw[:2].isdigit() and raw[2:3] == " ":
            count = int(raw[:2])
            name = raw[3:].strip()
        elif raw[:1].isdigit() and raw[1:2] == " ":
            count = int(raw[:1])
            name = raw[2:].strip()
        items.append({"name": name, "count": count})
    return items


def parse_dbf_cards(value: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for raw in value.split(","):
        raw = raw.strip()
        if not raw:
            continue
        if ":" in raw:
            dbf_id, count = raw.split(":", 1)
        elif "x" in raw.lower():
            count, dbf_id = raw.lower().split("x", 1)
        else:
            dbf_id, count = raw, "1"
        items.append({"dbfId": int(dbf_id.strip()), "count": int(count.strip())})
    return items


def load_deck_input(args: argparse.Namespace) -> dict[str, Any]:
    deck: dict[str, Any] = {}
    if args.input:
        with open(args.input, "r", encoding="utf-8") as f:
            deck.update(json.load(f))
    if args.deck_name:
        deck["name"] = args.deck_name
    if args.deck_class:
        deck["class"] = args.deck_class
    if args.format:
        deck["format"] = args.format
    if args.hero_dbf_id:
        deck["hero_dbf_id"] = args.hero_dbf_id
    if args.cards:
        deck["cards"] = parse_card_list(args.cards)
    if args.dbf_cards:
        deck["cards"] = parse_dbf_cards(args.dbf_cards)
    return deck


def validate_counts(card_refs: list[CardRef], by_dbf: dict[int, dict[str, Any]], deck_size: str) -> list[str]:
    warnings: list[str] = []
    total = sum(c.count for c in card_refs)
    if deck_size != "none":
        expected = int(deck_size)
        if total != expected:
            warnings.append(f"main deck has {total} cards; expected {expected}")

    counts = Counter({c.dbf_id: c.count for c in card_refs})
    for dbf_id, count in sorted(counts.items()):
        card = by_dbf.get(dbf_id, {})
        rarity = card.get("rarity")
        name = card.get("name", str(dbf_id))
        limit = 1 if rarity == RARITY_LEGENDARY else 2
        if count > limit:
            warnings.append(f"{name} dbfId={dbf_id} has {count} copies; usual constructed limit is {limit}")
    return warnings


def format_import_block(
    *,
    name: str,
    deck_class: str | None,
    format_name: str,
    card_refs: list[CardRef],
    by_dbf: dict[int, dict[str, Any]],
    code: str,
) -> str:
    lines = [f"### {name}"]
    if deck_class:
        lines.append(f"# Class: {deck_class.title()}")
    lines.append(f"# Format: {format_name.title()}")
    lines.append("#")
    for ref in sorted(card_refs, key=lambda r: (by_dbf.get(r.dbf_id, {}).get("cost", 99), by_dbf.get(r.dbf_id, {}).get("name") or r.name or str(r.dbf_id))):
        card = by_dbf.get(ref.dbf_id, {})
        card_name = card.get("name") or ref.name or f"dbfId {ref.dbf_id}"
        cost = card.get("cost")
        if isinstance(cost, int):
            lines.append(f"# {ref.count}x ({cost}) {card_name}")
        else:
            lines.append(f"# {ref.count}x {card_name}")
    lines.extend(["#", code, "#"])
    return "\n".join(lines)


def copy_to_clipboard(text: str) -> bool:
    commands = []
    if shutil.which("pbcopy"):
        commands.append(["pbcopy"])
    if shutil.which("wl-copy"):
        commands.append(["wl-copy"])
    if shutil.which("xclip"):
        commands.append(["xclip", "-selection", "clipboard"])
    if shutil.which("clip.exe"):
        commands.append(["clip.exe"])
    if os.name == "nt" and shutil.which("clip"):
        commands.append(["clip"])
    for cmd in commands:
        try:
            subprocess.run(cmd, input=text, text=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            continue
    return False


def build(args: argparse.Namespace) -> int:
    deck = load_deck_input(args)
    if not deck.get("cards"):
        raise ValueError("No cards supplied. Use --input, --cards, or --dbf-cards.")

    allow_fetch = not args.no_fetch
    cards_data = load_cards(args.cards_json, allow_fetch=allow_fetch)
    by_name, by_dbf = build_indexes(cards_data)

    deck_class = normalize_class(deck.get("class"))
    format_name = normalize_name(str(deck.get("format", "standard")))
    if format_name not in FORMAT_IDS:
        raise ValueError(f"Unsupported format {format_name!r}; choose {', '.join(FORMAT_IDS)}")
    format_id = FORMAT_IDS[format_name]

    hero_dbf = deck.get("hero_dbf_id") or deck.get("heroDbfId") or deck.get("hero_dbfId")
    if hero_dbf is None:
        if not deck_class:
            raise ValueError("Deck class or hero_dbf_id is required")
        if deck_class not in DEFAULT_HERO_DBF_IDS:
            raise ValueError(f"Unknown class {deck_class!r}; supply hero_dbf_id")
        hero_dbf = DEFAULT_HERO_DBF_IDS[deck_class]
    hero_dbf = int(hero_dbf)

    card_refs: list[CardRef] = []
    for item in deck["cards"]:
        dbf_id, resolved_name, card = choose_card(item=item, by_name=by_name, expected_class=deck_class, label="card")
        count = int(item.get("count", 1))
        card_refs.append(CardRef(dbf_id=dbf_id, count=count, name=resolved_name or item.get("name")))
        if card and deck_class:
            cclass = normalize_class(str(card.get("cardClass", "")))
            if cclass not in {deck_class, "neutral"}:
                print(f"WARNING: {card.get('name')} is class {card.get('cardClass')} in a {deck_class} deck", file=sys.stderr)

    sideboard_refs: list[CardRef] = []
    raw_sideboards = list(deck.get("sideboard_cards", []))
    for group in deck.get("sideboards", []):
        owner = group.get("owner") or group.get("owner_name") or group.get("ownerName")
        owner_dbf = group.get("owner_dbf_id") or group.get("ownerDbfId") or group.get("owner_dbfId")
        for card in group.get("cards", []):
            merged = dict(card)
            if owner and "owner" not in merged:
                merged["owner"] = owner
            if owner_dbf and "owner_dbf_id" not in merged:
                merged["owner_dbf_id"] = owner_dbf
            raw_sideboards.append(merged)

    for item in raw_sideboards:
        owner_dbf = item.get("owner_dbf_id") or item.get("ownerDbfId") or item.get("owner_dbfId")
        owner_name = item.get("owner") or item.get("owner_name") or item.get("ownerName")
        if owner_dbf is None:
            if not owner_name:
                raise ValueError(f"sideboard entry needs owner/owner_dbf_id: {item!r}")
            owner_dbf, _, _ = choose_card(item={"name": owner_name}, by_name=by_name, expected_class=deck_class, label="sideboard owner")
        dbf_id, resolved_name, _ = choose_card(item=item, by_name=by_name, expected_class=deck_class, label="sideboard card")
        sideboard_refs.append(
            CardRef(dbf_id=dbf_id, count=int(item.get("count", 1)), name=resolved_name or item.get("name"), owner_dbf_id=int(owner_dbf), owner_name=owner_name)
        )

    deck_size = str(args.deck_size)
    if deck_size == "auto":
        deck_size = "40" if any((by_dbf.get(c.dbf_id, {}).get("name") or c.name) == "Prince Renathal" for c in card_refs) else "30"
    warnings = validate_counts(card_refs, by_dbf, deck_size)

    code = encode_deckstring(
        cards=[(c.dbf_id, c.count) for c in card_refs],
        heroes=[hero_dbf],
        format_id=format_id,
        sideboards=[(c.dbf_id, c.count, int(c.owner_dbf_id)) for c in sideboard_refs],
    )
    decoded = decode_deckstring(code)
    expected_cards = sorted((c.dbf_id, c.count) for c in card_refs)
    if decoded["cards"] != expected_cards:
        raise AssertionError(f"round-trip mismatch: {decoded['cards']} != {expected_cards}")

    name = str(deck.get("name") or f"{(deck_class or 'Hearthstone').title()} Deck")
    block = format_import_block(name=name, deck_class=deck_class, format_name=format_name, card_refs=card_refs, by_dbf=by_dbf, code=code)

    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    print(block)
    print("\nDeck code only:")
    print(code)
    if args.copy:
        if copy_to_clipboard(block):
            print("\nCopied import block to clipboard.", file=sys.stderr)
        else:
            print("\nWARNING: Could not find a clipboard command; copy the import block manually.", file=sys.stderr)
    return 0


def selftest() -> int:
    # Public deckstrings README example: format wild, hero 7, cards 1/2/3 at two
    # copies and card 4 at one copy. This guards the varint/base64 layout.
    code = encode_deckstring(cards=[(1, 2), (2, 2), (3, 2), (4, 1)], heroes=[7], format_id=1)
    assert code == "AAEBAQcBBAMBAgMAAA==", code
    decoded = decode_deckstring(code)
    assert decoded["cards"] == [(1, 2), (2, 2), (3, 2), (4, 1)]
    assert decoded["heroes"] == [7]
    assert decoded["format"] == "wild"
    # Current public deckstrings README example with a sideboard card:
    # cards [[1,2],[2,2],[3,2],[4,1]], hero [7], sideboard [[5,1,90749]].
    side = encode_deckstring(cards=[(1, 2), (2, 2), (3, 2), (4, 1)], heroes=[7], format_id=1, sideboards=[(5, 1, 90749)])
    assert side == "AAEBAQcBBAMBAgMAAQEF/cQFAAA=", side
    assert decode_deckstring(side)["sideboards"] == [(5, 1, 90749)]
    print("selftest ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Build Hearthstone deck import codes from JSON/card lists.",
        epilog=textwrap.dedent(
            """
            Examples:
              %(prog)s --input deck.json
              %(prog)s --deck-name Test --class Warrior --format wild --dbf-cards 1:2,2:2,3:2,4:1 --deck-size none --no-fetch
              %(prog)s --decode AAEBAQcBBAMBAgMA
            """
        ),
    )
    parser.add_argument("--input", help="JSON deck file")
    parser.add_argument("--deck-name")
    parser.add_argument("--class", dest="deck_class")
    parser.add_argument("--format", choices=sorted(FORMAT_IDS), help="Constructed format")
    parser.add_argument("--hero-dbf-id", type=int, help="Hero DBF ID override")
    parser.add_argument("--cards", help="Semicolon/newline-separated list like '2x Fireball; 1x Zilliax'")
    parser.add_argument("--dbf-cards", help="Comma-separated DBF list like '123:2,456:1' for offline use")
    parser.add_argument("--cards-json", help="Local HearthstoneJSON cards.collectible.json")
    parser.add_argument("--no-fetch", action="store_true", help="Do not fetch HearthstoneJSON if card names need resolving")
    parser.add_argument("--deck-size", default="auto", help="Expected main deck size: auto, 30, 40, or none")
    parser.add_argument("--copy", action="store_true", help="Try to copy the import block to the clipboard")
    parser.add_argument("--decode", help="Decode a deck code and print JSON")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.selftest:
            return selftest()
        if args.decode:
            print(json.dumps(decode_deckstring(args.decode), indent=2, sort_keys=True))
            return 0
        return build(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
