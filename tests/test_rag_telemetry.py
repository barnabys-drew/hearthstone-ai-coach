from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hearthstone-tracker"))

from hstracker.lessons import Lesson  # noqa: E402
from hstracker import raglog  # noqa: E402
from hstracker.raglog import (  # noqa: E402
    RagTurnLogger, append_event, join_games, lesson_id, read_events,
)


def make_lesson(text: str, **trigger) -> Lesson:
    return Lesson.model_validate({"lesson": text, "trigger": trigger})


def snap(whose="me", raw_turn=3, turn=2, phase="playing", game_over=None,
         opp_class="WARRIOR"):
    return {
        "whose_turn": whose, "raw_turn": raw_turn, "turn": turn, "phase": phase,
        "game_over": game_over,
        "me": {"board": [], "hand": []},
        "opp": {"class": opp_class, "board": [], "hand": [], "hand_hidden": 0},
    }


class LessonIdTests(unittest.TestCase):
    def test_stable_across_whitespace_and_case(self) -> None:
        self.assertEqual(lesson_id("  Trade  FIRST "), lesson_id("trade first"))

    def test_distinct_for_distinct_text(self) -> None:
        self.assertNotEqual(lesson_id("trade first"), lesson_id("go face"))

    def test_format_is_12_hex(self) -> None:
        self.assertRegex(lesson_id("anything"), r"^[0-9a-f]{12}$")


class AppendReadTests(unittest.TestCase):
    def test_round_trip_and_corrupt_line_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.jsonl"
            self.assertTrue(append_event({"ev": "match", "a": 1}, path))
            path.open("a").write("{torn line\n\n")
            self.assertTrue(append_event({"ev": "outcome"}, path))
            events = read_events(path)
            self.assertEqual([ev["ev"] for ev in events], ["match", "outcome"])
            self.assertIn("ts", events[0])

    def test_missing_file_reads_empty(self) -> None:
        self.assertEqual(read_events(Path("/nonexistent/log.jsonl")), [])


class RagTurnLoggerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "log.jsonl"
        self.rag = RagTurnLogger(self.path)
        self.corpus = [make_lesson("trade first", enemy_board=["taunt guy"]),
                       make_lesson("untriggerable one")]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def events(self):
        return read_events(self.path)

    def test_corpus_once_per_game_and_zero_match_logged(self) -> None:
        self.rag.on_snapshot(snap(), [], self.corpus, session="S", game_no=1)
        self.rag.on_snapshot(snap(raw_turn=5), [], self.corpus, session="S", game_no=1)
        kinds = [ev["ev"] for ev in self.events()]
        self.assertEqual(kinds, ["corpus", "match", "match"])
        corpus_ev = self.events()[0]
        self.assertEqual(corpus_ev["count"], 2)
        self.assertEqual(corpus_ev["untriggered"], [lesson_id("untriggerable one")])
        self.assertEqual(self.events()[1]["matched"], [])

    def test_duplicate_turn_suppressed_but_changed_set_reemits(self) -> None:
        self.rag.on_snapshot(snap(), [], self.corpus, session="S", game_no=1)
        self.rag.on_snapshot(snap(), [], self.corpus, session="S", game_no=1)
        self.assertEqual(len([e for e in self.events() if e["ev"] == "match"]), 1)
        self.rag.on_snapshot(snap(), [self.corpus[0]], self.corpus, session="S", game_no=1)
        matches = [e for e in self.events() if e["ev"] == "match"]
        self.assertEqual(len(matches), 2)
        self.assertEqual(matches[1]["matched"][0]["id"], lesson_id("trade first"))

    def test_opponent_turn_and_mulligan_skipped(self) -> None:
        self.rag.on_snapshot(snap(whose="opp"), [], self.corpus, session="S", game_no=1)
        self.rag.on_snapshot(snap(phase="mulligan"), [], self.corpus, session="S", game_no=1)
        self.assertEqual([e["ev"] for e in self.events()], ["corpus"])

    def test_outcome_once_and_game_over_snapshot_fallback(self) -> None:
        self.rag.on_snapshot(snap(game_over="WON"), [], self.corpus, session="S", game_no=1)
        self.rag.on_game_over(snap(game_over="WON"), None, session="S", game_no=1,
                              deck_name="Burn Warrior")
        outcomes = [e for e in self.events() if e["ev"] == "outcome"]
        self.assertEqual(len(outcomes), 1)  # fallback fired; on_game_over deduped
        self.assertEqual(outcomes[0]["result"], "WON")

    def test_on_game_over_prefers_record_fields(self) -> None:
        record = mock.Mock(result="LOST", deck_name="Aya Rogue",
                           opponent_class="MAGE", turns=12,
                           start_time="2026-07-12 10:00:00")
        self.rag.on_game_over(snap(game_over="LOST"), record, session="S",
                              game_no=2, deck_name=None)
        outcome = self.events()[-1]
        self.assertEqual((outcome["deck"], outcome["opp_class"], outcome["start_time"]),
                         ("Aya Rogue", "MAGE", "2026-07-12 10:00:00"))


