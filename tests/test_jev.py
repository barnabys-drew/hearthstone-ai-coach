from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hearthstone-tracker"))

from hstracker import jev  # noqa: E402
from hstracker.decks import deckstring_format  # noqa: E402

DECKSTRING = "AAECAaKaBgL8rweqyQcOhqAEjtQEkNQE4+YG6IcHhZUH1aYH7awHj7EH0LIH1rIHlcIHm8IHnMIHAAA="


class FakeResolver:
    CARDS = {
        "SLAM": {"name": "Slam", "cost": 1, "type": "SPELL", "text": "Deal $2 damage to a minion."},
        "ROCK": {"name": "Rockskipper", "cost": 2, "type": "MINION", "attack": 1, "health": 3,
                 "text": "<b>Battlecry:</b> Get a Rock."},
    }

    def card(self, cid):
        return self.CARDS.get(cid, {})


def mulligan_snap(hand):
    return {"phase": "mulligan", "turn": 0,
            "me": {"class": "WARRIOR", "hand": hand}, "opp": {"class": "DEATHKNIGHT"}}


def offered(*names):
    return [{"name": n, "cost": 1, "type": "spell", "text": "", "copies": 1} for n in names]


def mull_hand(*names):
    return {"format": 2, "game_label": None, "my_class": "WARRIOR", "opp_class": "MAGE",
            "going_first": True, "deck_name": None, "decklist": [], "offered": offered(*names)}


def advisor(response=None, error=None, seconds=None):
    """An advisor whose every writer is stubbed: tests never touch the real
    overlay folder or telemetry log (one once wrote a real jev.json)."""
    published, discovers, logged = [], [], []
    seconds = [] if seconds is None else seconds

    def post(body):
        if error:
            raise error
        return response

    a = jev.JevAdvisor(jev.JevClient("k", post=post), None,
                       publish_advice=lambda p, _d: published.append(p),
                       publish_discover=lambda t, _d: discovers.append(t),
                       publish_jev=lambda p, _d: seconds.append(p),
                       log_event=logged.append, background=False)
    return a, published, discovers, logged


class HandTests(unittest.TestCase):
    def test_snapshot_groups_copies_drops_coin_and_infers_order(self):
        snap = mulligan_snap([
            {"name": "Slam", "id": "SLAM", "cost": 1},
            {"name": "Slam", "id": "SLAM", "cost": 1},
            {"name": "Rockskipper", "id": "ROCK", "cost": 2},
            {"name": "The Coin", "id": "COIN", "cost": 0},
        ])
        hand = jev.hand_from_snapshot(snap, FakeResolver(), {"SLAM": 2, "ROCK": 2}, "Burn",
                                      "FT_WILD", "GT_RANKED")
        self.assertEqual([(c["name"], c["copies"]) for c in hand["offered"]],
                         [("Slam", 2), ("Rockskipper", 1)])
        self.assertTrue(hand["going_first"])  # three real cards dealt
        self.assertEqual(hand["offered"][1]["stats"], "1/3")
        state = jev.mulligan_state(hand)
        self.assertTrue(state.startswith("Hearthstone Wild ranked game, mulligan screen."))
        self.assertIn("against Death Knight", state)
        self.assertIn("Your deck (count, name, mana cost): 2x Slam (1); 2x Rockskipper (2).", state)
        self.assertIn("[c0] Slam (x2): 1 mana, spell. Deal 2 damage to a minion.", state)

    def test_four_cards_means_going_second(self):
        snap = mulligan_snap([{"name": n, "id": None} for n in "ABCD"])
        hand = jev.hand_from_snapshot(snap, FakeResolver(), None, None)
        self.assertFalse(hand["going_first"])
        self.assertIn("going second", jev.mulligan_state(hand))

    def test_empty_hand_is_none(self):
        self.assertIsNone(jev.hand_from_snapshot(mulligan_snap([]), FakeResolver(), None, None))

    def test_unknown_game_type_drops_the_label(self):
        snap = mulligan_snap([{"name": "Slam", "id": "SLAM"}])
        hand = jev.hand_from_snapshot(snap, FakeResolver(), None, None, 2, "GT_TAVERNBRAWL")
        self.assertTrue(jev.mulligan_state(hand).startswith("Hearthstone Standard game, mulligan"))

    def test_lab_label_keeps_ranked_wording(self):
        h = mull_hand("Slam")
        del h["game_label"]
        h["format"] = "FT_STANDARD"
        self.assertTrue(jev.mulligan_state(h).startswith("Hearthstone Standard ranked game"))

    def test_deckstring_format(self):
        self.assertEqual(deckstring_format(DECKSTRING), 2)  # a Standard deck (it was played in Wild)
        self.assertIsNone(deckstring_format("not base64!"))


