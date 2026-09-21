"""SQLite database module for pi_agent.

Manages database connection, WAL mode, and schema migrations.
"""

from __future__ import annotations

import sqlite3
import zoneinfo
from datetime import datetime
from pathlib import Path
from agent.config import get_db_path
from agent.prompts import (
    system_limit_exceeded_message,
    user_event_limit_exceeded_message,
    user_limit_exceeded_message,
)

CURRENT_SCHEMA_VERSION = 5

# Model pricing (USD per 1M tokens) and daily limits. These are the highest prices
# llm.py lets OpenRouter pay, so the recorded cost is never below the real one.
INPUT_TOKEN_PRICE_PER_M = 0.30
OUTPUT_TOKEN_PRICE_PER_M = 1.20
MAX_USER_DAILY_COST_USD = 1.00
MAX_TOTAL_DAILY_COST_USD = 4.00
MAX_USER_DAILY_EVENTS = 20
RESET_TIMEZONE = "Europe/Vilnius"


class LimitExceededError(Exception):
    """Raised when daily cost or action limits are exceeded.

    str(error) is the Lithuanian message; message(language) gives it in the
    user's language.
    """

    def __init__(self, kind: str, limit: float) -> None:
        self.kind = kind
        self.limit = limit
        super().__init__(self.message("lt"))

    def message(self, language: str | None) -> str:
        if self.kind == "user":
            return user_limit_exceeded_message(self.limit, language)
        if self.kind == "system":
            return system_limit_exceeded_message(self.limit, language)
        return user_event_limit_exceeded_message(int(self.limit), language)



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
    """Applies schema migrations sequentially based on PRAGMA user_version."""
    version = get_user_version(conn)

    if version < 1:
        _migrate_to_v1(conn)
        conn.execute("PRAGMA user_version = 1;")
        conn.commit()
        version = 1

    if version < 2:
        _migrate_to_v2(conn)
        conn.execute("PRAGMA user_version = 2;")
        conn.commit()
        version = 2

    if version < 3:
        _migrate_to_v3(conn)
        conn.execute("PRAGMA user_version = 3;")
        conn.commit()
        version = 3

    if version < 4:
        _migrate_to_v4(conn)
        conn.execute("PRAGMA user_version = 4;")
        conn.commit()
        version = 4

    if version < 5:
        _migrate_to_v5(conn)
        conn.execute("PRAGMA user_version = 5;")
        conn.commit()
        version = 5


def _migrate_to_v5(conn: sqlite3.Connection) -> None:
    """Migration to schema version 5: reminders, notes, and notes_fts."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        created_by INTEGER NOT NULL,
        text TEXT NOT NULL,
        due_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        sent_at TEXT,
        failed_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders (sent_at, failed_at, due_at);
    CREATE INDEX IF NOT EXISTS idx_reminders_user ON reminders (user_id);
    CREATE INDEX IF NOT EXISTS idx_reminders_creator ON reminders (created_by);

    CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        text TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_notes_user ON notes (user_id, created_at);
    CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
        text,
        content='notes',
        content_rowid='id'
    );
    CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
        INSERT INTO notes_fts(rowid, text) VALUES (new.id, new.text);
    END;
    CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
        INSERT INTO notes_fts(notes_fts, rowid, text) VALUES('delete', old.id, old.text);
    END;
    CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
        INSERT INTO notes_fts(notes_fts, rowid, text) VALUES('delete', old.id, old.text);
        INSERT INTO notes_fts(rowid, text) VALUES (new.id, new.text);
    END;
    """)


def _migrate_to_v4(conn: sqlite3.Connection) -> None:
    """Migration to schema version 4: event_guests (invited people outside users.toml users)."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS event_guests (
        event_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        PRIMARY KEY (event_id, name),
        FOREIGN KEY (event_id) REFERENCES events (id) ON DELETE CASCADE
    );
    """)


def _migrate_to_v3(conn: sqlite3.Connection) -> None:
    """Migration to schema version 3: facts and facts_fts."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS facts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        fact TEXT NOT NULL,
        importance INTEGER NOT NULL,
        source_msg_id INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (source_msg_id) REFERENCES messages (id) ON DELETE SET NULL
    );
    CREATE INDEX IF NOT EXISTS idx_facts_user ON facts (user_id);
    CREATE INDEX IF NOT EXISTS idx_facts_user_importance ON facts (user_id, importance);

    CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
        fact,
        content='facts',
        content_rowid='id'
    );

    CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
        INSERT INTO facts_fts(rowid, fact) VALUES (new.id, new.fact);
    END;
    CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
        INSERT INTO facts_fts(facts_fts, rowid, fact) VALUES('delete', old.id, old.fact);
    END;
    CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
        INSERT INTO facts_fts(facts_fts, rowid, fact) VALUES('delete', old.id, old.fact);
        INSERT INTO facts_fts(rowid, fact) VALUES (new.id, new.fact);
    END;
    """)


