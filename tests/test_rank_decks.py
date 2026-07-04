from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hearthstone-deck-recommender" / "scripts"))

import rank_decks as r  # noqa: E402


class NormalizeCollectionTests(unittest.TestCase):
    def test_hsreplay_wrapper_sums_all_finishes(self) -> None:
        raw = {"collection": {"101": [1, 1, 0, 0], "102": [2, 0, 0, 0]}, "dust": 500}
        self.assertEqual(r.normalize_collection(raw), {101: 2, 102: 2})

    def test_plain_dbf_to_count_map(self) -> None:
        self.assertEqual(r.normalize_collection({"101": 2, "102": 1}), {101: 2, 102: 1})

    def test_non_numeric_keys_are_skipped(self) -> None:
        self.assertEqual(r.normalize_collection({"101": 2, "meta": 9}), {101: 2})

    def test_list_of_dicts_with_count(self) -> None:
        raw = [{"dbfId": 101, "count": 2}, {"dbf_id": 102, "count": 1}]
        self.assertEqual(r.normalize_collection(raw), {101: 2, 102: 1})

    def test_dict_value_finishes_are_summed(self) -> None:
        self.assertEqual(r._owned_from_value({"normal": 1, "golden": 1, "diamond": 1}), 3)
        self.assertEqual(r._owned_from_value({"ownedTotal": 2}), 2)

    def test_unrecognized_format_raises(self) -> None:
        with self.assertRaises(ValueError):
            r.normalize_collection("not a collection")


class NormalizeCollectionTextTests(unittest.TestCase):
    def test_csv_with_owned_column(self) -> None:
        text = "dbfId,owned\n101,2\n102,1\nbad,3\n"
        self.assertEqual(r.normalize_collection_text(text), {101: 2, 102: 1})

    def test_csv_without_dbfid_column_raises(self) -> None:
        with self.assertRaises(ValueError):
            r.normalize_collection_text("name,count\nFireball,2\n")

    def test_json_text_dispatches_to_normalize_collection(self) -> None:
        self.assertEqual(r.normalize_collection_text('{"101": 2}'), {101: 2})


class RarityCostTests(unittest.TestCase):
    def test_core_set_is_free(self) -> None:
        self.assertEqual(r.rarity_cost({"set": "CORE", "rarity": "LEGENDARY"}), 0)

    def test_legendary_costs_1600(self) -> None:
        self.assertEqual(r.rarity_cost({"set": "TITANS", "rarity": "LEGENDARY"}), 1600)

    def test_unknown_rarity_costs_nothing(self) -> None:
        self.assertEqual(r.rarity_cost({"set": "TITANS"}), 0)


class CookieEnvFallbackTests(unittest.TestCase):
    def run_main_and_capture_cookie(self, argv: list[str]) -> str | None:
        captured: dict[str, str | None] = {}

        def fake_source(path, url, *, cookie=None):
            captured["cookie"] = cookie
            return {}

        with mock.patch.object(r, "load_collection_source", side_effect=fake_source), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            r.main(argv)
        return captured["cookie"]

    BASE_ARGS = [
        "--collection", "examples/collection.sample.json",
        "--decks", str(ROOT / "examples" / "meta_decks.sample.json"),
        "--cards-json", str(ROOT / "examples" / "cards.sample.json"),
        "--no-fetch",
    ]

    def test_env_var_used_when_flag_absent(self) -> None:
        with mock.patch.dict(os.environ, {"HS_COLLECTION_COOKIE": "from-env"}):
            self.assertEqual(self.run_main_and_capture_cookie(self.BASE_ARGS), "from-env")

    def test_env_var_wins_over_deprecated_flag(self) -> None:
        with mock.patch.dict(os.environ, {"HS_COLLECTION_COOKIE": "from-env"}):
            argv = [*self.BASE_ARGS, "--collection-cookie", "from-flag"]
            self.assertEqual(self.run_main_and_capture_cookie(argv), "from-env")

    def test_deprecated_flag_still_works_alone(self) -> None:
        argv = [*self.BASE_ARGS, "--collection-cookie", "from-flag"]
        self.assertEqual(self.run_main_and_capture_cookie(argv), "from-flag")

    def test_cookie_file_flag_wins_over_everything(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".cookie", delete=False) as handle:
            handle.write("from-file\n")
            cookie_path = handle.name
        self.addCleanup(os.unlink, cookie_path)
        with mock.patch.dict(os.environ, {"HS_COLLECTION_COOKIE": "from-env"}):
            argv = [*self.BASE_ARGS, "--collection-cookie-file", cookie_path,
                    "--collection-cookie", "from-flag"]
            self.assertEqual(self.run_main_and_capture_cookie(argv), "from-file")


