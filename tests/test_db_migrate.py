"""db._migrate: old databases gain the later columns; identifiers stay literal."""
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hearthstone-tracker"))

from hstracker import db  # noqa: E402


class MigrateTest(unittest.TestCase):
    def test_old_table_gains_added_columns(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE games (id INTEGER PRIMARY KEY, start_time TEXT NOT NULL)")
        db._migrate(conn)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(games)")}
        self.assertTrue({c for c, _ in db._ADDED_COLUMNS} <= cols)
        db._migrate(conn)  # idempotent

    def test_refuses_a_non_identifier_column(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE games (id INTEGER PRIMARY KEY)")
        bad = (("x TEXT); DROP TABLE games; --", "TEXT"),)
        with mock.patch.object(db, "_ADDED_COLUMNS", bad), self.assertRaises(ValueError):
            db._migrate(conn)
        self.assertEqual(conn.execute("SELECT count(*) FROM sqlite_master WHERE name='games'").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
