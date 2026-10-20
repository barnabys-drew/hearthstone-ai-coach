"""Command-line interface: backfill, watch, live, stats, rag-report, rag-replay."""
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


def _current_deck(session_dir, resolver) -> tuple[dict[str, int] | None, str | None, int | None]:
    """(counts {card_id: copies}, name, FormatType id) of the most recently
    queued deck, from ONE pass over the session's Decks.log files: this runs
    at every game start, right when the mulligan wants low latency."""
    from .decks import decode_deckstring_counts, deckstring_format

    events = [ev for path in deck_logs(session_dir) for ev in parse_decks_log(path)]
    if not events:
        return None, None, None
    last = events[-1]
    counts: dict[str, int] = {}
    for dbf, n in decode_deckstring_counts(last.deckstring).items():
        card_id = resolver.card_by_dbf(dbf).get("id")
        if card_id:
            counts[card_id] = n
    return counts or None, last.name, deckstring_format(last.deckstring)


def _record_game_and_refresh_stats(session_dir, resolver, overlay_dir) -> list | None:
    """On game end: persist the finished game and refresh the stats panel.

    Best-effort — the live loop must never die over stats bookkeeping.
    Returns the records parsed from the newest Power.log so callers (rag
    telemetry) can attach outcome details; None on failure or startup call.
    """
    try:
        from .db import connect, save_games
        from .deckstats import write_deck_stats

        last_records = None
        conn = connect(resolve_db_path(None))
        if session_dir:
            deck_events = [ev for path in deck_logs(session_dir) for ev in parse_decks_log(path)]
            for path in power_logs(session_dir):
                records = parse_power_log(path, resolver)
                attach_decks(records, deck_events)
                save_games(conn, records)
                if records:
                    last_records = records
        write_deck_stats(conn, overlay_dir)
        conn.close()
        return last_records
    except Exception as exc:
        print(f"!! deck stats refresh failed: {exc}", flush=True)
        return None


