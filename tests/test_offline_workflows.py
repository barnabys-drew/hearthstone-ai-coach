from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


class OfflineWorkflowTests(unittest.TestCase):
    def run_cmd(self, *args: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [PYTHON, *args],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            self.fail(
                f"Command failed ({result.returncode}): {' '.join(args)}\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            )
        return result

    def test_builder_selftest(self) -> None:
        result = self.run_cmd("hearthstone-deck-builder/scripts/build_deck_code.py", "--selftest")
        self.assertIn("selftest ok", result.stdout)

    def test_builder_offline_dbf_output(self) -> None:
        result = self.run_cmd(
            "hearthstone-deck-builder/scripts/build_deck_code.py",
            "--deck-name", "Smoke",
            "--class", "Warrior",
            "--format", "wild",
            "--hero-dbf-id", "7",
            "--dbf-cards", "1:2,2:2,3:2,4:1",
            "--deck-size", "none",
            "--no-fetch",
        )
        self.assertIn("### Smoke", result.stdout)
        self.assertIn("AAEBAQcBBAMBAgMAAA==", result.stdout)

    def test_rank_decks_with_sample_fixtures(self) -> None:
        result = self.run_cmd(
            "hearthstone-deck-recommender/scripts/rank_decks.py",
            "--collection", "examples/collection.sample.json",
            "--decks", "examples/meta_decks.sample.json",
            "--cards-json", "examples/cards.sample.json",
            "--no-fetch",
        )
        self.assertIn("Sample Cheap Tempo", result.stdout)
        self.assertIn("Easiest to build: Sample Cheap Tempo", result.stdout)
        self.assertIn("0 dust", result.stdout)

    def test_rank_decks_with_file_url_collection(self) -> None:
        collection_url = (ROOT / "examples" / "collection.sample.json").as_uri()
        result = self.run_cmd(
            "hearthstone-deck-recommender/scripts/rank_decks.py",
            "--collection-url", collection_url,
            "--decks", "examples/meta_decks.sample.json",
            "--cards-json", "examples/cards.sample.json",
            "--no-fetch",
            "--json",
        )
        ranked = json.loads(result.stdout)
        self.assertEqual(ranked[0]["name"], "Sample Cheap Tempo")
        self.assertEqual(ranked[0]["dust_needed"], 0)
        self.assertEqual(ranked[1]["dust_needed"], 2500)

    def test_one_shot_wrapper_prints_import_block(self) -> None:
        result = self.run_cmd(
            "hearthstone-deck-recommender/scripts/recommend_and_import.py",
            "--collection", "examples/collection.sample.json",
            "--decks", "examples/meta_decks.sample.json",
            "--cards-json", "examples/cards.sample.json",
            "--no-fetch",
            "--top-missing", "3",
        )
        self.assertIn("COPY THIS INTO HEARTHSTONE", result.stdout)
        self.assertIn("### Sample Cheap Tempo", result.stdout)
        self.assertIn("AAEBAQcBBQEBAAA=", result.stdout)
        self.assertIn("# Format: Wild", result.stdout)


if __name__ == "__main__":
    unittest.main()
