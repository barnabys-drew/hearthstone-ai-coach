from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hearthstone-tracker"))

from hstracker.plancheck import check_plan, format_warnings  # noqa: E402


def card(name, cost):
    return {"name": name, "cost": cost}


def minion(name, atk=2, health=2, flags=()):
    return {"name": name, "atk": atk, "health": health, "flags": list(flags)}


def snap(hand=(), board=(), opp_board=(), mana=10, deck=()):
    return {"me": {"hand": list(hand), "board": list(board), "mana": mana,
                   "deck_cards_left": [{"name": n, "cost": 1, "count": 1} for n in deck]},
            "opp": {"board": list(opp_board)}}


def rules(warnings):
    return [w["rule"] for w in warnings]


class RealMissesTests(unittest.TestCase):
    """Each case is a miss recorded in hearthstone-live-coach/SKILL.md."""

    def test_second_taunt_left_standing_before_face_attacks(self):
        # Stonehill Defender AND a Voidwalker (from Voidlord's death) were both
        # Taunt; the plan cleared only Stonehill and sent the rest face.
        s = snap(board=[minion("Brood Keeper"), minion("Slitherdrake")],
                 opp_board=[minion("Stonehill Defender", 1, 4, ["taunt"]),
                            minion("Voidwalker", 1, 3, ["taunt"])])
        w = check_plan(["Attack Brood Keeper -> Stonehill Defender",
                        "Attack Slitherdrake face (5)", "End Turn"], s)
        self.assertEqual(rules(w), ["taunt"])
        self.assertIn("Voidwalker", w[0]["detail"])

    def test_reborn_minion_killed_once(self):
        s = snap(hand=[card("Slam", 1)], opp_board=[minion("Whelp of the Infinite", 3, 2, ["reborn"])])
        w = check_plan(["Cast Slam -> Whelp of the Infinite (kills it)", "End Turn"], s)
        self.assertEqual(rules(w), ["reborn"])
        ok = check_plan(["Cast Slam -> Whelp of the Infinite (kills it)",
                         "Attack Whelp of the Infinite's 1/1 Reborn copy with the hero"], s)
        self.assertEqual(ok, [], "a plan that budgets the second life is fine")

    def test_more_mana_than_available(self):
        s = snap(hand=[card("Windpeak Wyrm", 5), card("Precursory Strike", 2)], mana=6)
        w = check_plan(["Play Windpeak Wyrm (5 mana)", "Play Precursory Strike (2 mana) → 7/7"], s)
        self.assertEqual(rules(w), ["mana"])
        self.assertIn("cost 7 but only 6", w[0]["detail"])

    def test_card_that_exists_nowhere(self):
        s = snap(hand=[card("Slam", 1)], deck=["Torch"])
        w = check_plan(["Play Time-Twisted Seer (1 mana)", "Cast Slam -> Darkrider"], s)
        self.assertEqual(rules(w), ["unknown card"])
        self.assertIn("Time-Twisted Seer", format_warnings(w))


class FailOpenTests(unittest.TestCase):
    """Real step phrasings from the July telemetry that must NOT warn."""

    def test_coin_refunds_combos_and_prefixes(self):
        s = snap(hand=[card("The Coin", 0), card("Darkscale Broodmother", 2), card("Void Soul", 1),
                       card("Carrier Whelp", 1), card("Petal Peddler", 2), card("For Glory!", 5)], mana=3)
        steps = ["Play The Coin",
                 "Play Darkscale Broodmother (2 mana, refunded, holding Ysera)",
                 "Play Void Soul (1 mana)",
                 "Play Carrier Whelp + Petal Peddler with remaining mana"]
        self.assertEqual(rules(check_plan(steps, s)), [], "2 refunded + 1 + 1 + 2 = 4 <= 3 + Coin")
        self.assertEqual(rules(check_plan(["Play For Glory!"], s)), ["mana"],
                         "a name that starts with 'For' is still found and costed")

    def test_drawn_or_generated_cards_and_locations_are_not_unknown(self):
        s = snap(hand=[card("Rockskipper", 2)], board=[minion("Sanguine Depths")], deck=["Torch"])
        steps = ["Play Rockskipper (2 mana)", "Play the Rock it gives you -> face",
                 "Play Torch if you draw it", "Activate Sanguine Depths -> Cumulus",
                 "Play the discovered card"]
        self.assertEqual(check_plan(steps, s), [])

    def test_taunt_named_loosely_or_removed_by_spell_counts_as_handled(self):
        s = snap(hand=[card("Torch", 1)], board=[minion("Darkrider")],
                 opp_board=[minion("Fiery Elemental", 2, 1, ["taunt"]),
                            minion("Shade", 1, 1, ["taunt", "stealth"])])
        steps = ["Play Torch (clear Elemental)", "Attack Darkrider face (1)"]
        self.assertEqual(check_plan(steps, s), [])

    def test_no_face_attack_means_no_taunt_warning(self):
        s = snap(opp_board=[minion("Voidwalker", 1, 3, ["taunt"])])
        self.assertEqual(check_plan(["Trade Agent into Greenwing", "End Turn"], s), [])

    def test_empty_inputs(self):
        self.assertEqual(check_plan([], {}), [])


if __name__ == "__main__":
    unittest.main()