class ResolveCollectionCookieTests(unittest.TestCase):
    def test_no_sources_returns_none(self) -> None:
        self.assertIsNone(r.resolve_collection_cookie(env={}))

    def test_precedence_file_arg_then_file_env_then_env_then_arg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            arg_file = Path(tmp) / "arg.cookie"
            env_file = Path(tmp) / "env.cookie"
            arg_file.write_text("from-arg-file\n")
            env_file.write_text("from-env-file\n")
            env = {
                "HS_COLLECTION_COOKIE_FILE": str(env_file),
                "HS_COLLECTION_COOKIE": "from-env",
            }
            self.assertEqual(
                r.resolve_collection_cookie(cookie_arg="raw", cookie_file=str(arg_file), env=env),
                "from-arg-file",
            )
            self.assertEqual(
                r.resolve_collection_cookie(cookie_arg="raw", env=env),
                "from-env-file",
            )
            self.assertEqual(
                r.resolve_collection_cookie(cookie_arg="raw", env={"HS_COLLECTION_COOKIE": "from-env"}),
                "from-env",
            )
            self.assertEqual(r.resolve_collection_cookie(cookie_arg="raw", env={}), "raw")

    def test_read_cookie_file_strips_whitespace(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".cookie", delete=False) as handle:
            handle.write("  session=abc123 \n")
            cookie_path = handle.name
        self.addCleanup(os.unlink, cookie_path)
        self.assertEqual(r.read_cookie_file(cookie_path), "session=abc123")

    def test_read_cookie_file_dash_reads_stdin(self) -> None:
        with mock.patch.object(sys, "stdin", io.StringIO("session=xyz\n")):
            self.assertEqual(r.read_cookie_file("-"), "session=xyz")


class ReadLimitedTests(unittest.TestCase):
    def test_within_limit_returns_all_bytes(self) -> None:
        self.assertEqual(r.read_limited(io.BytesIO(b"abc"), 3, "test"), b"abc")

    def test_over_limit_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            r.read_limited(io.BytesIO(b"x" * 11), 10, "test")
        self.assertIn("size limit", str(ctx.exception))


class BuildOwnedPoolTests(unittest.TestCase):
    def test_empty_collection_returns_empty_pool(self) -> None:
        owned = {}
        by_dbf = {}
        pool = r.build_owned_pool(owned, by_dbf)
        self.assertEqual(pool, [])

    def test_skips_zero_count_cards(self) -> None:
        owned = {1: 0, 2: 1}
        by_dbf = {1: {"name": "Card 1"}, 2: {"name": "Card 2"}}
        pool = r.build_owned_pool(owned, by_dbf)
        self.assertEqual(len(pool), 1)
        self.assertEqual(pool[0]["dbfId"], 2)

    def test_skips_unknown_cards(self) -> None:
        owned = {1: 1, 2: 1}
        by_dbf = {1: {"name": "Card 1"}}  # Card 2 missing
        pool = r.build_owned_pool(owned, by_dbf)
        self.assertEqual(len(pool), 1)
        self.assertEqual(pool[0]["name"], "Card 1")

    def test_materializes_card_attributes(self) -> None:
        owned = {1: 2}
        by_dbf = {
            1: {
                "name": "Beast",
                "cardClass": "warrior",
                "type": "minion",
                "cost": 3,
                "race": "beast",
                "mechanics": ["taunt", "divine_shield"],
                "rarity": "rare",
            }
        }
        pool = r.build_owned_pool(owned, by_dbf)
        self.assertEqual(len(pool), 1)
        card = pool[0]
        self.assertEqual(card["name"], "Beast")
        self.assertEqual(card["cardClass"], "WARRIOR")
        self.assertEqual(card["type"], "MINION")
        self.assertEqual(card["cost"], 3)
        self.assertEqual(card["race"], "BEAST")
        self.assertEqual(card["mechanics"], ["TAUNT", "DIVINE_SHIELD"])
        self.assertEqual(card["owned_count"], 2)


class FindSubstitutesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.owned_pool = [
            {
                "dbfId": 10,
                "name": "Same Class Minion",
                "cardClass": "WARRIOR",
                "type": "MINION",
                "cost": 3,
                "race": "BEAST",
                "mechanics": ["TAUNT"],
                "rarity": "RARE",
                "owned_count": 2,
            },
            {
                "dbfId": 11,
                "name": "Wrong Class",
                "cardClass": "MAGE",
                "type": "MINION",
                "cost": 3,
                "race": "BEAST",
                "mechanics": [],
                "rarity": "COMMON",
                "owned_count": 2,
            },
            {
                "dbfId": 12,
                "name": "Wrong Type",
                "cardClass": "WARRIOR",
                "type": "SPELL",
                "cost": 3,
                "race": None,
                "mechanics": ["TAUNT"],
                "rarity": "COMMON",
                "owned_count": 2,
            },
            {
                "dbfId": 13,
                "name": "Out of Cost Window",
                "cardClass": "WARRIOR",
                "type": "MINION",
                "cost": 6,
                "race": None,
                "mechanics": [],
                "rarity": "COMMON",
                "owned_count": 2,
            },
            {
                "dbfId": 14,
                "name": "Neutral Match",
                "cardClass": "NEUTRAL",
                "type": "MINION",
                "cost": 3,
                "race": "BEAST",
                "mechanics": [],
                "rarity": "COMMON",
                "owned_count": 2,
            },
        ]

    def test_reprints_are_deduped_by_name(self) -> None:
        # The same card can exist under several dbfIds (Core vs. legacy
        # printings); suggestions must not repeat it.
        reprint_pool = [
            {
                "dbfId": dbf,
                "name": "Execute Twin",
                "cardClass": "WARRIOR",
                "type": "MINION",
                "cost": 3,
                "race": None,
                "mechanics": [],
                "rarity": "COMMON",
                "owned_count": 2,
            }
            for dbf in (20, 21, 22)
        ] + [self.owned_pool[0]]  # Same Class Minion
        missing = {
            "dbfId": 1,
            "name": "Missing",
            "type": "MINION",
            "cost": 3,
            "race": None,
            "mechanics": [],
        }
        subs = r.find_substitutes(
            missing, deck_class="WARRIOR", decoded_cards=[], owned_pool=reprint_pool
        )
        names = [s["name"] for s in subs]
        self.assertEqual(len(names), len(set(names)), f"duplicate names in {names}")
        self.assertIn("Execute Twin", names)
        self.assertIn("Same Class Minion", names)

    def test_filters_by_class_legality(self) -> None:
        missing = {
            "dbfId": 1,
            "name": "Missing",
            "type": "MINION",
            "cost": 3,
            "race": None,
            "mechanics": [],
        }
        # Wrong class should be filtered
        subs = r.find_substitutes(
            missing, deck_class="WARRIOR", decoded_cards=[], owned_pool=self.owned_pool
        )
        names = [s["name"] for s in subs]
        self.assertIn("Same Class Minion", names)
        self.assertIn("Neutral Match", names)
        self.assertNotIn("Wrong Class", names)

    def test_filters_by_type_match(self) -> None:
        missing = {
            "dbfId": 1,
            "name": "Missing Minion",
            "type": "MINION",
            "cost": 3,
            "race": None,
            "mechanics": [],
        }
        subs = r.find_substitutes(
            missing, deck_class="WARRIOR", decoded_cards=[], owned_pool=self.owned_pool
        )
        types = {s["name"]: s for s in subs}
        self.assertNotIn("Wrong Type", types)

    def test_filters_by_cost_window(self) -> None:
        missing = {
            "dbfId": 1,
            "name": "Missing",
            "type": "MINION",
            "cost": 3,
            "race": None,
            "mechanics": [],
        }
        subs = r.find_substitutes(
            missing,
            deck_class="WARRIOR",
            decoded_cards=[],
            owned_pool=self.owned_pool,
            cost_window=1,
        )
        names = [s["name"] for s in subs]
        self.assertNotIn("Out of Cost Window", names)

    def test_excludes_self(self) -> None:
        missing = {
            "dbfId": 10,
            "name": "Missing",
            "type": "MINION",
            "cost": 3,
            "race": "BEAST",
            "mechanics": ["TAUNT"],
        }
        subs = r.find_substitutes(
            missing, deck_class="WARRIOR", decoded_cards=[], owned_pool=self.owned_pool
        )
        names = [s["name"] for s in subs]
        self.assertNotIn("Same Class Minion", names)

    def test_scores_by_tribe_overlap(self) -> None:
        missing = {
            "dbfId": 1,
            "name": "Missing Beast",
            "type": "MINION",
            "cost": 3,
            "race": "BEAST",
            "mechanics": [],
        }
        subs = r.find_substitutes(
            missing, deck_class="WARRIOR", decoded_cards=[], owned_pool=self.owned_pool
        )
        self.assertGreater(len(subs), 0)
        self.assertEqual(subs[0]["match_score"], 2)

    def test_truncates_to_max_substitutes(self) -> None:
        missing = {
            "dbfId": 1,
            "name": "Missing",
            "type": "MINION",
            "cost": 3,
            "race": "BEAST",
            "mechanics": [],
        }
        subs = r.find_substitutes(
            missing,
            deck_class="WARRIOR",
            decoded_cards=[],
            owned_pool=self.owned_pool,
            max_substitutes=1,
        )
        self.assertEqual(len(subs), 1)

    def test_returns_empty_when_no_matches(self) -> None:
        missing = {
            "dbfId": 1,
            "name": "Missing",
            "type": "WEAPON",
            "cost": 3,
            "race": None,
            "mechanics": [],
        }
        subs = r.find_substitutes(
            missing, deck_class="WARRIOR", decoded_cards=[], owned_pool=self.owned_pool
        )
        self.assertEqual(subs, [])


if __name__ == "__main__":
    unittest.main()