def cmd_live(args) -> int:
    import time

    from .config import DEFAULT_DB
    from .live import (
        LiveGameTail, format_snapshot, write_snapshot_json,
        snapshot_delta, pending_discovers,
    )
    from .budget import assemble, budget_enabled
    from .embed import T2Retriever, t2_live_enabled
    from .choices import ModeWatcher, effective_mode, status_line, uses_jev
    from .jev import advisor_from_env, hand_from_snapshot
    from .lessons import StoreWatcher, mirror_store
    from .lexical import retrieve_lessons, t1_live_enabled
    from .overlay import mirror_live_snapshot, resolve_overlay_dir
    from .raglog import RagTurnLogger, lesson_id

    log_root = find_log_root(args.logs_root)
    json_file = Path(args.json_file) if args.json_file else DEFAULT_DB.parent / "live.json"
    overlay_dir = resolve_overlay_dir(args.overlay_dir)
    resolver = HeroClassResolver()
    lesson_store = StoreWatcher()  # mtime-cached; new lessons picked up mid-game
    rag = RagTurnLogger()  # retrieval telemetry (progressive-RAG Phase 1)
    # Tier 2 (semantic fallback) is lab-gated: HS_RAG_T2=1 opts in. The
    # retriever embeds one query per game (at its first snapshot); per-turn
    # matching is pure dot products over vectors cached by `hst rag-embed`.
    t2 = T2Retriever() if t2_live_enabled() else None
    # Who answers mulligans and Discovers is picked in the overlay
    # (settings.json: jev | coach | both | off, see choices.py) and re-read
    # whenever it changes. Jev calls run on background threads; their answers
    # land on the overlay and as `== JEV` feed lines.
    jev = advisor_from_env(overlay_dir)
    choices = ModeWatcher(overlay_dir)
    jev_mulligan_done: set[tuple] = set()  # (session, game_no) already asked
    mirror_store()  # give the overlay lessons panel the structured store at startup
    _record_game_and_refresh_stats(None, resolver, overlay_dir)  # stats panel at startup

    print(f"Live game state (json: {json_file}). Ctrl-C to stop.", flush=True)
    if overlay_dir:
        print(f"Overlay mirror: {overlay_dir}", flush=True)
    print(status_line(choices.mode, jev is not None), flush=True)
    tail: LiveGameTail | None = None
    current_dir = None
    last_marker = None  # (path, raw_turn, phase, game_over) last printed
    last_snap = None  # for snapshot_delta comparisons
    seen_discover_ids = set()  # to avoid re-printing the same pending choice
    deck_counts = None
    deck_name = None
    deck_format = None
    deck_game_no = -1
    snap_failing = False  # warn once (not every poll) if snapshots stop exporting

    while True:
        mode, mode_changed = choices.poll()
        if mode_changed:
            print(status_line(mode, jev is not None), flush=True)
        choice_mode = effective_mode(mode, jev is not None)
        if jev:
            for line in jev.drain():
                print(line, flush=True)
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
                deck_counts, deck_name, deck_format = _current_deck(current_dir, resolver)
                # Stats panel follows the deck you QUEUED with, from game start.
                try:
                    from .db import connect
                    from .deckstats import write_deck_stats
                    conn = connect(resolve_db_path(None))
                    write_deck_stats(conn, overlay_dir, deck_name)
                    conn.close()
                except Exception:
                    pass
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
                # Tier 1 (lexical fallback) is lab-gated: HS_RAG_T1=1 opts in.
                if t2 is not None:
                    t2.prime(snap, tail.game_no)  # once per game, not per turn
                # Phase 5 (lab-gated, HS_RAG_BUDGET=1): over-fetch, then let
                # the evidence ranker cut the list to the context budget.
                budgeting = budget_enabled()
                results, tiers_ran = retrieve_lessons(
                    snap, lesson_store.lessons(), t1_enabled=t1_live_enabled(),
                    t2=t2, cap=6 if budgeting else 3)
                budget_info = None
                if budgeting and results:
                    results, dropped, spent = assemble(results)
                    budget_info = {"chars": spent, "dropped": dropped}
                if deck_name:
                    # The overlay lessons panel filters deck-tagged tips by
                    # the deck actually being played (real complaint: Aya
                    # Rogue tips shown all through a Two Bit Rogue session).
                    snap["deck"] = deck_name
                if results:
                    snap["lessons_matched"] = [
                        {"lesson": r["lesson"].lesson, "cost": r["lesson"].cost,
                         "id": lesson_id(r["lesson"].lesson), "tier": r["tier"]}
                        for r in results
                    ]
                rag.on_snapshot(snap, results, lesson_store.lessons(),
                                session=current_dir.name, game_no=tail.game_no,
                                tiers_ran=tiers_ran, budget_info=budget_info)
                write_snapshot_json(snap, json_file)
                if overlay_dir:
                    try:
                        mirror_live_snapshot(snap, overlay_dir)
                    except OSError as exc:
                        print(f"!! Overlay mirror failed: {exc}", flush=True)
                marker = (str(tail.path), snap["raw_turn"], snap.get("phase"), snap.get("game_over"))

                game_key = (current_dir.name, tail.game_no)
                if (jev and uses_jev(choice_mode) and snap.get("phase") == "mulligan"
                        and game_key not in jev_mulligan_done):
                    hand = hand_from_snapshot(
                        snap, resolver, deck_counts, deck_name,
                        tail.meta.get("FormatType") or deck_format, tail.meta.get("GameType"))
                    if hand:
                        jev_mulligan_done.add(game_key)
                        jev.mulligan(hand, publish=choice_mode == "jev", mode=choice_mode,
                                     session=current_dir.name, game_no=tail.game_no)

                # Tier 2: Check for pending unresolved Discovers (best-effort)
                if tail.last_tree and tail.last_friendly_id is not None:
                    try:
                        discovers = pending_discovers(tail.last_tree, resolver, tail.last_friendly_id)
                        for discover in discovers:
                            choice_id = discover["choice_id"]
                            if choice_id not in seen_discover_ids:
                                seen_discover_ids.add(choice_id)
                                options_str = " | ".join(
                                    f"{opt['name']}({opt.get('cost', '?')})"
                                    + (f" [{opt['text'][:90]}]" if opt.get("text") else "")
                                    for opt in discover.get("options", [])
                                )
                                print(
                                    f"== DISCOVER PENDING [choices: {choice_mode}] — "
                                    f"{discover['source']}: {options_str}",
                                    flush=True
                                )
                                if jev and uses_jev(choice_mode):
                                    jev.discover(snap, discover, publish=choice_mode == "jev",
                                                 mode=choice_mode, session=current_dir.name,
                                                 game_no=tail.game_no)
                    except Exception:
                        pass  # Silently skip pending_discovers errors

                if marker != last_marker:
                    # Game over with no turn change: the board already printed,
                    # so a full re-print would be an identical block with one
                    # new line buried at the bottom. Print just the verdict.
                    if (
                        last_marker is not None
                        and marker[:3] == last_marker[:3]
                        and snap.get("game_over")
                    ):
                        print(f"== GAME OVER: {snap['game_over']}", flush=True)
                        records = _record_game_and_refresh_stats(current_dir, resolver, overlay_dir)
                        rag.on_game_over(snap, records[-1] if records else None,
                                         session=current_dir.name,
                                         game_no=tail.game_no, deck_name=deck_name)
                        last_marker = marker
                        last_snap = snap
                        if args.once:
                            return 0
                        time.sleep(args.interval)
                        continue
                    # raw_turn increments once per turn taken by either side, but
                    # the displayed turn number pairs one raw_turn from each side
                    # into a shared "TURN N" label. An extra-turn effect (same
                    # side goes twice) advances raw_turn without advancing that
                    # shared label — the header would look identical to the
                    # previous print (e.g. two "TURN 8 (opponent's turn)" rows)
                    # even though a full turn's worth of new events happened.
                    if (
                        last_snap is not None
                        and snap.get("turn") == last_snap.get("turn")
                        and snap.get("whose_turn") == last_snap.get("whose_turn")
                        and snap.get("raw_turn") != last_snap.get("raw_turn")
                    ):
                        print(
                            f"== EXTRA TURN — {('you' if snap.get('whose_turn') == 'me' else 'opponent')} "
                            f"went again (raw turn {last_snap.get('raw_turn')} -> {snap.get('raw_turn')})",
                            flush=True,
                        )
                    last_marker = marker
                    text = format_snapshot(snap)
                    if snap.get("phase") == "mulligan":
                        # The mode rides on the decision line itself, so the
                        # coach never has to remember an earlier mode line.
                        text = text.replace("== MULLIGAN —", f"== MULLIGAN [choices: {choice_mode}] —", 1)
                    print(text, flush=True)
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