class IngestHookTests(unittest.TestCase):
    def test_tmp_store_path_emits_nothing(self) -> None:
        from hstracker.lessons import append_lesson
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "store.json"
            log = Path(tmp) / "log.jsonl"
            with mock.patch.object(raglog, "RAG_LOG_PATH", log):
                append_lesson({"lesson": "not the real store"}, store)
            self.assertFalse(log.exists())

    def test_default_store_emits_one_ingest_and_dupe_none(self) -> None:
        import hstracker.lessons as lessons_mod
        from hstracker.lessons import append_lesson
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "store.json"
            log = Path(tmp) / "log.jsonl"
            with mock.patch.object(lessons_mod, "STORE_PATH", store), \
                 mock.patch.object(raglog, "RAG_LOG_PATH", log), \
                 mock.patch.object(lessons_mod, "mirror_store", lambda p=None: None):
                append_lesson({"lesson": "one-hit or leave it",
                               "trigger": {"enemy_board": ["Bloodhoof Brave"]}})
                append_lesson({"lesson": "one-hit or leave it"})  # exact dupe
            events = read_events(log)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["ev"], "ingest")
            self.assertEqual(events[0]["lesson_id"], lesson_id("one-hit or leave it"))
            self.assertEqual(events[0]["conds"], 1)


def match_ev(game_no, raw_turn, ids, session="S", ts=100.0):
    return {"ev": "match", "session": session, "game_no": game_no, "ts": ts,
            "raw_turn": raw_turn, "turn": (raw_turn + 1) // 2, "tiers": ["t0"],
            "matched": [{"id": i, "tier": "t0", "conds": 1} for i in ids]}


def outcome_ev(game_no, result, ts, session="S", deck="Burn Warrior"):
    return {"ev": "outcome", "session": session, "game_no": game_no, "ts": ts,
            "result": result, "deck": deck, "opp_class": "MAGE", "turns": 10}


def corpus_ev(game_no, ids, session="S", ts=90.0):
    return {"ev": "corpus", "session": session, "game_no": game_no, "ts": ts,
            "ids": ids, "count": len(ids), "untriggered": []}


