from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lab" / "mulligan-ab"))

import mulligan_ab as m  # noqa: E402


class FakeCards:
    CARDS = {
        "A": {"name": "Slam", "cost": 1, "type": "SPELL", "text": "Deal $2 damage to a minion."},
        "B": {"name": "Rockskipper", "cost": 2, "type": "MINION", "attack": 1, "health": 3,
              "text": "<b>Battlecry:</b> Get a Rock."},
        "C": {"name": "For Glory!", "cost": 5, "type": "SPELL", "text": "Draw[x] two."},
        "COIN": {"name": "The Coin", "cost": 0, "type": "SPELL", "text": "Gain 1 Mana."},
    }

    def card(self, cid):
        return self.CARDS.get(cid, {})

    def card_by_dbf(self, dbf):
        return {}


def hand(offered, **extra):
    return {"hand_id": "g1", "result": "WON", "format": "FT_STANDARD",
            "my_class": "WARRIOR", "opp_class": "DEATHKNIGHT", "going_first": False,
            "deck_name": "Burn", "decklist": [], "offered": offered, **extra}


def card(cid, copies, kept, name=None):
    return {"card_id": cid, "copies": copies, "kept": kept, "name": name or cid,
            "cost": 1, "type": "spell", "text": ""}


class BuildTests(unittest.TestCase):
    def test_build_drops_coin_and_opponent_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "games.db"
            con = sqlite3.connect(db)
            con.executescript("""
                CREATE TABLE games (id INTEGER PRIMARY KEY, start_time TEXT, game_type TEXT,
                    format_type TEXT, friendly_class TEXT, opponent_class TEXT,
                    friendly_first INTEGER, result TEXT, deck_name TEXT, deckstring TEXT);
                CREATE TABLE game_cards (game_id INTEGER, card_id TEXT, friendly INTEGER,
                    mull_offered INTEGER, mull_kept INTEGER);
                INSERT INTO games VALUES (1, 't1', 'GT_RANKED', 'FT_WILD', 'WARRIOR', 'MAGE',
                    0, 'LOST', 'Burn', NULL);
                INSERT INTO games VALUES (2, 't2', 'GT_BATTLEGROUNDS', NULL, NULL, NULL,
                    0, NULL, NULL, NULL);
                INSERT INTO game_cards VALUES (1, 'A', 1, 2, 1), (1, 'B', 1, 1, 1),
                    (1, 'C', 1, 1, 0), (1, 'COIN', 1, 1, 0), (1, 'A', 0, 1, 1);
            """)
            con.commit()
            con.close()
            hands, skipped = m.build_hands(db, FakeCards())
        self.assertEqual(len(hands), 1)
        h = hands[0]
        self.assertEqual([c["card_id"] for c in h["offered"]], ["A", "B", "C"])
        self.assertEqual(sum(c["copies"] for c in h["offered"]), 4)
        self.assertEqual(h["offered"][1]["stats"], "1/3")
        self.assertEqual(h["offered"][0]["text"], "Deal 2 damage to a minion.")
        self.assertEqual(skipped, {"no_mulligan_rows": 0, "odd_hand_size": 0})

    def test_state_has_no_player_keeps(self):
        s = m.hand_state(hand([card("A", 1, 1, "Slam")]))
        self.assertIn("Death Knight", s)
        self.assertIn("going second", s)
        self.assertIn("[c0] Slam", s)
        self.assertNotIn("kept", s.lower())


class ArmTests(unittest.TestCase):
    def test_haiku_arm_parses_and_clamps(self):
        h = hand([card("A", 2, 1), card("B", 1, 0)])
        payload = {"cards": [{"slot": "c0", "keep": 5, "reason": "cheap"}]}
        resp = SimpleNamespace(
            model="claude-haiku-5-5", stop_reason="end_turn",
            content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=json.dumps(payload))],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200))
        seen = {}

        def create(**kw):
            seen.update(kw)
            return resp

        client = SimpleNamespace(messages=SimpleNamespace(create=create))
        out = m.make_claude_arm("low", client=client)(h, True)
        self.assertEqual(out["keeps"], {"A": 2, "B": 0})  # clamped to copies; skipped card tossed
        self.assertEqual(seen["output_config"]["effort"], "low")
        self.assertAlmostEqual(out["cost_usd"], (1000 * 0.10 + 200 * 0.50) / 1e6)

    def test_jev_arm_uses_noul_threshold(self):
        h = hand([card("A", 2, 2), card("B", 1, 0)])
        sent = {}

        def post(body):
            sent.update(json.loads(body))
            return {"model": "jev-1.13.0", "usage": {"input_tokens": 500},
                    "answers": {"c0": {"type": "noul", "noul": 0.8},
                                "c1": {"type": "noul", "noul": 0.2}}}

        out = m.make_jev_arm("k", post=post)(h, False)
        self.assertEqual(out["keeps"], {"A": 2, "B": 0})
        self.assertEqual(sent["model"], m.JEV_MODEL)
        self.assertEqual(sent["questions"]["c0"]["type"], "noul")
        self.assertNotIn("Your deck", sent["state"])


class RunAndReportTests(unittest.TestCase):
    def test_run_resumes_and_report_scores(self):
        h1 = hand([card("A", 2, 1), card("B", 1, 1)], hand_id="g1", result="WON")
        h2 = hand([card("A", 1, 0), card("C", 1, 0)], hand_id="g2", result="LOST")
        calls = []

        def ask(h, with_decklist):
            calls.append(h["hand_id"])
            keeps = {c["card_id"]: c["copies"] for c in h["offered"]}  # keeps everything
            return {"model": "fake", "keeps": keeps, "probs": {k: 0.9 for k in keeps},
                    "latency_s": 1.0, "input_tokens": 10, "output_tokens": 0, "cost_usd": 0.001}

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "jev.jsonl"
            m.run_arm("jev", ask, [h1, h2], 2, out, workers=1)
            m.run_arm("jev", ask, [h1, h2], 2, out, workers=1)  # nothing left to ask
            self.assertEqual(len(calls), 4)
            s = m.summarize_arm(m.read_jsonl(out), {"g1": h1, "g2": h2})
            text = m.report([h1, h2], Path(tmp))
        # player kept 2 of 5 copies; keep-everything agrees on those 2 only
        self.assertEqual(s["agree_copies"], "4/10")
        self.assertEqual(s["stable_pct"], 100.0)
        self.assertEqual(s["keep_rate_pct"], 100.0)
        self.assertIn("## Arms vs the player's own keeps", text)

    def test_kappa_and_auc(self):
        self.assertEqual(m.kappa([(1, 1), (0, 0)]), 1.0)
        self.assertEqual(m.kappa([(1, 0), (0, 1)]), -1.0)
        self.assertEqual(m.auc([(0.9, 1), (0.1, 0)]), 1.0)
        self.assertIsNone(m.auc([(0.9, 1)]))


if __name__ == "__main__":
    unittest.main()