def cmd_rag_report(args) -> int:
    from . import raglog
    from .lessons import load_store

    events = raglog.read_events(Path(args.log) if args.log else None)
    if args.days:
        import time
        cutoff = time.time() - args.days * 86400
        events = [ev for ev in events if (ev.get("ts") or 0) >= cutoff]
    if not events:
        print("No retrieval telemetry yet — play a game with `hst live` running.")
        return 0

    store = load_store()
    games = raglog.join_games(events)

    precision, has_applied = raglog.precision_rows(games, store)
    sections = [
        ("Summary", raglog.summary_rows(games)),
        ("Tier earnings", raglog.tier_rows(games)),
        ("Per-lesson firing", raglog.fire_rows(games, store)),
        ("Dead knowledge (never fired)", raglog.dead_rows(games, store)),
        ("Retrieval misses (misplay recorded, nothing fired)", raglog.miss_rows(games)),
        ("Precision proxy" if has_applied
         else "Precision proxy (no applied events — proxy is fired∧won)", precision),
    ]
    budgeted = [ev for ev in events
                if ev.get("ev") == "match" and ev.get("context")]
    if budgeted:
        chars = [ev["context"].get("chars", 0) for ev in budgeted]
        drops = sum(len(ev["context"].get("dropped") or []) for ev in budgeted)
        sections.append(("Context budget (Phase 5)", [{
            "budgeted_turns": len(budgeted),
            "avg_chars": round(sum(chars) / len(chars)),
            "max_chars": max(chars),
            "lessons_dropped": drops,
        }]))
    for title, rows in sections:
        print(f"## {title}")
        print(stats_mod.format_table(rows))
        print()
    stray = raglog.unjoined_counts(games)
    if stray["applied"] or stray["ingested"]:
        print(f"(unjoined events: {stray['applied']} applied, {stray['ingested']} ingested "
              f"— outside their join windows)")
    return 0


