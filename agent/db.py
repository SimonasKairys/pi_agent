"""SQLite database module for pi_agent.

Manages database connection, WAL mode, and schema migrations.
"""

from __future__ import annotations

import sqlite3
import zoneinfo
from datetime import datetime
from pathlib import Path
from agent.config import get_db_path

CURRENT_SCHEMA_VERSION = 1

# Pricing and limits from TASK.md "Sprendimai ir skaičiai"
INPUT_TOKEN_PRICE_PER_M = 0.15
OUTPUT_TOKEN_PRICE_PER_M = 0.60
MAX_USER_DAILY_COST_USD = 1.00
MAX_TOTAL_DAILY_COST_USD = 4.00
RESET_TIMEZONE = "Europe/Vilnius"


class LimitExceededError(Exception):
    """Raised when daily cost or action limits are exceeded."""
    pass



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
        raise LimitExceededError(
            f"Viršyta jūsų dienos naudojimo riba ({MAX_USER_DAILY_COST_USD:.2f} USD). Bandykite rytoj."
        )

    total_cost = get_total_daily_cost(conn, day=day)
    if total_cost >= MAX_TOTAL_DAILY_COST_USD:
        raise LimitExceededError(
            f"Viršyta bendra sistemos dienos naudojimo riba ({MAX_TOTAL_DAILY_COST_USD:.2f} USD). Bandykite rytoj."
        )

