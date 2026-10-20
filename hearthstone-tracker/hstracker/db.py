"""SQLite storage for captured games."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .capture import GameRecord, record_dict

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY,
    start_time TEXT NOT NULL,
    end_time TEXT,
    duration_seconds INTEGER,
    game_type TEXT,
    format_type TEXT,
    scenario_id INTEGER,
    build_number INTEGER,
    friendly_name TEXT,
    friendly_class TEXT,
    friendly_hero TEXT,
    opponent_name TEXT,
    opponent_class TEXT,
    opponent_hero TEXT,
    friendly_first INTEGER,
    result TEXT,
    turns INTEGER,
    bg_place INTEGER,
    source_file TEXT,
    deck_name TEXT,
    deckstring TEXT,
    bg_tech INTEGER,
    UNIQUE (start_time, friendly_name, opponent_name)
);

CREATE TABLE IF NOT EXISTS game_cards (
    game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    card_id TEXT NOT NULL,
    friendly INTEGER NOT NULL,
    drawn INTEGER NOT NULL DEFAULT 0,
    played INTEGER NOT NULL DEFAULT 0,
    mull_offered INTEGER NOT NULL DEFAULT 0,
    mull_kept INTEGER NOT NULL DEFAULT 0,
    first_played_turn INTEGER,
    PRIMARY KEY (game_id, card_id, friendly)
);
"""

_COLUMNS = [
    "start_time", "end_time", "duration_seconds", "game_type", "format_type",
    "scenario_id", "build_number", "friendly_name", "friendly_class",
    "friendly_hero", "opponent_name", "opponent_class", "opponent_hero",
    "friendly_first", "result", "turns", "bg_place", "source_file",
    "deck_name", "deckstring", "bg_tech",
]

_INSERT = (
    f"INSERT OR IGNORE INTO games ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join(':' + c for c in _COLUMNS)})"
)


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


# Columns added after the first release. SQLite cannot bind identifiers as
# parameters, so these are interpolated: they must stay literals defined here.
_ADDED_COLUMNS = (("deck_name", "TEXT"), ("deckstring", "TEXT"), ("bg_tech", "INTEGER"))
_COLUMN_TYPES = {"TEXT", "INTEGER", "REAL"}


def _migrate(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(games)")}
    with conn:
        for column, col_type in _ADDED_COLUMNS:
            if column not in existing:
                if not column.isidentifier() or col_type not in _COLUMN_TYPES:
                    raise ValueError(f"refusing to add column {column!r} {col_type!r}")
                # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
                conn.execute(f"ALTER TABLE games ADD COLUMN {column} {col_type}")


_INSERT_CARD = """
INSERT OR IGNORE INTO game_cards
    (game_id, card_id, friendly, drawn, played, mull_offered, mull_kept, first_played_turn)
VALUES
    (:game_id, :card_id, :friendly, :drawn, :played, :mull_offered, :mull_kept, :first_played_turn)
"""


def save_games(conn: sqlite3.Connection, records: list[GameRecord]) -> int:
    """Insert records and their card events, skipping duplicates.

    Card rows are also written for already-known games that lack them, so a
    re-backfill fills in card data for games captured before this existed.
    Returns the number of games actually inserted.
    """
    inserted = 0
    with conn:
        for record in records:
            cur = conn.execute(_INSERT, record_dict(record))
            inserted += cur.rowcount
            if cur.rowcount:
                game_id = cur.lastrowid
            else:
                row = conn.execute(
                    "SELECT id FROM games WHERE start_time = ? "
                    "AND friendly_name IS ? AND opponent_name IS ?",
                    (record.start_time, record.friendly_name, record.opponent_name),
                ).fetchone()
                game_id = row["id"] if row else None
                if game_id is not None and record.bg_place is not None:
                    # Upgrade games recorded before BG extraction existed.
                    conn.execute(
                        "UPDATE games SET bg_place = ?, bg_tech = ?, friendly_hero = ? "
                        "WHERE id = ? AND bg_place IS NULL",
                        (record.bg_place, record.bg_tech, record.friendly_hero, game_id),
                    )
            if game_id is not None:
                for card in record.cards:
                    conn.execute(_INSERT_CARD, {**card, "game_id": game_id})
    return inserted