def cmd_rag_replay(args) -> int:
    import json

    from .lessons import load_store
    from .ragreplay import replay_session, replay_report

    session = Path(args.session_dir)
    if not session.is_absolute():
        session = find_log_root(args.logs_root) / args.session_dir
    if not session.is_dir():
        print(f"ERROR: no such session directory: {session}", file=sys.stderr)
        return 2

    store = load_store()
    resolver = HeroClassResolver()
    if args.tier0_only:
        tiers = ("t0",)
    elif args.t2:
        from .embed import CACHE_PATH, Embedder, load_cache
        if not load_cache():
            print(f"ERROR: no embedding cache at {CACHE_PATH} — run `hst rag-embed` first",
                  file=sys.stderr)
            return 2
        if not Embedder().available():
            print("ERROR: fastembed is not installed in this venv — "
                  "`.venv/bin/python -m pip install fastembed`", file=sys.stderr)
            return 2
        tiers = ("t0", "t1", "t2")
    else:
        tiers = ("t0", "t1")
    events = replay_session(session, store, resolver, tiers=tiers,
                            candidates=args.candidates, budget=args.budget,
                            ranker=args.ranker)

    if args.json:
        for ev in events:
            print(json.dumps(ev, separators=(",", ":"), sort_keys=True))
    else:
        replay_report(events, store, print)
    if args.log:
        from .raglog import append_event
        for ev in events:
            append_event(dict(ev), Path(args.log))
    return 0


def cmd_rag_embed(args) -> int:
    from .embed import CACHE_PATH, Embedder, backfill_embeddings, load_cache
    from .lessons import load_store
    from .raglog import lesson_id

    store = load_store()
    if args.status:
        vectors = load_cache().get("vectors") or {}
        covered = sum(1 for rec in store if lesson_id(rec.lesson) in vectors)
        print(f"cache: {CACHE_PATH}")
        print(f"lessons: {len(store)}  embedded: {covered}  "
              f"stale vectors: {len(vectors) - covered}")
        return 0
    embedder = Embedder()
    if not embedder.available():
        print("ERROR: fastembed is not installed in this venv — "
              "`.venv/bin/python -m pip install fastembed`", file=sys.stderr)
        return 2
    stats = backfill_embeddings(store, embedder=embedder)
    print(f"embedded {stats['embedded']} new, pruned {stats['pruned']} stale, "
          f"{stats['total']} total vectors -> {CACHE_PATH}")
    return 0


def cmd_rag_maintain(args) -> int:
    from pathlib import Path as _P

    from .hygiene import maintain
    from .lessons import load_store
    from .raglog import lesson_id

    report = maintain(log_path=_P(args.log) if args.log else None,
                      apply=args.apply,
                      dedupe_threshold=args.dedupe_threshold,
                      decay_games=args.decay_games)
    store = load_store()
    titles = {lesson_id(rec.lesson): (rec.title or rec.lesson[:60])
              for rec in store}

    stat_rows = [{"id": lid, "title": titles.get(lid, "(archived)"),
                  "fires": s["times_fired"], "games": s["games_fired"],
                  "corpus": s["games_in_corpus"], "won": s["won_when_fired"],
                  "applied": s["applied"], "last_fired": s["last_fired"] or ""}
                 for lid, s in report["stats"].items()]
    stat_rows.sort(key=lambda r: (-r["fires"], r["id"]))
    dupe_rows = [{"similarity": d["similarity"], "keep": d["keep"],
                  "keep_title": d["keep_title"], "drop": d["drop"],
                  "drop_title": d["drop_title"]} for d in report["duplicates"]]
    sections = [
        (f"Per-lesson stats ({report['lessons']} lessons)", stat_rows),
        ("Near-duplicates (keep <- drop)", dupe_rows),
        (f"Decay candidates (0 fires in >={args.decay_games} games)", report["decayed"]),
        ("Headline candidates (for the next post-game synthesis)",
         report["headline_candidates"]),
    ]
    for title, rows in sections:
        print(f"## {title}")
        print(stats_mod.format_table(rows))
        print()
    if args.apply:
        print(f"APPLIED: {report.get('archived_count', 0)} archived, "
              f"{report.get('remaining', report['lessons'])} remain; stats stamped.")
    else:
        print("DRY RUN — nothing written. Re-run with --apply to stamp stats "
              "and archive the rows above.")
    return 0


