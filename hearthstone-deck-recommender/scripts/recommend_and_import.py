#!/usr/bin/env python3
"""One-shot Hearthstone recommendation -> import block.

This is the tandem workflow between the recommender and deck-builder skills:

1. Load the user's collection from a file or collection JSON URL.
2. Get current meta deckstrings — from `--decks`, or fetched live from a public
   deck site when `--decks` is omitted (via the sibling `fetch_meta_decks.py`).
3. Rank them by dust needed to complete and pick the top recommendation.
4. Print the ranked summary plus a Hearthstone clipboard/import block.

Top-deck sites change HTML frequently. If the live fetch comes back empty, the
surrounding AI skill should browse current sites and save `meta_decks.json`,
then re-run with `--decks`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# Import rank_decks.py from the same directory regardless of cwd.
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from fetch_meta_decks import fetch_decks  # type: ignore  # noqa: E402
from rank_decks import (  # type: ignore  # noqa: E402
    attach_substitutes,
    build_owned_pool,
    choose_recommendations,
    evaluate_deck,
    format_report,
    format_visual_report,
    index_cards,
    load_cards,
    load_collection_source,
    load_decks,
    rank,
    resolve_collection_cookie,
)


def _deckstring(deck: dict[str, Any]) -> str:
    code = deck.get("deckstring") or deck.get("code")
    if not code:
        raise ValueError(f"Recommended deck {deck.get('name')!r} has no deckstring")
    return str(code).strip()


def detect_available_dust(collection_path: str | None) -> int | None:
    if not collection_path:
        return None
    try:
        with open(collection_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    dust = data.get("dust") if isinstance(data, dict) else None
    return int(dust) if isinstance(dust, (int, float)) else None


def choose_import_deck(
    ranked_all: list[dict[str, Any]],
    shown: list[dict[str, Any]],
    *,
    pick_policy: str,
    pick: int,
    available_dust: int | None,
    close_dust: int,
) -> dict[str, Any]:
    if pick_policy == "rank":
        if pick < 1 or pick > len(shown):
            raise ValueError(f"--pick must be between 1 and {len(shown)}")
        return shown[pick - 1]
    picks = choose_recommendations(ranked_all, available_dust=available_dust, close_dust=close_dust)
    mapping = {
        "close": picks["best_close"],
        "affordable": picks["best_affordable"],
        "overall": picks["best_overall"],
        "cheapest": picks["cheapest"],
    }
    chosen = mapping[pick_policy]
    if not chosen:
        chosen = picks["cheapest"] or picks["best_overall"]
    if not chosen:
        raise ValueError("No deck available to import")
    return chosen


def format_import_block(deck: dict[str, Any], *, top_missing: int) -> str:
    name = deck.get("name", "Recommended Hearthstone Deck")
    deck_class = deck.get("class") or deck.get("hero_class")
    fmt = deck.get("format") or deck.get("decoded_format") or "Standard"
    code = _deckstring(deck)

    lines = [f"### {name}"]
    if deck_class:
        lines.append(f"# Class: {deck_class}")
    if fmt:
        lines.append(f"# Format: {str(fmt).title()}")
    if "dust_needed" in deck:
        lines.append(f"# Dust needed from your collection: {deck['dust_needed']}")
    if "percent_owned" in deck:
        lines.append(f"# Collection completion: {deck['percent_owned']}%")
    lines.append("#")

    missing = list(deck.get("missing", []))[:top_missing]
    if missing:
        lines.append("# Missing to craft / unlock:")
        for m in missing:
            dust = f"{m['dust']} dust" if m.get("dust") else "free/Core"
            lines.append(f"# - {m.get('need', 1)}x {m.get('name', 'Unknown')} ({m.get('rarity', 'UNKNOWN')}, {dust})")
            subs = m.get("substitutes", [])
            if subs:
                sub_strs = [f"{s['name']} ({s['cost']}-mana {s['rarity'].title()})" for s in subs]
                lines.append(f"#     substitutes you own (unverified — pick by curve/synergy): {', '.join(sub_strs)}")
        lines.append("#")

    lines.append(code)
    lines.append("#")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rank Hearthstone meta decks for your collection and print the best deck's import block.",
    )
    parser.add_argument("--collection", help="Path to collection JSON/CSV")
    parser.add_argument("--collection-url", help="URL returning collection JSON, such as HSReplay's account_lo JSON response")
    parser.add_argument("--collection-cookie-file", help="File holding the Cookie header for private collection URLs ('-' reads stdin); also settable via HS_COLLECTION_COOKIE_FILE or HS_COLLECTION_COOKIE")
    # Deprecated: a raw cookie on the command line leaks via shell history and
    # process listings. Kept hidden for backwards compatibility.
    parser.add_argument("--collection-cookie", help=argparse.SUPPRESS)
    parser.add_argument("--decks", help="Meta decks JSON/text containing deckstrings; omit to fetch current Standard decks live from a public deck site")
    parser.add_argument("--fetch-limit", type=int, default=25, help="Max decks to collect when fetching live (no --decks)")
    parser.add_argument("--fetch-listing", action="append", help="Deck-site listing URL(s) for the live fetch (repeatable)")
    parser.add_argument("--cards-json", help="Local HearthstoneJSON cards.collectible.json")
    parser.add_argument("--no-fetch", action="store_true", help="Offline mode: fetch neither HearthstoneJSON card data nor live meta decks")
    parser.add_argument("--sort", choices=["value", "dust", "completion", "meta"], default="value")
    parser.add_argument("--budget", type=int, help="Only recommend decks completable within this much dust")
    parser.add_argument("--max-results", type=int, default=10)
    parser.add_argument("--top-missing", type=int, default=10)
    parser.add_argument("--available-dust", type=int, help="Dust available; defaults to dust in collection JSON when present")
    parser.add_argument("--close-dust", type=int, default=3200, help="Dust threshold for close/easy craft picks")
    parser.add_argument("--view", choices=["visual", "table", "both"], default="visual", help="Recommendation output style")
    parser.add_argument("--pick-policy", choices=["close", "affordable", "overall", "cheapest", "rank"], default="close", help="Which deck to print as the import block")
    parser.add_argument("--pick", type=int, default=1, help="1-based ranked deck to output when --pick-policy rank")
    parser.add_argument("--suggest-substitutes", action="store_true", help="Suggest owned cards as substitutes for missing cards")
    parser.add_argument("--substitute-cost-window", type=int, default=1, help="Mana cost window for substitute matching (default 1)")
    parser.add_argument("--max-substitutes", type=int, default=3, help="Max substitute suggestions per missing card (default 3)")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON with ranked decks and chosen import block")
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
        if args.decks:
            decks = load_decks(args.decks)
            if not decks:
                raise ValueError("No decks found in --decks input")
        elif args.no_fetch:
            raise ValueError("--decks is required when --no-fetch is set")
        else:
            print("No --decks given; fetching current Standard meta decks ...", file=sys.stderr)
            decks = fetch_decks(args.fetch_listing or None, limit=args.fetch_limit)
            if not decks:
                raise ValueError(
                    "Live meta-deck fetch found nothing (site layout may have changed); "
                    "assemble meta_decks.json by hand and pass --decks"
                )
            print(f"Fetched {len(decks)} deck(s).", file=sys.stderr)
        by_dbf = index_cards(load_cards(args.cards_json, allow_fetch=not args.no_fetch))
        if not by_dbf:
            print("WARNING: no card data; dust costs/names unavailable. Provide --cards-json or allow fetch.", file=sys.stderr)

        available_dust = args.available_dust if args.available_dust is not None else detect_available_dust(args.collection)
        ranked_all = []
        for i, d in enumerate(decks, 1):
            d = dict(d)
            d.setdefault("source_rank", i)
            ranked_all.append(evaluate_deck(d, owned, by_dbf))
        if args.budget is not None:
            ranked_all = [d for d in ranked_all if d["dust_needed"] <= args.budget]
        ranked = rank(ranked_all, args.sort)
        if not ranked:
            raise ValueError("No decks matched the budget/filter")

        shown = ranked[: args.max_results]
        chosen = choose_import_deck(
            ranked_all,
            shown,
            pick_policy=args.pick_policy,
            pick=args.pick,
            available_dust=available_dust,
            close_dust=args.close_dust,
        )
        if args.suggest_substitutes:
            owned_pool = build_owned_pool(owned, by_dbf)
            attach_substitutes(
                chosen,
                owned_pool,
                limit=args.top_missing,
                cost_window=args.substitute_cost_window,
                max_substitutes=args.max_substitutes,
            )
        import_block = format_import_block(chosen, top_missing=args.top_missing)

        if args.json:
            print(json.dumps({"chosen": chosen, "import_block": import_block, "ranked": shown}, indent=2))
        else:
            if args.view in {"visual", "both"}:
                print(format_visual_report(ranked_all, available_dust=available_dust, close_dust=args.close_dust))
            if args.view == "both":
                print("\n" + "=" * 72 + "\n")
            if args.view in {"table", "both"}:
                print(format_report(shown, top_missing=args.top_missing))
            print("\n" + "=" * 72)
            print(f"COPY THIS INTO HEARTHSTONE ({args.pick_policy.upper()} PICK)")
            print("=" * 72)
            print(import_block)
            print("\nHow to use: copy the deck code/import block, open Hearthstone, create a new deck, and accept the detected clipboard deck.")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
