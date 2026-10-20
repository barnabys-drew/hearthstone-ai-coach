from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hearthstone-tracker"))

from hstracker.advice import (  # noqa: E402
    adherence_score, advised_names, coach_report, contradictions,
    game_actions, latency_rows, latency_splits,
)


def advice_ev(turn, steps, ts, kind="turn", model="haiku", **extra):
    return {"ev": "advice", "advice_id": f"id{ts}", "kind": kind, "turn": turn,
            "headline": extra.pop("headline", "do it"), "steps": steps,
            "model": model, "ts": ts, **extra}


def match_ev(turn, ts, session="S", game_no=1):
    return {"ev": "match", "session": session, "game_no": game_no,
            "turn": turn, "raw_turn": 2 * turn - 1, "tiers": ["t0"],
            "matched": [], "ts": ts}


def outcome_ev(result, ts, session="S", game_no=1):
    return {"ev": "outcome", "session": session, "game_no": game_no,
            "result": result, "deck": None, "opp_class": "MAGE", "turns": 9,
            "ts": ts}


POOL = {"Foxy Fraud", "Lotus Bookie", "Blackpaw's Whip", "Eventuality",
        "The Coin"}


class AdvisedNamesTests(unittest.TestCase):
    def test_names_matched_from_pool_only(self) -> None:
        ev = advice_ev(3, ["Play Foxy Fraud (2)", "Equip Blackpaw's Whip"], 1.0)
        self.assertEqual(advised_names(ev, POOL),
                         {"Foxy Fraud", "Blackpaw's Whip"})

    def test_mulligan_cards_and_discover_count(self) -> None:
        ev = advice_ev(1, [], 1.0, kind="mulligan",
                       mulligan_cards=["Lotus Bookie"])
        self.assertEqual(advised_names(ev, POOL), {"Lotus Bookie"})

    def test_short_names_ignored(self) -> None:
        ev = advice_ev(3, ["use it"], 1.0)
        self.assertEqual(advised_names(ev, {"it", "Foxy Fraud"}), set())


class AdherenceTests(unittest.TestCase):
    def actions(self, plays_by_raw):
        return {"pool": POOL, "plays": {k: set(v) for k, v in plays_by_raw.items()}}

    def test_full_adherence(self) -> None:
        ev = advice_ev(3, ["Play Foxy Fraud", "Play Lotus Bookie"], 1.0)
        adh = adherence_score(ev, self.actions({5: ["Foxy Fraud", "Lotus Bookie"]}))
        self.assertEqual(adh["score"], 1.0)

    def test_override_scores_zero(self) -> None:
        ev = advice_ev(3, ["Play Foxy Fraud"], 1.0)
        adh = adherence_score(ev, self.actions({5: ["Eventuality"]}))
        self.assertEqual(adh["score"], 0.0)

    def test_both_raw_turns_of_display_turn_count(self) -> None:
        ev = advice_ev(3, ["Play Foxy Fraud"], 1.0)
        adh = adherence_score(ev, self.actions({6: ["Foxy Fraud"]}))
        self.assertEqual(adh["score"], 1.0)

    def test_unmeasurable_returns_none(self) -> None:
        self.assertIsNone(adherence_score(advice_ev(None, ["x"], 1.0),
                                          self.actions({})))
        self.assertIsNone(adherence_score(advice_ev(3, ["nothing known"], 1.0),
                                          self.actions({})))


class LatencySplitTests(unittest.TestCase):
    """A single global median hid a 30s p50 behind a 15s target. The splits are
    what make the gate's effect visible."""

    def events(self):
        return [
            match_ev(2, 100.0), advice_ev(2, ["a"], 105.0, variant="gated"),
            match_ev(6, 200.0), advice_ev(6, ["a"], 240.0, variant="full"),
            match_ev(10, 300.0), advice_ev(10, ["a"], 320.0, variant="full"),
        ]

    def test_buckets_split_early_mid_late(self):
        got = {r["bucket"]: r for r in latency_splits(self.events())["by_bucket"]}
        self.assertEqual(got["early (1-4)"]["median_s"], 5.0)
        self.assertEqual(got["mid (5-8)"]["median_s"], 40.0)
        self.assertEqual(got["late (9+)"]["median_s"], 20.0)

    def test_over_deadline_counted_against_the_15s_contract(self):
        got = {r["bucket"]: r for r in latency_splits(self.events())["by_bucket"]}
        self.assertEqual(got["early (1-4)"]["over_deadline"], "0/1")  # 5s is fine
        self.assertEqual(got["mid (5-8)"]["over_deadline"], "1/1")  # 40s is not

    def test_variant_arms_are_separated_for_the_ab(self):
        got = {r["variant"]: r for r in latency_splits(self.events())["by_variant"]}
        self.assertEqual(got["gated"]["n"], 1)
        self.assertEqual(got["gated"]["median_s"], 5.0)
        self.assertEqual(got["full"]["n"], 2)

    def test_untagged_advice_is_its_own_arm(self):
        evs = [match_ev(3, 10.0), advice_ev(3, ["a"], 20.0)]  # no variant
        got = {r["variant"]: r for r in latency_splits(evs)["by_variant"]}
        self.assertIn("(untagged)", got)