def cmd_coach_report(args) -> int:
    from pathlib import Path as _P

    from . import advice as advice_mod
    from .advice import coach_report
    from .raglog import read_events

    events = read_events(_P(args.log) if args.log else None)
    if not [ev for ev in events if ev.get("ev") == "advice"]:
        print("No advice telemetry yet — publish advice via coach_publish.py "
              "(the coaching skills do this) and re-run.")
        return 0
    session = None
    if args.session:
        session = _P(args.session)
        if not session.is_absolute():
            session = find_log_root(args.logs_root) / args.session
        if not session.is_dir():
            print(f"ERROR: no such session directory: {session}", file=sys.stderr)
            return 2
    report = coach_report(events, session_dir=session)

    print("## Advice per game" + ("" if session else "  (no --session: adherence skipped)"))
    print(stats_mod.format_table(report["games"]))
    print()
    print("## Latency (turn marker -> advice publish)")
    print(stats_mod.format_table([report["latency"]]))
    print()
    if report["latency_by_bucket"]:
        print(f"## Latency by turn bucket  (deadline {advice_mod.TURN_DEADLINE_S:.0f}s)")
        print(stats_mod.format_table(report["latency_by_bucket"]))
        print()
    if report["latency_by_variant"]:
        print("## Latency by variant  (gated vs full — the check-gate A/B)")
        print(stats_mod.format_table(report["latency_by_variant"]))
        print()
    if report["outcomes"]:
        o = report["outcomes"]
        print("## Outcomes by adherence (proxy, threshold 0.5)")
        print(stats_mod.format_table([{
            "followed": o["followed"], "followed_won": o["followed_won"],
            "overrode": o["overrode"], "overrode_won": o["overrode_won"]}]))
        print()
    if report["calibration"]:
        c = report["calibration"]
        print(f"## Proxy calibration: {c['proxy_agrees']}/{c['labels']} "
              f"human labels agree with the proxy")
        print()
    if report["nominations"]:
        print("## Override wins — lesson nominations (user beat the coach)")
        print(stats_mod.format_table(report["nominations"]))
        print()
    _print_choices_readouts(events, session, stats_mod)
    if report["unjoined_advice"]:
        print(f"({report['unjoined_advice']} advice events outside any game's "
              f"join window)")
    return 0


def _print_choices_readouts(events, session, stats_mod) -> None:
    """#20 / #19: mulligans (coach vs Jev vs what you kept, from games.db) and
    Discovers (vs what you picked, from the session's Power.log)."""
    from .choicesreport import discover_readout, mulligan_readout, session_discovers
    from .db import connect
    from .raglog import join_games

    games = join_games(events)
    resolver = HeroClassResolver(allow_fetch=False)
    try:
        conn = connect(resolve_db_path(None))
    except Exception:
        conn = None
    if conn is not None:
        mull = mulligan_readout(games, conn, resolver.name)
        conn.close()
        if mull["games"]:
            print("## Mulligans: coach vs Jev vs what you kept  (per card; agreement, not correctness)")
            print(stats_mod.format_table(mull["games"]))
            print()
            print(stats_mod.format_table(mull["summary"]))
            print()
    if session is None:
        if any(ev.get("kind") == "discover" for ev in events if ev.get("ev") == "advice"):
            print("(Discover picks are scored with --session <dir>: the pick comes from its Power.log)")
        return
    disc = discover_readout(games, session_discovers(session, resolver), session.name)
    if disc["picks"]:
        print("## Discovers: advised pick vs what you picked")
        print(stats_mod.format_table(disc["picks"]))
        print()
        print(stats_mod.format_table(disc["summary"]))
        print()


def cmd_overlay_sync(args) -> int:
    from .overlayapp import stale_files, sync, windows_app_dir

    app_dir = Path(args.app_dir).expanduser() if args.app_dir else windows_app_dir()
    if not app_dir.is_dir():
        print(f"No overlay app copy at {app_dir}. Create it per hearthstone-overlay/README.md "
              "(Electron mode), or pass --app-dir.", file=sys.stderr)
        return 2
    stale = stale_files(app_dir)
    if args.check:
        if stale:
            print(f"{len(stale)} stale file(s) in {app_dir}:")
            for rel in stale:
                print(f"  {rel}")
            print("Run `hst overlay-sync` to update the copy.")
            return 1
        print(f"Overlay app copy is up to date: {app_dir}")
        return 0
    result = sync(app_dir)
    if not result["copied"] and not result["seeded"]:
        print(f"Already up to date: {app_dir}")
        return 0
    for rel in result["copied"]:
        print(f"copied  {rel}")
    for rel in result["seeded"]:
        print(f"seeded  {rel} (from config.example.json)")
    if "package.json" in result["copied"]:
        print("package.json changed: run `npm install` in that folder on Windows, then restart the overlay.")
    else:
        print("Restart the overlay (start-overlay.cmd) to load the new files.")
    return 0


