"""Command-line interface: backfill, watch, stats."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import stats as stats_mod
from .capture import parse_power_log, power_logs
from .cards import HeroClassResolver
from .config import find_log_root, resolve_db_path, session_dirs
from .db import connect, save_games
from .decks import attach_decks, deck_logs, parse_decks_log

GAME_TYPE_ALIASES = {
    "ranked": "GT_RANKED",
    "casual": "GT_CASUAL",
    "arena": "GT_ARENA",
    "bg": "GT_BATTLEGROUNDS",
    "battlegrounds": "GT_BATTLEGROUNDS",
    "duos": "GT_BATTLEGROUNDS_DUO",
    "twist": "GT_RANKED",
}
FORMAT_ALIASES = {"standard": "FT_STANDARD", "wild": "FT_WILD", "twist": "FT_TWIST"}


def _game_type(value: str | None) -> str | None:
    if not value:
        return None
    return GAME_TYPE_ALIASES.get(value.lower(), value.upper())


def _format_type(value: str | None) -> str | None:
    if not value:
        return None
    return FORMAT_ALIASES.get(value.lower(), value.upper())


def cmd_backfill(args) -> int:
    log_root = find_log_root(args.logs_root)
    conn = connect(resolve_db_path(args.db))
    resolver = HeroClassResolver(allow_fetch=not args.no_fetch)
    total_found = total_new = 0
    for session in session_dirs(log_root):
        deck_events = [ev for path in deck_logs(session) for ev in parse_decks_log(path)]
        for path in power_logs(session):
            records = parse_power_log(path, resolver)
            attach_decks(records, deck_events)
            inserted = save_games(conn, records)
            total_found += len(records)
            total_new += inserted
            if records:
                print(f"{path.parent.name}/{path.name}: {len(records)} game(s), {inserted} new")
    print(f"\nBackfill complete: {total_found} game(s) found, {total_new} new.")
    return 0


def cmd_watch(args) -> int:
    from .watch import watch_loop

    log_root = find_log_root(args.logs_root)
    conn = connect(resolve_db_path(args.db))

    resolver = HeroClassResolver(allow_fetch=False)

    def on_games(records):
        inserted = save_games(conn, records)
        for r in records:
            dup = "" if inserted else " (already recorded)"
            if "BATTLEGROUNDS" in str(r.game_type or ""):
                hero = resolver.name(r.friendly_hero) or r.friendly_hero or "?"
                place = f"place {r.bg_place}/8" if r.bg_place else "place ?"
                tier = f" (tier {r.bg_tech})" if r.bg_tech else ""
                print(f"[{r.start_time}] {r.game_type} {hero}: {place}{tier}{dup}", flush=True)
                continue
            mine = r.deck_name or r.friendly_class or r.friendly_hero or "?"
            theirs = r.opponent_class or r.opponent_hero or "?"
            print(f"[{r.start_time}] {r.game_type or '?'} {mine} vs {theirs} "
                  f"({r.opponent_name or 'unknown'}): {r.result or '?'} in {r.turns or '?'} turns"
                  + dup, flush=True)

    print(f"Live capture started (db: {resolve_db_path(args.db)}). Ctrl-C to stop.", flush=True)
    try:
        watch_loop(log_root, on_games, interval=args.interval)
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


def _current_deck_counts(session_dir, resolver) -> dict[str, int] | None:
    """{card_id: copies} for the most recently queued deck, from Decks.log."""
    from .decks import decode_deckstring_counts

    events = [ev for path in deck_logs(session_dir) for ev in parse_decks_log(path)]
    if not events:
        return None
    counts: dict[str, int] = {}
    for dbf, n in decode_deckstring_counts(events[-1].deckstring).items():
        card_id = resolver.card_by_dbf(dbf).get("id")
        if card_id:
            counts[card_id] = n
    return counts or None


def cmd_live(args) -> int:
    import time

    from .config import DEFAULT_DB
    from .live import (
        LiveGameTail, format_snapshot, write_snapshot_json,
        snapshot_delta, pending_discovers,
    )

    log_root = find_log_root(args.logs_root)
    json_file = Path(args.json_file) if args.json_file else DEFAULT_DB.parent / "live.json"
    resolver = HeroClassResolver()

    print(f"Live game state (json: {json_file}). Ctrl-C to stop.", flush=True)
    tail: LiveGameTail | None = None
    current_dir = None
    last_marker = None  # (path, raw_turn, phase, game_over) last printed
    last_snap = None  # for snapshot_delta comparisons
    seen_discover_ids = set()  # to avoid re-printing the same pending choice
    deck_counts = None
    deck_game_no = -1
    snap_failing = False  # warn once (not every poll) if snapshots stop exporting

    while True:
        dirs = session_dirs(log_root)
        newest = dirs[-1] if dirs else None
        if newest and newest != current_dir:
            current_dir = newest
            tail = None
            last_snap = None
            seen_discover_ids.clear()
            print(f"watching {newest}", flush=True)
        path = current_dir / "Power.log" if current_dir else None
        if path and path.exists() and (tail is None or tail.path != path):
            tail = LiveGameTail(path)
        if tail and (tail.poll() or args.once):
            if tail.game_no != deck_game_no:
                # A new game began; re-read Decks.log for the deck just queued.
                deck_game_no = tail.game_no
                deck_counts = _current_deck_counts(current_dir, resolver)
                seen_discover_ids.clear()  # reset for the new game
                last_snap = None  # don't diff game N's opener against game N-1
                snap_failing = False
            snap = tail.snapshot(resolver, deck_counts=deck_counts)
            if not snap and last_snap is not None and not snap_failing:
                # The log is flowing but the game stopped exporting mid-game —
                # say so loudly instead of freezing the live view in silence.
                snap_failing = True
                print("!! Error: game state stopped exporting — live view is stale", flush=True)
            if snap:
                snap_failing = False
                write_snapshot_json(snap, json_file)
                marker = (str(tail.path), snap["raw_turn"], snap.get("phase"), snap.get("game_over"))

                # Tier 2: Check for pending unresolved Discovers (best-effort)
                if tail.last_tree and tail.last_friendly_id is not None:
                    try:
                        discovers = pending_discovers(tail.last_tree, resolver, tail.last_friendly_id)
                        for discover in discovers:
                            choice_id = discover["choice_id"]
                            if choice_id not in seen_discover_ids:
                                seen_discover_ids.add(choice_id)
                                options_str = " | ".join(
                                    opt["name"] for opt in discover.get("options", [])
                                )
                                print(
                                    f"== DISCOVER PENDING — {discover['source']}: {options_str}",
                                    flush=True
                                )
                    except Exception:
                        pass  # Silently skip pending_discovers errors

                if marker != last_marker:
                    last_marker = marker
                    print(format_snapshot(snap), flush=True)
                    last_snap = snap
                elif last_snap:
                    # Tier 1: Report mid-turn changes (hand/board)
                    delta_str = snapshot_delta(last_snap, snap)
                    if delta_str:
                        print(delta_str, flush=True)
                    last_snap = snap
                else:
                    last_snap = snap  # Initialize baseline

                if args.once:
                    return 0
        if args.once:
            print("no game in progress", flush=True)
            return 1
        time.sleep(args.interval)


def cmd_stats(args) -> int:
    conn = connect(resolve_db_path(args.db))
    gt, ft = _game_type(args.game_type), _format_type(args.format)
    view = args.view
    deck = getattr(args, "deck", None)

    sections = []
    if view in ("all", "overall"):
        sections.append(("Overall (by mode)", stats_mod.overall(conn, gt, ft)))
    if view in ("all", "deck"):
        sections.append(("Win rate by deck", stats_mod.by_deck(conn, gt, ft)))
    if view in ("all", "class"):
        sections.append(("Win rate by my class", stats_mod.by_class(conn, gt, ft)))
    if view in ("all", "matchup"):
        sections.append(("Matchups (my class vs theirs)",
                         stats_mod.matchups(conn, gt, ft, min_games=args.min_games)))
    if view in ("all", "first"):
        sections.append(("Going first vs second", stats_mod.first_vs_second(conn, gt, ft)))
    if view in ("all", "bg", "bg-heroes"):
        resolver = HeroClassResolver(allow_fetch=False)
        if view in ("all", "bg"):
            overall_bg = stats_mod.bg_overall(conn)
            if overall_bg or view == "bg":
                sections.append(("Battlegrounds", overall_bg))
                sections.append(("Battlegrounds heroes",
                                 stats_mod.resolve_card_names(
                                     stats_mod.bg_heroes(conn, min_games=args.min_games),
                                     resolver.name)))
        if view == "bg-heroes":
            sections.append(("Battlegrounds hero picks",
                             stats_mod.resolve_card_names(
                                 stats_mod.bg_hero_picks(conn, min_games=args.min_games),
                                 resolver.name)))
    if view in ("cards", "mulligan", "cut"):
        resolver = HeroClassResolver(allow_fetch=False)
        if view == "cards":
            rows = stats_mod.card_performance(conn, gt, ft, deck, min_games=args.min_games)
            sections.append(("Card performance (my cards)",
                             stats_mod.resolve_card_names(rows, resolver.name)))
        elif view == "mulligan":
            rows = stats_mod.mulligan(conn, gt, ft, deck, min_games=args.min_games)
            sections.append(("Mulligan (my keeps)",
                             stats_mod.resolve_card_names(rows, resolver.name)))
        else:  # view == "cut"
            rows = stats_mod.cut_candidates(conn, gt, ft, deck, min_games=args.min_games)
            sections.append(("Cut candidates (my cards)",
                             stats_mod.resolve_card_names(rows, resolver.name)))
    if view == "recent":
        sections.append(("Recent games", stats_mod.recent(conn, limit=args.limit)))

    filters = " ".join(f for f in (gt, ft, deck) if f)
    for title, rows in sections:
        print(f"## {title}" + (f"  [{filters}]" if filters else ""))
        print(stats_mod.format_table(rows))
        print()
    return 0


def main(argv=None) -> int:
    logging.disable(logging.WARNING)  # hslog is chatty about known log quirks

    parser = argparse.ArgumentParser(prog="hstracker", description=__doc__)
    parser.add_argument("--db", help="SQLite database path (default: ~/.local/share/hearthstone-tracker/games.db)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("backfill", help="Parse all existing Power logs into the database")
    p.add_argument("--logs-root", help="Hearthstone Logs directory (auto-detected by default)")
    p.add_argument("--no-fetch", action="store_true", help="Skip HearthstoneJSON download (class names may be missing)")
    p.set_defaults(func=cmd_backfill)

    p = sub.add_parser("watch", help="Tail the live Power.log and record games as they finish")
    p.add_argument("--logs-root", help="Hearthstone Logs directory (auto-detected by default)")
    p.add_argument("--interval", type=float, default=2.0, help="Poll interval in seconds")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("live", help="Tail the current game's state in real time (for live advice)")
    p.add_argument("--logs-root", help="Hearthstone Logs directory (auto-detected by default)")
    p.add_argument("--interval", type=float, default=1.0, help="Poll interval in seconds")
    p.add_argument("--json-file", help="Where to write the latest snapshot JSON (default: next to the DB)")
    p.add_argument("--once", action="store_true", help="Print one snapshot and exit")
    p.set_defaults(func=cmd_live)

    p = sub.add_parser("stats", help="Show win-rate stats")
    p.add_argument("view", nargs="?", default="all",
                   choices=["all", "overall", "deck", "class", "matchup", "first",
                            "cards", "mulligan", "cut", "bg", "bg-heroes", "recent"])
    p.add_argument("--game-type", help="e.g. ranked, casual, arena, bg, or a raw GT_* value")
    p.add_argument("--format", help="standard, wild, twist, or a raw FT_* value")
    p.add_argument("--deck", help="Filter cards/mulligan views to decks whose name contains this")
    p.add_argument("--min-games", type=int, default=1, help="Minimum games/samples per row")
    p.add_argument("--limit", type=int, default=20, help="Rows for the recent view")
    p.set_defaults(func=cmd_stats)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
