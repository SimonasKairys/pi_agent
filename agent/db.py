"""SQLite database module for pi_agent.

Manages database connection, WAL mode, and schema migrations.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from agent.config import get_db_path

CURRENT_SCHEMA_VERSION = 1


def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Connects to SQLite database, enables WAL mode, and applies migrations."""
    path = Path(db_path) if db_path is not None else get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row

    # Performance and concurrency settings
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA foreign_keys = ON;")

    apply_migrations(conn)
    return conn


def get_user_version(conn: sqlite3.Connection) -> int:
    """Returns the current PRAGMA user_version."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA user_version;")
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Applies schema migrations based on PRAGMA user_version."""
    version = get_user_version(conn)

    if version < 1:
        _migrate_to_v1(conn)
        conn.execute("PRAGMA user_version = 1;")
        conn.commit()


def _migrate_to_v1(conn: sqlite3.Connection) -> None:
    """Migration to schema version 1: messages, summaries, usage."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        tokens INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_messages_user_created ON messages (user_id, created_at);

    CREATE TABLE IF NOT EXISTS summaries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        content TEXT NOT NULL,
        covers_until_msg_id INTEGER NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_summaries_user ON summaries (user_id, created_at);

    CREATE TABLE IF NOT EXISTS usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        cost_usd REAL NOT NULL DEFAULT 0.0,
        events_created INTEGER NOT NULL DEFAULT 0
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_user_day ON usage (user_id, day);
    """)