def cmd_selftest(args) -> int:
    from .selftest import format_results, run_checks
    results = run_checks()
    print(format_results(results))
    return 1 if any(r["status"] == "FAIL" for r in results) else 0


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
    p.add_argument("--overlay-dir", help="Mirror live.json for the Windows overlay (default: HS_OVERLAY_DIR or Windows user hs-overlay)")
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

    p = sub.add_parser("rag-report", help="Retrieval telemetry: firing rates, dead knowledge, misses")
    p.add_argument("--log", help="Retrieval log path (default: retrieval_log.jsonl next to the DB)")
    p.add_argument("--days", type=int, help="Only events from the last N days")
    p.set_defaults(func=cmd_rag_report)

    p = sub.add_parser("rag-replay", help="Run the current lesson store against a historical session's Power.logs")
    p.add_argument("session_dir", help="Session dir name (Hearthstone_YYYY_...) or absolute path")
    p.add_argument("--logs-root", help="Hearthstone Logs directory (auto-detected by default)")
    p.add_argument("--json", action="store_true", help="Emit raw events as JSON lines (for diff-based regression tests)")
    p.add_argument("--log", help="Also append the replayed events to this JSONL file (never the live log)")
    p.add_argument("--tier0-only", action="store_true", help="A/B baseline: skip the lexical tier")
    p.add_argument("--t2", action="store_true", help="Also run the semantic tier (needs `hst rag-embed` first; opt-in so existing replay diffs stay stable)")
    p.add_argument("--candidates", action="store_true", help="Include unthresholded t1/t2 candidate scores in events (threshold tuning)")
    p.add_argument("--budget", action="store_true", help="Phase-5 A/B: over-fetch, rank, and cut to the context budget")
    p.add_argument("--ranker", choices=["evidence", "legacy"], default=None, help="Ranking function for --budget (default: evidence, or HS_RAG_RANKER)")
    p.set_defaults(func=cmd_rag_replay)

    p = sub.add_parser("rag-embed", help="Build/refresh the Tier-2 lesson embedding cache (fastembed, local)")
    p.add_argument("--status", action="store_true", help="Show cache coverage without embedding anything")
    p.set_defaults(func=cmd_rag_embed)

    p = sub.add_parser("coach-report", help="Phase 6: advice volume, latency, adherence proxy, outcome splits, override-win nominations")
    p.add_argument("--session", help="Session dir name or path — enables the adherence proxy (needs the Power.log)")
    p.add_argument("--logs-root", help="Hearthstone Logs directory (auto-detected by default)")
    p.add_argument("--log", help="Retrieval log path (default: retrieval_log.jsonl next to the DB)")
    p.set_defaults(func=cmd_coach_report)

    p = sub.add_parser("selftest", help="Phase 7: PASS/WARN/FAIL every layer of the running stack (read-only, safe mid-session)")
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser("overlay-sync", help="Copy changed overlay app files to the Windows Electron copy (keeps config.json)")
    p.add_argument("--check", action="store_true", help="Only list stale files; exit 1 if any")
    p.add_argument("--app-dir", help="The Windows copy (default: HS_OVERLAY_APP_DIR, else next to the shared overlay folder)")
    p.set_defaults(func=cmd_overlay_sync)

    p = sub.add_parser("rag-maintain", help="KB hygiene: stamp per-lesson stats, merge near-dupes, archive dead knowledge (dry-run by default)")
    p.add_argument("--apply", action="store_true", help="Write the store/archive; without this, report only")
    p.add_argument("--decay-games", type=int, default=15, help="Archive lessons unfired across this many telemetry games (default 15)")
    p.add_argument("--dedupe-threshold", type=float, default=0.6, help="Jaccard similarity to treat two lessons as duplicates (default 0.6)")
    p.add_argument("--log", help="Retrieval log path (default: retrieval_log.jsonl next to the DB)")
    p.set_defaults(func=cmd_rag_maintain)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