class AdvisorTests(unittest.TestCase):
    def test_mulligan_publishes_logs_and_announces(self):
        resp = {"model": "jev-1.13.0", "usage": {"input_tokens": 700},
                "answers": {"c0": {"noul": 0.81}, "c1": {"noul": 0.49}}}
        a, published, _, logged = advisor(resp)
        a.mulligan(mull_hand("Slam", "For Glory!"), session="S", game_no=2)
        rows = published[0]["mulligan"]
        self.assertEqual([(r["card"], r["keep"]) for r in rows],
                         [("Slam", True), ("For Glory!", False)])
        self.assertEqual(published[0]["model"], "jev-1.13.0")
        self.assertEqual(logged[0]["variant"], "jev")
        self.assertEqual(logged[0]["game_no"], 2)
        self.assertEqual(logged[0]["jev"]["input_tokens"], 700)
        line = a.drain()[0]
        self.assertTrue(line.startswith("== JEV MULLIGAN"))
        self.assertIn("keep: Slam 81%", line)
        self.assertIn("toss: For Glory! 49%", line)

    def test_failure_tells_the_coach_to_take_over(self):
        a, published, _, logged = advisor(error=jev.JevError("HTTP 500"))
        a.mulligan(mull_hand("Slam"))
        self.assertEqual(published, [])
        self.assertEqual(logged, [])
        self.assertTrue(a.drain()[0].startswith("!! Jev mulligan failed"))

    def test_discover_picks_highest_probability_not_first_option(self):
        options = [{"name": "A", "cost": 1, "type": "SPELL", "text": "x"},
                   {"name": "B", "cost": 2, "type": "MINION", "text": "y"},
                   {"name": "C", "cost": 3, "type": "SPELL", "text": "z"}]
        resp = {"answers": {"o0": {"noul": 0.3}, "o1": {"noul": 0.7}, "o2": {"noul": 0.1}}}
        a, _, discovers, logged = advisor(resp)
        snap = {"turn": 4, "me": {"class": "ROGUE", "hp": 20, "hand": [], "board": []},
                "opp": {"class": "MAGE", "hp": 18, "board": []}}
        a.discover(snap, {"source": "Tracking", "options": options})
        self.assertEqual(discovers, ["Pick B — Jev 70% (vs A 30%, C 10%)"])
        self.assertEqual(logged[0]["kind"], "discover")
        self.assertIn("== JEV DISCOVER", a.drain()[0])

    def test_questions_are_noul(self):
        qs = jev.discover_questions([{"name": "A"}, {"name": "B"}])
        self.assertEqual({q["type"] for q in qs.values()}, {"noul"})
        self.assertEqual({q["type"] for q in jev.mulligan_questions(offered("A")).values()}, {"noul"})

    def test_available_means_a_key(self):
        with mock.patch.dict("os.environ", {"HS_JEV": "1"}, clear=True):
            self.assertFalse(jev.jev_available())
            self.assertIsNone(jev.advisor_from_env())
        with mock.patch.dict("os.environ", {"TYPESAFE_API_KEY": "k"}, clear=True):
            self.assertTrue(jev.jev_available())

    def test_both_mode_is_a_second_opinion_only(self):
        resp = {"answers": {"c0": {"noul": 0.9}}}
        seconds = []
        a, published, _, logged = advisor(resp, seconds=seconds)
        a.mulligan(mull_hand("Slam"), publish=False, mode="both")
        self.assertEqual(published, [])  # the coach's card stays the main one
        self.assertEqual(seconds[0]["mulligan"][0]["card"], "Slam")
        self.assertEqual(logged[0]["choices_mode"], "both")
        self.assertEqual(logged[0]["mulligan_keep"], [True])
        self.assertIn("second opinion", a.drain()[0])

    def test_discover_second_opinion_skips_main_card(self):
        seconds = []
        a, _, discovers, _ = advisor({"answers": {"o0": {"noul": 0.2}, "o1": {"noul": 0.6}}},
                                     seconds=seconds)
        snap = {"turn": 3, "me": {"hand": [], "board": []}, "opp": {"board": []}}
        a.discover(snap, {"source": "X", "options": [{"name": "A"}, {"name": "B"}]},
                   publish=False, mode="both")
        self.assertEqual(discovers, [])
        self.assertTrue(seconds[0]["discover"].startswith("Pick B"))

    def test_client_rejects_non_http_url(self):
        with self.assertRaises(ValueError):
            jev.JevClient("k", url="file:///etc/passwd")

    def test_client_sends_pinned_model(self):
        sent = {}
        client = jev.JevClient("k", post=lambda body: sent.update(json.loads(body)) or {})
        client.ask("state", {"q": {"type": "noul", "instructions": "x"}})
        self.assertEqual(sent["model"], jev.MODEL)


class TailMetaTests(unittest.TestCase):
    def test_tail_tracks_newest_games_type_and_format(self):
        import tempfile

        from hstracker.live import LiveGameTail

        pre = "D 10:00:00.0000000 GameState.DebugPrintGame() - "
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "Power.log"
            log.write_text("".join(pre + kv + "\n" for kv in (
                "BuildNumber=1", "GameType=GT_CASUAL", "FormatType=FT_STANDARD",
                "BuildNumber=1", "GameType=GT_RANKED", "FormatType=FT_WILD")), encoding="utf-8")
            tail = LiveGameTail(log)
            tail.poll()
        self.assertEqual(tail.meta, {"BuildNumber": "1", "GameType": "GT_RANKED",
                                     "FormatType": "FT_WILD"})


@unittest.skipUnless(shutil.which("awk"), "awk not installed")
class FeedFilterTests(unittest.TestCase):
    def test_jev_lines_reach_the_coach(self):
        feed = ("== TURN 2 [raw 3] (opponent's turn) — x\n"
                "== JEV MULLIGAN (0.2s) — keep: Slam 81% | toss: none\n"
                "== JEV DISCOVER (0.2s) — Tracking: Pick B\n"
                "== CHOICES MODE: both — you answer\n")
        out = subprocess.run(["awk", "-f", str(ROOT / "hearthstone-tracker" / "coach_filter.awk")],
                             input=feed, capture_output=True, text=True, check=True).stdout
        self.assertNotIn("TURN 2", out)
        self.assertIn("== JEV MULLIGAN", out)
        self.assertIn("== JEV DISCOVER", out)
        self.assertIn("== CHOICES MODE: both", out)


if __name__ == "__main__":
    unittest.main()
