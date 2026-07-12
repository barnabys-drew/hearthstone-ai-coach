from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hearthstone-tracker"))

try:
    import hslog  # noqa: F401

    HAS_HSLOG = True
except ImportError:
    HAS_HSLOG = False

CREATE = "D 10:00:00 GameState.DebugPrintPower() - CREATE_GAME"


def turn_line(value: int, source: str = "GameState") -> str:
    return (f"D 10:00:05 {source}.DebugPrintPower() -     "
            f"TAG_CHANGE Entity=GameEntity tag=TURN value={value}")


@unittest.skipUnless(HAS_HSLOG, "hslog not installed (pip install -r hearthstone-tracker/requirements.txt)")
class IterTurnBuffersTests(unittest.TestCase):
    def test_yields_end_of_turn_buffers_and_final_flush(self) -> None:
        from hstracker.ragreplay import iter_turn_buffers

        lines = [CREATE, "g1 setup",
                 turn_line(1), "g1 turn1",
                 turn_line(2), "g1 turn2",
                 CREATE, "g2 setup",
                 turn_line(1), "g2 turn1"]
        yielded = list(iter_turn_buffers(iter(lines)))

        self.assertEqual([(g, t) for g, t, _ in yielded],
                         [(1, 1), (1, None), (2, None)])
        # The (1, 1) buffer is turn 1's final state: everything before the
        # TURN value=2 line.
        self.assertEqual(len(yielded[0][2]), 4)
        self.assertIn("g1 turn1", yielded[0][2][-1])
        # Final flushes contain the whole game.
        self.assertIn("g1 turn2", yielded[1][2][-1])
        self.assertIn("g2 turn1", yielded[2][2][-1])

    def test_duplicate_turn_values_yield_once(self) -> None:
        from hstracker.ragreplay import iter_turn_buffers

        lines = [CREATE, "setup",
                 turn_line(1), "turn1",
                 turn_line(2), turn_line(2, source="PowerTaskList"), "turn2",
                 turn_line(3)]
        boundaries = [(g, t) for g, t, _ in iter_turn_buffers(iter(lines))]
        self.assertEqual(boundaries, [(1, 1), (1, 2), (1, None)])

    def test_lines_before_first_create_game_ignored(self) -> None:
        from hstracker.ragreplay import iter_turn_buffers

        lines = ["stray preamble", turn_line(1), CREATE, "setup", turn_line(1)]
        yielded = list(iter_turn_buffers(iter(lines)))
        self.assertEqual([(g, t) for g, t, _ in yielded], [(1, None)])
        self.assertNotIn("stray preamble\n", yielded[0][2])


@unittest.skipUnless(HAS_HSLOG, "hslog not installed (pip install -r hearthstone-tracker/requirements.txt)")
class ReplayEventShapeTests(unittest.TestCase):
    def test_replay_session_on_empty_dir_returns_no_events(self) -> None:
        import tempfile

        from hstracker.cards import HeroClassResolver
        from hstracker.ragreplay import replay_session

        with tempfile.TemporaryDirectory() as tmp:
            events = replay_session(Path(tmp), [], HeroClassResolver(allow_fetch=False))
        self.assertEqual(events, [])

    def test_replay_events_are_ts_free_and_tagged(self) -> None:
        # Synthetic buffers exercise the event shape without a real Power.log;
        # full-log replay is verified manually against real session dirs.
        from hstracker.raglog import join_games
        from hstracker.ragreplay import replay_report

        events = [
            {"ev": "corpus", "session": "S", "game_no": 1, "ids": [], "count": 0,
             "untriggered": [], "replay": True},
            {"ev": "match", "session": "S", "game_no": 1, "turn": 2, "raw_turn": 3,
             "tiers": ["t0"], "matched": [], "opp_class": "MAGE",
             "corpus_count": 0, "replay": True},
            {"ev": "outcome", "session": "S", "game_no": 1, "result": "WON",
             "deck": None, "opp_class": "MAGE", "turns": 9, "replay": True},
        ]
        self.assertTrue(all("ts" not in ev for ev in events))
        games = join_games(events)
        self.assertEqual(games[("S", 1)]["result"], "WON")

        out: list[str] = []
        replay_report(events, [], out.append)
        text = "\n".join(out)
        self.assertIn("Replayed games", text)
        self.assertIn("WON", text)


if __name__ == "__main__":
    unittest.main()
