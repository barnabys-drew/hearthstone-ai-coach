from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hearthstone-tracker"))

from hstracker.checks import format_checks, live_checks  # noqa: E402


def minion(name, atk=1, health=1, flags=None, text="", location=False) -> dict:
    return {"name": name, "atk": atk, "health": health, "pos": 1,
            "flags": flags or [], "location": location, "text": text}


def card(name, cost=1, ctype="SPELL", text="") -> dict:
    return {"name": name, "cost": cost, "type": ctype, "text": text}


def snap(me=None, opp=None) -> dict:
    base_me = {"hp": 30, "armor": 0, "attack": 0, "mana": 1, "mana_max": 1,
               "weapon": None, "hand": [], "board": []}
    base_opp = {"hp": 30, "armor": 0, "attack": 0, "weapon": None,
                "hand": [], "board": []}
    base_me.update(me or {})
    base_opp.update(opp or {})
    return {"turn": 1, "whose_turn": "me", "me": base_me, "opp": base_opp}


def names(result) -> set[str]:
    return {c["name"] for c in result["checks"]}


class TestTrivial(unittest.TestCase):
    def test_empty_board_turn_one_is_trivial(self):
        r = live_checks(snap(me={"hand": [card("Petal Peddler", ctype="MINION")]}))
        self.assertEqual(r["klass"], "TRIVIAL")
        self.assertEqual(r["checks"], [])

    def test_trivial_marker_says_so(self):
        lines = format_checks(live_checks(snap()))
        self.assertIn("decision class: TRIVIAL", lines[0])
        self.assertIn("checks live (0)", lines[1])

    def test_coin_alone_makes_it_standard(self):
        r = live_checks(snap(me={"hand": [card("The Coin", cost=0)]}))
        self.assertEqual(r["klass"], "STANDARD")
        self.assertEqual(names(r), {"coin"})


class TestLethalGate(unittest.TestCase):
    def test_small_early_board_does_not_trigger_lethal(self):
        r = live_checks(snap(me={"board": [minion("Wisp", atk=1)]}))
        self.assertNotIn("lethal", names(r))

    def test_lethal_fires_when_ceiling_reaches_face(self):
        r = live_checks(snap(
            me={"board": [minion("Big", atk=8), minion("Big2", atk=8)]},
            opp={"hp": 12},
        ))
        self.assertIn("lethal", names(r))
        self.assertEqual(r["klass"], "CRITICAL")

    def test_armor_counts_toward_their_effective_hp(self):
        r = live_checks(snap(
            me={"board": [minion("Big", atk=10)]}, opp={"hp": 8, "armor": 5},
        ))
        self.assertNotIn("lethal", names(r))

    def test_burn_in_hand_counts_toward_ceiling(self):
        r = live_checks(snap(
            me={"hand": [card("Fireball", text="Deal 6 damage.")]}, opp={"hp": 5},
        ))
        self.assertIn("lethal", names(r))

    def test_minion_only_damage_is_not_face_damage(self):
        r = live_checks(snap(
            me={"hand": [card("Torch", text="Deal 8 damage to a damaged minion.")]},
            opp={"hp": 8},
        ))
        self.assertNotIn("lethal", names(r))

    def test_locations_cannot_attack(self):
        r = live_checks(snap(
            me={"board": [minion("Sanguine Depths", atk=9, location=True)]},
            opp={"hp": 6},
        ))
        self.assertNotIn("lethal", names(r))


class TestSurvivalGate(unittest.TestCase):
    def test_survival_fires_when_their_swing_kills_you(self):
        r = live_checks(snap(
            me={"hp": 6},
            opp={"board": [minion("Force-Tank MAX", atk=7, health=7)]},
        ))
        self.assertIn("survival", names(r))
        self.assertEqual(r["klass"], "CRITICAL")

    def test_windfury_swings_twice(self):
        r = live_checks(snap(
            me={"hp": 8},
            opp={"board": [minion("Windfury Harpy", atk=4, flags=["windfury"])]},
        ))
        self.assertIn("survival", names(r))

    def test_your_armor_saves_you(self):
        r = live_checks(snap(
            me={"hp": 4, "armor": 10},
            opp={"board": [minion("Boar", atk=5)]},
        ))
        self.assertNotIn("survival", names(r))


