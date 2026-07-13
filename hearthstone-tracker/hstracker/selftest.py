"""Phase 7 (v1): one command that proves every layer of the running stack.

`hst selftest` exercises each phase end-to-end — synthetic inputs through the
REAL code paths, plus read-only checks of the real stores/caches/logs — and
prints one PASS/WARN/FAIL line per layer. The loop it exists for: play a
session, run selftest, fix whatever line went red, play again.

PASS = the layer works. WARN = the layer is fine but has nothing to work
with yet (no telemetry, cache not initialized, feed not running) — expected
on a fresh setup, worth reading, never fatal. FAIL = broken code or state;
the command exits 1.

Read-only by design: real stores and the live telemetry log are never
written (synthetic round-trips use temp files), so it is always safe to run
mid-session.
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any

CheckResult = dict[str, Any]  # {check, status: PASS|WARN|FAIL, detail}


def _result(check: str, status: str, detail: str) -> CheckResult:
    return {"check": check, "status": status, "detail": detail}


def _synthetic_snapshot() -> dict[str, Any]:
    card = {"name": "Bloodhoof Brave", "text": "Taunt. +3 Attack while damaged.",
            "flags": ["taunt"]}
    return {"whose_turn": "me", "raw_turn": 3, "turn": 2, "phase": "playing",
            "me": {"hand": [], "board": []},
            "opp": {"class": "WARRIOR", "board": [card], "hand": [],
                    "hand_hidden": 4}}


def check_p0_triggers() -> CheckResult:
    from .lessons import Lesson, match_lessons
    rec = Lesson.model_validate({
        "lesson": "one-hit Bloodhoof Brave or leave it",
        "trigger": {"enemy_board": ["Bloodhoof Brave"]}})
    hits = match_lessons(_synthetic_snapshot(), [rec])
    if hits and hits[0].lesson == rec.lesson:
        return _result("p0 trigger matching", "PASS", "synthetic trigger fires")
    return _result("p0 trigger matching", "FAIL", "synthetic trigger did not fire")


def check_p1_telemetry() -> CheckResult:
    from .raglog import RAG_LOG_PATH, join_games, read_events
    events = read_events()
    if not events:
        return _result("p1 telemetry", "WARN",
                       f"no events yet at {RAG_LOG_PATH} — play with `hst live` running")
    games = join_games(events)
    newest = max((ev.get("ts") or 0) for ev in events)
    age_h = (time.time() - newest) / 3600
    return _result("p1 telemetry", "PASS",
                   f"{len(events)} events, {len(games)} game aggregates, "
                   f"newest {age_h:.1f}h old")


def check_p2_lexical() -> CheckResult:
    from .lessons import Lesson
    from .lexical import LessonIndex
    store = [Lesson.model_validate({"lesson": t}) for t in (
        "kill bloodhoof brave in one hit: taunt gains attack while damaged",
        "coin only when it converts", "spend every weapon charge")]
    index = LessonIndex(store)
    score, overlap = index.score(["bloodhoof", "brave", "taunt", "damaged"], 0)
    if score > 0 and overlap >= 2:
        return _result("p2 lexical tier", "PASS",
                       f"BM25 scores (score {score:.2f}, overlap {overlap}); "
                       f"live {'ON' if os.environ.get('HS_RAG_T1') == '1' else 'off (lab)'}")
    return _result("p2 lexical tier", "FAIL", "BM25 failed to score a known match")


def check_p3_embeddings() -> CheckResult:
    from .embed import CACHE_PATH, Embedder, load_cache, _dot, _unit
    if round(_dot(_unit([3.0, 4.0]), _unit([3.0, 4.0])), 3) != 1.0:
        return _result("p3 semantic tier", "FAIL", "cosine math broken")
    cache = load_cache()
    if not cache:
        return _result("p3 semantic tier", "WARN",
                       f"no embedding cache at {CACHE_PATH} — run `hst rag-embed`")
    from .lessons import load_store
    from .raglog import lesson_id
    store = load_store()
    vectors = cache.get("vectors") or {}
    covered = sum(1 for rec in store if lesson_id(rec.lesson) in vectors)
    detail = (f"{covered}/{len(store)} lessons embedded; fastembed "
              f"{'available' if Embedder().available() else 'MISSING (write path only)'}; "
              f"live {'ON' if os.environ.get('HS_RAG_T2') == '1' else 'off (lab)'}")
    if store and covered == 0:
        return _result("p3 semantic tier", "WARN", detail + " — re-run `hst rag-embed`")
    return _result("p3 semantic tier", "PASS", detail)


def check_p4_hygiene() -> CheckResult:
    from .hygiene import maintain
    report = maintain(apply=False)  # dry-run: reads real store+log, writes nothing
    return _result("p4 hygiene", "PASS",
                   f"dry-run over {report['lessons']} lessons: "
                   f"{len(report['duplicates'])} dupes, "
                   f"{len(report['decayed'])} decay candidates, "
                   f"{len(report['headline_candidates'])} headline noms")


def check_p5_budget() -> CheckResult:
    from .budget import assemble
    from .lessons import Lesson
    results = [{"lesson": Lesson.model_validate(
        {"lesson": f"lesson {i} " + "x" * 90, "date": "2026-07-10"}),
        "tier": "t0", "score": None} for i in range(4)]
    kept, dropped, spent = assemble(results, budget=300)
    if kept and dropped and spent <= 300 + 150:
        live = "ON" if os.environ.get("HS_RAG_BUDGET") == "1" else "off (lab)"
        return _result("p5 budget", "PASS",
                       f"kept {len(kept)}, dropped {len(dropped)}, "
                       f"{spent} chars; live {live}")
    return _result("p5 budget", "FAIL",
                   f"assemble misbehaved (kept {len(kept)}, spent {spent})")


def check_p6_advice() -> CheckResult:
    from .advice import coach_report
    from .raglog import append_event, read_events
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "log.jsonl"
        append_event({"ev": "advice", "advice_id": "selftest12345",
                      "kind": "turn", "turn": 3, "headline": "selftest",
                      "steps": ["Play Selftest Minion"], "model": "selftest"},
                     log)
        events = read_events(log)
        report = coach_report(events)
    if events and events[0].get("advice_id") == "selftest12345" \
            and report["unjoined_advice"] == 1:
        real = [ev for ev in read_events() if ev.get("ev") == "advice"]
        return _result("p6 advice telemetry", "PASS",
                       f"round-trip OK; {len(real)} real advice events logged"
                       + ("" if real else " — coaching skills must pass --model"))
    return _result("p6 advice telemetry", "FAIL", "advice event round-trip failed")


def check_feed() -> CheckResult:
    from .config import DEFAULT_DB
    live_json = DEFAULT_DB.parent / "live.json"
    try:
        age = time.time() - live_json.stat().st_mtime
    except OSError:
        return _result("feed / live.json", "WARN",
                       f"{live_json} missing — start coach_feed.sh when playing")
    if age < 600:
        return _result("feed / live.json", "PASS", f"fresh ({age:.0f}s old)")
    return _result("feed / live.json", "WARN",
                   f"stale ({age/3600:.1f}h old) — feed not running (fine if not playing)")


def check_overlay_dir() -> CheckResult:
    from .overlay import resolve_overlay_dir
    directory = resolve_overlay_dir(None)
    if not directory:
        return _result("overlay dir", "WARN", "not resolvable (HS_OVERLAY_DIR unset?)")
    probe = Path(directory) / ".selftest_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return _result("overlay dir", "PASS", f"writable: {directory}")
    except OSError as exc:
        return _result("overlay dir", "FAIL", f"{directory} not writable: {exc}")


def check_store() -> CheckResult:
    from .lessons import STORE_PATH, load_store
    store = load_store()
    if not store:
        return _result("lesson store", "WARN", f"empty store at {STORE_PATH}")
    stamped = sum(1 for rec in store if rec.stats is not None)
    return _result("lesson store", "PASS",
                   f"{len(store)} lessons, {stamped} with Phase-4 stats")


ALL_CHECKS = [
    check_feed, check_overlay_dir, check_store,
    check_p0_triggers, check_p1_telemetry, check_p2_lexical,
    check_p3_embeddings, check_p4_hygiene, check_p5_budget, check_p6_advice,
]


def run_checks() -> list[CheckResult]:
    results = []
    for check in ALL_CHECKS:
        try:
            results.append(check())
        except Exception as exc:  # a crashing check IS the finding
            results.append(_result(check.__name__.replace("check_", "").replace("_", " "),
                                   "FAIL", f"{type(exc).__name__}: {exc}"))
    return results


def format_results(results: list[CheckResult]) -> str:
    width = max(len(r["check"]) for r in results)
    lines = [f"{r['status']:<4}  {r['check']:<{width}}  {r['detail']}"
             for r in results]
    fails = sum(1 for r in results if r["status"] == "FAIL")
    warns = sum(1 for r in results if r["status"] == "WARN")
    lines.append(f"\n{len(results)} checks: {len(results) - fails - warns} pass, "
                 f"{warns} warn, {fails} fail")
    return "\n".join(lines)
