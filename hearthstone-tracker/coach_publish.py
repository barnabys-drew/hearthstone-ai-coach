#!/usr/bin/env python3
"""Publish live-coach advice to the Hearthstone overlay.

Examples:
  ./coach_publish.py --kind turn --turn 5 --headline "Stabilize" \
    --why "They have 11 incoming; clear the biggest minion." \
    --step "Trade 3/2 into their 5/3" --step "Hero Power face"

  printf '%s' '{"kind":"lethal","turn":8,"steps":["Swing face"]}' | ./coach_publish.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Re-exec into the project venv (like ./hst) so dependencies such as pydantic
# resolve no matter which python invoked this script. Compare sys.prefix, not
# executable paths — venv pythons are symlinks to the base interpreter.
_VENV_DIR = Path(__file__).resolve().parent / ".venv"
_VENV_PYTHON = _VENV_DIR / "bin" / "python"
if _VENV_PYTHON.exists() and Path(sys.prefix).resolve() != _VENV_DIR.resolve():
    os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON), __file__, *sys.argv[1:]])

# Works when launched directly from this directory without package install.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hstracker.overlay import write_advice, write_clear_advice, write_discover  # noqa: E402


def _payload_from_args(args: argparse.Namespace) -> dict:
    payload = {
        "kind": args.kind,
        "turn": args.turn,
        "headline": args.headline,
        "why": args.why,
        "steps": args.step or [],
        "warning": args.warning,
        "discover": args.discover,
        "lessons": args.lesson or [],
    }
    if args.lethal_math:
        payload["kind"] = "lethal"
        payload["lethal"] = {"is_lethal": True, "math": args.lethal_math}
    if args.game_over:
        payload["kind"] = "gameover"
        payload["game_over"] = args.game_over
    if args.mulligan_json:
        payload["kind"] = "mulligan"
        payload["mulligan"] = json.loads(args.mulligan_json)
    return {key: value for key, value in payload.items() if value not in (None, [], "")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overlay-dir", help="Shared overlay folder (default: HS_OVERLAY_DIR or Windows user hs-overlay)")
    parser.add_argument("--clear", action="store_true", help="Write an idle advice card")
    parser.add_argument("--json", help="Advice payload JSON object; stdin JSON is also accepted")
    parser.add_argument("--kind", default="turn", choices=["idle", "turn", "mulligan", "lethal", "update", "gameover"])
    parser.add_argument("--turn", type=int)
    parser.add_argument("--headline")
    parser.add_argument("--why")
    parser.add_argument("--step", action="append", help="Add one numbered step; repeat for multiple steps")
    parser.add_argument("--warning")
    parser.add_argument("--discover", help="Discover pick line ('Pick X — reason'). Alone, it merges into the current advice card instead of replacing it.")
    parser.add_argument("--lesson", action="append", help="Recurring-lesson line for the overlay lessons box; repeat for multiple")
    parser.add_argument("--lesson-record", help="JSON Lesson record with trigger conditions; appends to the tracker's lesson store so it fires in future turn markers. Example: '{\"lesson\":\"one-hit or leave it\",\"trigger\":{\"enemy_board\":[\"Bloodhoof Brave\"]},\"cost\":\"7 face\"}'")
    parser.add_argument("--applied-lesson", action="append", help="A fired lesson this advice actually used: the 12-hex id from the turn marker (#abc123def456) or the exact lesson text; repeat for multiple. Feeds the rag-report precision proxy.")
    parser.add_argument("--lethal-math", help="Marks the payload as lethal and displays this arithmetic")
    parser.add_argument("--game-over", choices=["WON", "LOST", "TIED", "UNKNOWN"])
    parser.add_argument("--mulligan-json", help="JSON array of {card, keep, reason} rows")
    args = parser.parse_args(argv)

    if args.lesson_record:
        from hstracker.lessons import append_lesson
        path = append_lesson(json.loads(args.lesson_record))
        print(path)
        if not (args.json or args.headline or args.step or args.mulligan_json or args.discover or args.clear or args.applied_lesson):
            return 0

    if args.applied_lesson:
        import re
        from hstracker.raglog import append_event, lesson_id
        ids = [v.lstrip("#") if re.fullmatch(r"#?[0-9a-f]{12}", v) else lesson_id(v)
               for v in args.applied_lesson]
        append_event({"ev": "applied", "lesson_ids": ids, "turn": args.turn})
        if not (args.json or args.headline or args.step or args.mulligan_json or args.discover or args.clear):
            return 0

    if args.clear:
        path = write_clear_advice(args.overlay_dir, turn=args.turn)
    elif args.discover and not (args.json or args.headline or args.step or args.mulligan_json):
        # Mid-turn discover pick: merge into the advice card already on screen.
        path = write_discover(args.discover, args.overlay_dir)
    else:
        if args.json:
            payload = json.loads(args.json)
        else:
            stdin = "" if sys.stdin.isatty() else sys.stdin.read().strip()
            payload = json.loads(stdin) if stdin else _payload_from_args(args)
        path = write_advice(payload, args.overlay_dir)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