class TestTauntRegression(unittest.TestCase):
    """The bug from commit 0c3e262: a lethal plan cleared ONE taunt and then
    counted the attacker blocked by a SECOND taunt as face damage. The gate
    must enumerate every taunt by name, not merely flag that taunts exist."""

    def test_both_simultaneous_taunts_are_named(self):
        r = live_checks(snap(
            me={"board": [minion("A", atk=10), minion("B", atk=10)]},
            opp={"hp": 14, "board": [
                minion("Stonehill Defender", atk=1, health=4, flags=["taunt"]),
                minion("Voidwalker", atk=1, health=3, flags=["taunt"]),
            ]},
        ))
        self.assertIn("taunt", names(r))
        self.assertEqual(r["klass"], "CRITICAL")  # lethal is live -> full rigor
        detail = next(c["detail"] for c in r["checks"] if c["name"] == "taunt")
        self.assertIn("2 on their board", detail)
        self.assertIn("Stonehill Defender 1/4", detail)
        self.assertIn("Voidwalker 1/3", detail)

    def test_no_taunt_check_on_a_clean_board(self):
        r = live_checks(snap(opp={"board": [minion("Boar", atk=1)]}))
        self.assertNotIn("taunt", names(r))


class TestRemainingGates(unittest.TestCase):
    def test_hero_attack_only_when_armed(self):
        self.assertNotIn("hero-attack", names(live_checks(snap())))
        r = live_checks(snap(me={"attack": 3}))
        self.assertIn("hero-attack", names(r))

    def test_deathrattle_detected_on_either_board(self):
        r = live_checks(snap(
            opp={"board": [minion("Egg", text="Deathrattle: Summon a 2/2.")]},
        ))
        self.assertIn("deathrattle", names(r))

    def test_random_splash_detected_in_hand(self):
        r = live_checks(snap(
            me={"hand": [card("Volcano", text="Deal 15 damage randomly split.")]},
        ))
        self.assertIn("random-splash", names(r))

    def test_splash_location_on_board_is_a_hazard(self):
        """Erupting Volcano is a Location: it sits on the board and is
        activated from there. Scanning only the hand missed it on a real
        snapshot."""
        r = live_checks(snap(me={"board": [minion(
            "Erupting Volcano", atk=0, health=1, location=True,
            text="Deal 3 damage randomly split among enemies.")]}))
        self.assertIn("random-splash", names(r))

    def test_random_damage_at_a_target_is_a_splash_hazard(self):
        r = live_checks(snap(me={"hand": [card(
            "Lotus Troublemaker",
            text="Battlecry: Deal 2 damage to a random enemy.")]}))
        self.assertIn("random-splash", names(r))

    def test_random_generation_is_not_a_splash_hazard(self):
        """Randomness that only *makes cards* cannot kill the target that a
        later step depends on. A bare /random/ match fired on 83% of real
        turns; these are the effects that made it useless."""
        for name, text in [
            ("Shadowed Informant", "Battlecry: Discover a spell from your class."),
            ("Thief's Tools", "Get two random 4-Cost spells. Reduce their Costs by (2)."),
            ("Flashback", "Summon two random 1-Cost minions from the past."),
            ("Shadowflame Suffusion", "Deal 2 damage. Discover a Warrior minion."),
            ("Talanji's Last Stand",
             'Give your minions "Deathrattle: Summon a random 4-Cost minion."'),
        ]:
            r = live_checks(snap(me={"hand": [card(name, text=text)]}))
            self.assertNotIn("random-splash", names(r), f"{name} should not fire")

    def test_aoe_names_their_low_hp_minions(self):
        """Real miss: Searing Fissure (1 damage to all) would have killed the
        opponent's own 2/1 Vendor. The gate surfaces the minions at risk."""
        r = live_checks(snap(
            me={"hand": [card("Searing Fissure",
                              text="Deal 1 damage to all minions.")]},
            opp={"board": [minion("Vendor", atk=2, health=1),
                           minion("Bruiser", atk=5, health=6)]},
        ))
        self.assertIn("aoe", names(r))
        detail = next(c["detail"] for c in r["checks"] if c["name"] == "aoe")
        self.assertIn("Vendor 2/1", detail)
        self.assertNotIn("Bruiser", detail)

    def test_no_aoe_check_without_an_aoe_card(self):
        r = live_checks(snap(me={"hand": [card("Fireball", text="Deal 6 damage.")]}))
        self.assertNotIn("aoe", names(r))

    def test_notable_flags_listed_with_owner(self):
        r = live_checks(snap(
            opp={"board": [minion("Tank", atk=7, health=7,
                                  flags=["divine shield", "exhausted"])]},
        ))
        self.assertIn("flags", names(r))
        detail = next(c["detail"] for c in r["checks"] if c["name"] == "flags")
        self.assertIn("Tank [divine shield]", detail)
        self.assertNotIn("exhausted", detail)  # not notable; every fresh minion has it


if __name__ == "__main__":
    unittest.main()
