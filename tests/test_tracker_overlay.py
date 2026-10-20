from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRACKER = ROOT / "hearthstone-tracker"
sys.path.insert(0, str(TRACKER))


class OverlayBridgeTests(unittest.TestCase):
    def test_normalize_advice_shapes_payload(self) -> None:
        from hstracker.overlay import normalize_advice

        advice = normalize_advice({
            "kind": "lethal",
            "turn": "8",
            "headline": "Kill them",
            "why": "5+5 beats their 10.",
            "steps": "Swing face\nFireball face",
            "warning": "Do not trade.",
            "lethal": {"math": "5+5 = 10 ≥ 10"},
        })
        self.assertEqual(advice["kind"], "lethal")
        self.assertEqual(advice["turn"], 8)
        self.assertEqual(advice["steps"], ["Swing face", "Fireball face"])
        self.assertTrue(advice["lethal"]["is_lethal"])
        self.assertEqual(advice["lethal"]["math"], "5+5 = 10 ≥ 10")
        self.assertIn("ts", advice)

    def test_write_advice_and_mirror_live_are_atomic_json_files(self) -> None:
        from hstracker.overlay import mirror_live_snapshot, write_advice

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            advice_path = write_advice({"kind": "turn", "turn": 3, "steps": ["Trade first"]}, out)
            live_path = mirror_live_snapshot({"turn": 3, "me": {"hp": 30}, "opp": {"hp": 12}}, out)

            self.assertEqual(advice_path.name, "advice.json")
            self.assertEqual(live_path.name, "live.json")
            self.assertEqual(json.loads(advice_path.read_text())["steps"], ["Trade first"])
            self.assertEqual(json.loads(live_path.read_text())["opp"]["hp"], 12)
            self.assertFalse(list(out.glob("*.tmp")))

    def test_coach_publish_cli_accepts_stdin_and_clear(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # Route the Phase-6a advice event to a temp log — exercising the
            # real CLI must never pollute live telemetry (it did once).
            env = {**os.environ, "HS_RAG_LOG": str(Path(tmp) / "rag.jsonl")}
            payload = {"kind": "mulligan", "turn": 0, "mulligan": [{"card": "The Coin", "keep": False, "reason": "not a keep"}]}
            proc = subprocess.run(
                [sys.executable, str(TRACKER / "coach_publish.py"), "--overlay-dir", tmp],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                check=True,
                env=env,
            )
            self.assertIn("advice.json", proc.stdout)
            advice = json.loads((Path(tmp) / "advice.json").read_text())
            self.assertEqual(advice["kind"], "mulligan")
            self.assertEqual(advice["mulligan"][0]["card"], "The Coin")

            subprocess.run(
                [sys.executable, str(TRACKER / "coach_publish.py"), "--overlay-dir", tmp, "--clear"],
                capture_output=True,
                text=True,
                check=True,
            )
            cleared = json.loads((Path(tmp) / "advice.json").read_text())
            self.assertEqual(cleared["kind"], "idle")

    def test_coach_publish_runs_the_plan_check(self) -> None:
        """#25: an illegal plan prints PLAN CHECK lines, lands on the overlay's
        warning line, and is recorded on the advice event."""
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "live.json"
            live.write_text(json.dumps({
                "turn": 6, "whose_turn": "me",
                "me": {"mana": 6, "hand": [{"name": "Windpeak Wyrm", "cost": 5},
                                            {"name": "Precursory Strike", "cost": 2}],
                       "board": [{"name": "Slitherdrake", "atk": 5, "health": 5}]},
                "opp": {"board": [{"name": "Voidwalker", "atk": 1, "health": 3, "flags": ["taunt"]}]},
            }))
            env = {**os.environ, "HS_RAG_LOG": str(Path(tmp) / "rag.jsonl"), "HS_LIVE_JSON": str(live)}
            payload = {"kind": "turn", "turn": 6, "headline": "Go face",
                       "steps": ["Play Windpeak Wyrm (5 mana)", "Play Precursory Strike (2 mana)",
                                 "Attack Slitherdrake face (5)"]}
            proc = subprocess.run(
                [sys.executable, str(TRACKER / "coach_publish.py"), "--overlay-dir", tmp],
                input=json.dumps(payload), text=True, capture_output=True, check=True, env=env)
            self.assertIn("PLAN CHECK (mana)", proc.stdout)
            self.assertIn("PLAN CHECK (taunt)", proc.stdout)
            advice = json.loads((Path(tmp) / "advice.json").read_text())
            self.assertIn("Plan check:", advice["warning"])
            event = json.loads((Path(tmp) / "rag.jsonl").read_text().splitlines()[-1])
            self.assertEqual(sorted(event["plan_check"]), ["mana", "taunt"])

            payload["turn"] = 7  # a plan for another turn is not checked against this board
            proc = subprocess.run(
                [sys.executable, str(TRACKER / "coach_publish.py"), "--overlay-dir", tmp],
                input=json.dumps(payload), text=True, capture_output=True, check=True, env=env)
            self.assertNotIn("PLAN CHECK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
