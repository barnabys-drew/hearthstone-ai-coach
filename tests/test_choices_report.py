from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hearthstone-tracker"))

from hstracker import choicesreport as cr  # noqa: E402
from hstracker.raglog import join_games  # noqa: E402

NAMES = {"SLAM": "Slam", "ROCK": "Rockskipper", "GLORY": "For Glory!", "COIN": "The Coin"}


def db(games):
    """games: {start_time: [(card_id, offered, kept)]}"""
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE games (id INTEGER PRIMARY KEY, start_time TEXT, source_file TEXT);
        CREATE TABLE game_cards (game_id INTEGER, card_id TEXT, friendly INTEGER,
                                 mull_offered INTEGER, mull_kept INTEGER);
    """)
    for i, (start, cards) in enumerate(games.items(), 1):
        conn.execute("INSERT INTO games VALUES (?, ?, ?)",
                     (i, start, f"/logs/Hearthstone_S{i // 10}/Power.log"))
        for cid, offered, kept in cards:
            conn.execute("INSERT INTO game_cards VALUES (?, ?, 1, ?, ?)", (i, cid, offered, kept))
        conn.execute("INSERT INTO game_cards VALUES (?, 'ROCK', 0, 1, 0)", (i,))  # opponent row
    return conn


def outcome(game_no, result, start, ts, session="S"):
    return {"ev": "outcome", "session": session, "game_no": game_no, "result": result,
            "start_time": start, "ts": ts}


def mull(cards, keeps, ts, model, variant=None, mode="both"):
    return {"ev": "advice", "kind": "mulligan", "turn": 0, "mulligan_cards": cards,
            "mulligan_keep": keeps, "model": model, "variant": variant,
            "choices_mode": mode, "ts": ts}


class MulliganReadoutTests(unittest.TestCase):
    def test_coach_jev_and_player_compared_per_game(self):
        conn = db({"2026-10-20 10:00:00": [("SLAM", 2, 1), ("ROCK", 1, 1), ("GLORY", 1, 0), ("COIN", 1, 0)],
                   "2026-10-20 10:20:00": [("SLAM", 1, 0), ("GLORY", 1, 0)]})
        events = [
            mull(["Slam", "Rockskipper", "For Glory!"], [True, True, False], 100, "haiku-5-5"),
            mull(["Slam x2", "Rockskipper", "For Glory!"], [True, True, True], 101, "jev-1.13.0", "jev"),
            outcome(1, "WON", "2026-10-20 10:00:00", 600),
            mull(["Slam", "For Glory!"], [True, False], 1300, "haiku-5-5"),
            mull(["Slam", "For Glory!"], [False, False], 1301, "jev-1.13.0", "jev"),
            outcome(2, "LOST", "2026-10-20 10:20:00", 1800),
        ]
        out = cr.mulligan_readout(join_games(events), conn, NAMES.get)
        g1, g2 = out["games"]
        self.assertEqual((g1["coach_vs_you"], g1["jev_vs_you"], g1["coach_vs_jev"]), ("3/3", "2/3", "2/3"))
        self.assertEqual((g2["coach_vs_you"], g2["jev_vs_you"]), ("1/2", "2/2"))
        self.assertEqual(g1["mode"], "both")
        coach, jev, both = out["summary"]
        self.assertEqual((coach["cards"], coach["games_matched_exactly"], coach["of_those_won"]), ("4/5", 1, 1))
        self.assertEqual((jev["cards"], jev["games_matched_exactly"], jev["of_those_won"]), ("4/5", 1, 0))
        self.assertEqual(both["cards"], "3/5")

    def test_old_events_without_keeps_and_unknown_games_are_skipped(self):
        conn = db({})
        old = {"ev": "advice", "kind": "mulligan", "mulligan_cards": ["Slam"], "ts": 100}
        events = [old, outcome(1, "WON", "2026-10-20 10:00:00", 600)]
        self.assertEqual(cr.mulligan_readout(join_games(events), conn, NAMES.get)["games"], [])
        events = [mull(["Slam"], [True], 100, "haiku-5-5"), outcome(1, "WON", "not in db", 600)]
        row = cr.mulligan_readout(join_games(events), conn, NAMES.get)["games"][0]
        self.assertEqual(row["coach_vs_you"], "—")  # nothing to compare against, no crash

    def test_player_keeps_fall_back_to_session_and_game_order(self):
        conn = db({"2026-10-20 10:00:00": [("SLAM", 1, 1)], "2026-10-20 10:20:00": [("SLAM", 1, 0)]})
        # Both games sit in the same session folder (ids 1 and 2 -> S0 by the helper,
        # so set them explicitly): game 2 of session S0 is the second row.
        conn.execute("UPDATE games SET source_file = '/logs/Hearthstone_TEST/Power.log'")
        self.assertIsNone(cr.player_keeps(conn, None, NAMES.get))
        self.assertEqual(cr.player_keeps(conn, None, NAMES.get, session="Hearthstone_TEST", game_no=2),
                         {"slam": False})
        self.assertEqual(cr.player_keeps(conn, "2026-10-20 10:00:00", NAMES.get),
                         {"slam": True}, "start_time still wins when present")

    def test_card_key_and_keeps(self):
        self.assertEqual(cr.card_key("Slam x2"), "slam")
        self.assertEqual(cr.advice_keeps(mull(["A", "A"], [False, True], 1, "m")), {"a": True})
        self.assertIsNone(cr.advice_keeps(mull(["A"], [True, False], 1, "m")))


def choice(cid, source, options, type_="GENERAL"):
    return SimpleNamespace(id=cid, source=source, choices=options, type=SimpleNamespace(name=type_))


class DiscoverReadoutTests(unittest.TestCase):
    ENT = {10: "Tracking", 11: "Bait", 12: "Trap", 13: "Shot", 20: "Enemy Card", 21: "X", 22: "Y"}
    CTRL = {10: 1, 20: 2}

    def extract(self, choices, chosen):
        return cr.extract_discovers(choices, chosen, self.ENT.get, 1, self.CTRL.get)

    def test_extract_keeps_only_friendly_discovers_with_a_pick(self):
        choices = [choice(1, 10, [11, 12, 13]), choice(2, 20, [21, 22]),
                   choice(3, 10, [11, 12], type_="MULLIGAN"), choice(4, 10, [12, 13])]
        chosen = [SimpleNamespace(id=1, choices=[12]), SimpleNamespace(id=2, choices=[21])]
        out = self.extract(choices, chosen)
        self.assertEqual(out, [{"source": "Tracking", "options": ["Bait", "Trap", "Shot"], "chosen": "Trap"}])

    def test_jev_and_coach_scored_against_the_real_pick(self):
        actual = {1: [{"source": "Tracking", "options": ["Bait", "Trap", "Shot"], "chosen": "Trap"}]}
        events = [
            {"ev": "advice", "kind": "discover", "variant": "jev", "ts": 100,
             "discover": "Pick Trap — Jev 70% (vs Bait 20%, Shot 10%)",
             "jev": {"options": ["Bait", "Trap", "Shot"]}},
            {"ev": "advice", "kind": "discover", "ts": 101, "discover": "Pick Bait — cheap"},
            {"ev": "advice", "kind": "discover", "ts": 102, "discover": "Pick Bait — cheap"},  # republish
            outcome(1, "WON", "t", 600),
        ]
        out = cr.discover_readout(join_games(events), actual, "S")
        by = {s["source"]: s for s in out["summary"]}
        self.assertEqual((by["jev"]["picks"], by["jev"]["followed"], by["jev"]["followed_won"]), (1, 1, 1))
        self.assertEqual((by["coach"]["picks"], by["coach"]["overridden"]), (1, 1), "republish counted once")
        self.assertEqual(cr.discover_readout(join_games(events), actual, "OTHER")["picks"], [])

    def test_picked_name(self):
        opts = ["Bait and Switch", "Bait", "Shot"]
        self.assertEqual(cr.picked_name("Pick Bait and Switch — Jev 70%", opts), "Bait and Switch")
        self.assertEqual(cr.picked_name("Pick Shot - burn", opts), "Shot")
        self.assertEqual(cr.picked_name("Take the Bait and Switch here", opts), "Bait and Switch")
        self.assertIsNone(cr.picked_name("Pick something else", opts))


if __name__ == "__main__":
    unittest.main()