class JoinTests(unittest.TestCase):
    def test_applied_joins_game_in_progress(self) -> None:
        games = join_games([
            outcome_ev(1, "WON", ts=1000),
            {"ev": "applied", "ts": 900, "lesson_ids": ["aaa"]},
        ])
        self.assertIn("aaa", games[("S", 1)]["applied_ids"])

    def test_ingest_joins_most_recent_finished_game(self) -> None:
        games = join_games([
            outcome_ev(1, "LOST", ts=1000),
            {"ev": "ingest", "ts": 1200, "lesson_id": "bbb"},
        ])
        self.assertIn("bbb", games[("S", 1)]["ingested_ids"])

    def test_out_of_window_events_stay_unjoined(self) -> None:
        games = join_games([
            outcome_ev(1, "WON", ts=1000),
            {"ev": "ingest", "ts": 1000 + raglog.INGEST_JOIN_WINDOW + 1, "lesson_id": "ccc"},
            {"ev": "applied", "ts": 1000 - raglog.APPLIED_JOIN_WINDOW - 1, "lesson_ids": ["ddd"]},
        ])
        stray = games[("", -1)]
        self.assertEqual((stray["ingested_ids"], stray["applied_ids"]), ({"ccc"}, {"ddd"}))

    def test_turn_events_union_reemitted_turns(self) -> None:
        games = join_games([
            match_ev(1, 3, []),
            match_ev(1, 3, ["aaa"]),
        ])
        self.assertEqual(games[("S", 1)]["turn_events"][3], {"aaa"})


class ReportMathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = [make_lesson("trade first", enemy_board=["taunt"]),
                      make_lesson("never fired lesson", opp_class="MAGE"),
                      make_lesson("no conditions at all")]
        self.fired_id = lesson_id("trade first")
        # Game 1 (won): lesson fired turn 3 of 2 your-turns. Game 2 (lost): silent.
        self.events = [
            corpus_ev(1, [lesson_id(rec.lesson) for rec in self.store]),
            match_ev(1, 3, [self.fired_id]),
            match_ev(1, 5, []),
            outcome_ev(1, "WON", ts=1000),
            corpus_ev(2, [lesson_id(rec.lesson) for rec in self.store], ts=1900),
            match_ev(2, 3, [], ts=2000),
            outcome_ev(2, "LOST", ts=2100),
            {"ev": "ingest", "ts": 2200, "lesson_id": "fedcba987654"},
        ]

    def test_fire_rate_denominator_uses_corpus_membership(self) -> None:
        rows = raglog.fire_rows(join_games(self.events), self.store)
        fired = next(r for r in rows if r["id"] == self.fired_id)
        # 1 fire over 3 eligible your-turns (2 in game 1 + 1 in game 2).
        self.assertEqual((fired["fires"], fired["games_fired"], fired["fire_rate_pct"]),
                         (1, 1, 33))

    def test_dead_rows_flag_never_fired_and_untriggerable(self) -> None:
        rows = raglog.dead_rows(join_games(self.events), self.store)
        ids = {r["id"] for r in rows}
        self.assertNotIn(self.fired_id, ids)
        self.assertIn(lesson_id("never fired lesson"), ids)
        untrig = next(r for r in rows if r["id"] == lesson_id("no conditions at all"))
        self.assertIn("untriggerable", untrig["note"])

    def test_miss_rows_find_ingest_with_no_fires(self) -> None:
        rows = raglog.miss_rows(join_games(self.events))
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["game"], rows[0]["result"]), (2, "LOST"))
        self.assertIn("fedcba987654", rows[0]["ingested"])

    def test_precision_fallback_without_applied(self) -> None:
        rows, has_applied = raglog.precision_rows(join_games(self.events), self.store)
        self.assertFalse(has_applied)
        row = next(r for r in rows if r["id"] == self.fired_id)
        self.assertEqual(row["win_rate_pct"], 100)
        self.assertNotIn("precision_pct", row)

    def test_precision_with_applied(self) -> None:
        events = self.events + [{"ev": "applied", "ts": 950, "lesson_ids": [self.fired_id]}]
        rows, has_applied = raglog.precision_rows(join_games(events), self.store)
        self.assertTrue(has_applied)
        row = next(r for r in rows if r["id"] == self.fired_id)
        self.assertEqual((row["applied"], row["precision_pct"]), (1, 100))

    def test_summary_counts(self) -> None:
        rows = raglog.summary_rows(join_games(self.events))
        self.assertEqual(rows[0]["games"], 2)
        self.assertEqual(rows[0]["your_turns"], 3)
        self.assertEqual(rows[0]["turns_matched"], 1)


if __name__ == "__main__":
    unittest.main()