class ContradictionTests(unittest.TestCase):
    def test_conflicting_same_turn_advice_counts(self) -> None:
        a = advice_ev(5, ["Play Foxy Fraud"], 1.0)
        b = advice_ev(5, ["Play Eventuality"], 2.0)
        self.assertEqual(contradictions([a, b], POOL), 1)

    def test_subset_reissue_is_not_a_contradiction(self) -> None:
        a = advice_ev(5, ["Play Foxy Fraud", "Play Lotus Bookie"], 1.0)
        b = advice_ev(5, ["Play Foxy Fraud"], 2.0)
        self.assertEqual(contradictions([a, b], POOL), 0)

    def test_discover_kind_exempt(self) -> None:
        a = advice_ev(5, ["Play Foxy Fraud"], 1.0)
        d = advice_ev(5, [], 2.0, kind="discover", discover="Pick Eventuality")
        self.assertEqual(contradictions([a, d], POOL), 0)


class LatencyTests(unittest.TestCase):
    def test_advice_paired_with_preceding_marker_same_turn(self) -> None:
        events = [match_ev(3, 100.0), advice_ev(3, ["x"], 108.5)]
        self.assertEqual(latency_rows(events), [8.5])

    def test_wrong_turn_or_too_late_not_paired(self) -> None:
        events = [match_ev(3, 100.0), advice_ev(4, ["x"], 108.0),
                  match_ev(5, 200.0), advice_ev(5, ["x"], 200.0 + 500)]
        self.assertEqual(latency_rows(events), [])

    def test_jev_answers_are_not_coach_latency(self) -> None:
        events = [match_ev(3, 100.0),
                  advice_ev(3, [], 100.2, kind="discover", model="jev-1.13.0", variant="jev"),
                  advice_ev(3, ["x"], 125.0)]
        self.assertEqual(latency_rows(events), [25.0])


class CoachReportTests(unittest.TestCase):
    def test_report_joins_and_counts(self) -> None:
        events = [
            match_ev(3, 100.0),
            advice_ev(3, ["Play Foxy Fraud"], 105.0),
            advice_ev(5, ["Play Eventuality"], 130.0, kind="lethal"),
            outcome_ev("WON", 200.0),
        ]
        report = coach_report(events)
        self.assertEqual(len(report["games"]), 1)
        row = report["games"][0]
        self.assertEqual((row["advice"], row["result"]), (2, "WON"))
        self.assertIn("haiku", row["models"])
        self.assertEqual(report["latency"]["paired"], 1)
        self.assertIsNone(report["outcomes"])  # no session dir: no adherence
        self.assertEqual(report["unjoined_advice"], 0)

    def test_orphan_advice_reported_as_unjoined(self) -> None:
        report = coach_report([advice_ev(3, ["x"], 105.0)])
        self.assertEqual(report["unjoined_advice"], 1)


class GameActionsTests(unittest.TestCase):
    def test_parses_plays_per_turn_from_power_log(self) -> None:
        import tempfile
        log = (
            "GameState.DebugPrintPower() - CREATE_GAME\n"
            "TAG_CHANGE Entity=GameEntity tag=TURN value=1\n"
            "BLOCK_START BlockType=PLAY Entity=[entityName=Foxy Fraud id=33 zone=HAND zonePos=1 cardId=X player=1]\n"
            "TAG_CHANGE Entity=GameEntity tag=TURN value=2\n"
            "BLOCK_START BlockType=ATTACK Entity=[entityName=Lotus Bookie id=44 zone=PLAY zonePos=1 cardId=Y player=2]\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "Power.log").write_text(log, encoding="utf-8")
            games = game_actions(Path(tmp))
        self.assertEqual(games[1]["plays"][1], {"Foxy Fraud"})
        self.assertEqual(games[1]["plays"][2], {"Lotus Bookie"})
        self.assertIn("Foxy Fraud", games[1]["pool"])


class SelftestTests(unittest.TestCase):
    def test_all_checks_run_without_fail_status(self) -> None:
        from hstracker.selftest import format_results, run_checks
        results = run_checks()
        fails = [r for r in results if r["status"] == "FAIL"]
        self.assertEqual(fails, [], f"selftest FAILs: {fails}")
        text = format_results(results)
        self.assertIn("checks:", text)


if __name__ == "__main__":
    unittest.main()