def _migrate_to_v2(conn: sqlite3.Connection) -> None:
    """Migration to schema version 2: events, event_attendees, pending_approvals."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        google_event_id TEXT,
        title TEXT NOT NULL,
        starts_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        deleted_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_events_user ON events (user_id, deleted_at);
    CREATE INDEX IF NOT EXISTS idx_events_google_id ON events (google_event_id);

    CREATE TABLE IF NOT EXISTS event_attendees (
        event_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        PRIMARY KEY (event_id, user_id),
        FOREIGN KEY (event_id) REFERENCES events (id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_event_attendees_user ON event_attendees (user_id);

    CREATE TABLE IF NOT EXISTS pending_approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        tool_name TEXT NOT NULL,
        arguments TEXT NOT NULL,
        chat_id INTEGER,
        message_id INTEGER,
        expires_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending'
    );
    CREATE INDEX IF NOT EXISTS idx_pending_approvals_user ON pending_approvals (user_id, status);
    CREATE INDEX IF NOT EXISTS idx_pending_approvals_expires ON pending_approvals (expires_at);
    """)


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


def get_current_day(tz_name: str = RESET_TIMEZONE) -> str:
    """Returns today's date in YYYY-MM-DD format for the specified timezone."""
    tz = zoneinfo.ZoneInfo(tz_name)
    return datetime.now(tz).strftime("%Y-%m-%d")


def calculate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    """Calculates query cost in USD based on input and output token rates."""
    return (
        prompt_tokens * INPUT_TOKEN_PRICE_PER_M
        + completion_tokens * OUTPUT_TOKEN_PRICE_PER_M
    ) / 1_000_000.0


def record_usage(
    conn: sqlite3.Connection,
    user_id: int,
    cost_usd: float,
    events_created: int = 0,
    day: str | None = None,
) -> None:
    """Records or increments usage costs and events created for user and day."""
    if day is None:
        day = get_current_day()
    conn.execute(
        """
        INSERT INTO usage (user_id, day, cost_usd, events_created)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, day) DO UPDATE SET
            cost_usd = cost_usd + excluded.cost_usd,
            events_created = events_created + excluded.events_created;
        """,
        (user_id, day, cost_usd, events_created),
    )
    conn.commit()


def get_user_daily_cost(
    conn: sqlite3.Connection,
    user_id: int,
    day: str | None = None,
) -> float:
    """Returns the cumulative cost in USD for a user on a given day."""
    if day is None:
        day = get_current_day()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT cost_usd FROM usage WHERE user_id = ? AND day = ?;",
        (user_id, day),
    )
    row = cursor.fetchone()
    return float(row["cost_usd"]) if row else 0.0


def get_total_daily_cost(
    conn: sqlite3.Connection,
    day: str | None = None,
) -> float:
    """Returns the cumulative cost in USD across all users on a given day."""
    if day is None:
        day = get_current_day()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COALESCE(SUM(cost_usd), 0.0) AS total FROM usage WHERE day = ?;",
        (day,),
    )
    row = cursor.fetchone()
    return float(row["total"]) if row else 0.0


def check_daily_cost_limit(
    conn: sqlite3.Connection,
    user_id: int,
    day: str | None = None,
) -> None:
    """Checks user and total daily cost limits.

    Raises LimitExceededError if either limit is reached or exceeded.
    """
    if day is None:
        day = get_current_day()

    user_cost = get_user_daily_cost(conn, user_id, day=day)
    if user_cost >= MAX_USER_DAILY_COST_USD:
        raise LimitExceededError("user", MAX_USER_DAILY_COST_USD)

    total_cost = get_total_daily_cost(conn, day=day)
    if total_cost >= MAX_TOTAL_DAILY_COST_USD:
        raise LimitExceededError("system", MAX_TOTAL_DAILY_COST_USD)


def get_user_daily_events(
    conn: sqlite3.Connection,
    user_id: int,
    day: str | None = None,
) -> int:
    """Returns the number of events created by a user on a given day."""
    if day is None:
        day = get_current_day()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT events_created FROM usage WHERE user_id = ? AND day = ?;",
        (user_id, day),
    )
    row = cursor.fetchone()
    return int(row["events_created"]) if row else 0


def check_daily_event_limit(
    conn: sqlite3.Connection,
    user_id: int,
    day: str | None = None,
) -> None:
    """Checks user daily event creation limit.

    Raises LimitExceededError if limit is reached or exceeded.
    """
    if day is None:
        day = get_current_day()

    events_count = get_user_daily_events(conn, user_id, day=day)
    if events_count >= MAX_USER_DAILY_EVENTS:
        raise LimitExceededError("events", MAX_USER_DAILY_EVENTS)


