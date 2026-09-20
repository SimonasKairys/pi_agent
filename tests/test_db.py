"""Tests for agent/db.py."""

from pathlib import Path
import sqlite3
import pytest
from agent.db import (
    CURRENT_SCHEMA_VERSION,
    LimitExceededError,
    _migrate_to_v1,
    check_daily_event_limit,
    get_connection,
    get_user_daily_events,
    get_user_version,
    record_usage,
)


def test_create_new_database(tmp_path: Path):
    db_file = tmp_path / "test.db"
    conn = get_connection(db_file)

    try:
        # Check user_version matches CURRENT_SCHEMA_VERSION (2)
        assert get_user_version(conn) == CURRENT_SCHEMA_VERSION
        assert get_user_version(conn) == 2

        # Check WAL mode
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode;")
        mode = cursor.fetchone()[0]
        assert mode.lower() == "wal"

        # Check all tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row[0] for row in cursor.fetchall()}
        assert {"messages", "summaries", "usage", "events", "event_attendees", "pending_approvals"}.issubset(tables)

        # Verify messages table columns
        cursor.execute("PRAGMA table_info(messages);")
        msg_cols = {row[1] for row in cursor.fetchall()}
        assert {"id", "user_id", "role", "content", "tokens", "created_at"}.issubset(msg_cols)

        # Verify summaries table columns
        cursor.execute("PRAGMA table_info(summaries);")
        sum_cols = {row[1] for row in cursor.fetchall()}
        assert {"id", "user_id", "content", "covers_until_msg_id", "created_at"}.issubset(sum_cols)

        # Verify usage table columns
        cursor.execute("PRAGMA table_info(usage);")
        usage_cols = {row[1] for row in cursor.fetchall()}
        assert {"id", "user_id", "day", "cost_usd", "events_created"}.issubset(usage_cols)

        # Verify events table columns
        cursor.execute("PRAGMA table_info(events);")
        events_cols = {row[1] for row in cursor.fetchall()}
        assert {"id", "user_id", "google_event_id", "title", "starts_at", "created_at", "deleted_at"}.issubset(events_cols)

        # Verify event_attendees table columns
        cursor.execute("PRAGMA table_info(event_attendees);")
        attendees_cols = {row[1] for row in cursor.fetchall()}
        assert {"event_id", "user_id"}.issubset(attendees_cols)

        # Verify pending_approvals table columns
        cursor.execute("PRAGMA table_info(pending_approvals);")
        approvals_cols = {row[1] for row in cursor.fetchall()}
        assert {"id", "user_id", "tool_name", "arguments", "chat_id", "message_id", "expires_at", "status"}.issubset(approvals_cols)
    finally:
        conn.close()


def test_reopen_existing_database(tmp_path: Path):
    db_file = tmp_path / "test.db"

    # Open first time and insert test data
    conn1 = get_connection(db_file)
    conn1.execute(
        "INSERT INTO messages (user_id, role, content, tokens, created_at) VALUES (?, ?, ?, ?, ?);",
        (12345, "user", "Labas", 2, "2026-09-20T12:00:00"),
    )
    conn1.commit()
    conn1.close()

    # Open second time
    conn2 = get_connection(db_file)
    try:
        assert get_user_version(conn2) == 2
        cursor = conn2.cursor()
        cursor.execute("SELECT user_id, role, content FROM messages WHERE user_id = 12345;")
        row = cursor.fetchone()
        assert row is not None
        assert row["user_id"] == 12345
        assert row["role"] == "user"
        assert row["content"] == "Labas"
    finally:
        conn2.close()


def test_user_version_retained(tmp_path: Path):
    db_file = tmp_path / "test.db"
    conn = get_connection(db_file)
    try:
        assert get_user_version(conn) == 2
    finally:
        conn.close()


def test_unique_constraint_usage(tmp_path: Path):
    db_file = tmp_path / "test.db"
    conn = get_connection(db_file)
    try:
        conn.execute(
            "INSERT INTO usage (user_id, day, cost_usd, events_created) VALUES (?, ?, ?, ?);",
            (1001, "2026-09-20", 0.05, 1),
        )
        conn.commit()

        # Second insert for same user and day should fail unique constraint
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO usage (user_id, day, cost_usd, events_created) VALUES (?, ?, ?, ?);",
                (1001, "2026-09-20", 0.10, 2),
            )
    finally:
        conn.close()


def test_migration_v1_to_v2_preserves_data(tmp_path: Path):
    """Old version 1 database must upgrade cleanly to version 2 without data loss."""
    db_file = tmp_path / "v1.db"

    # 1. Create a pure v1 database manually
    conn_raw = sqlite3.connect(str(db_file))
    _migrate_to_v1(conn_raw)
    conn_raw.execute("PRAGMA user_version = 1;")
    conn_raw.execute(
        "INSERT INTO messages (user_id, role, content, tokens, created_at) VALUES (?, ?, ?, ?, ?);",
        (111, "user", "Svarbi žinutė iš v1", 5, "2026-09-20T10:00:00"),
    )
    conn_raw.execute(
        "INSERT INTO summaries (user_id, content, covers_until_msg_id, created_at) VALUES (?, ?, ?, ?);",
        (111, "Sena santrauka iš v1", 1, "2026-09-20T10:05:00"),
    )
    conn_raw.execute(
        "INSERT INTO usage (user_id, day, cost_usd, events_created) VALUES (?, ?, ?, ?);",
        (111, "2026-09-20", 0.42, 3),
    )
    conn_raw.commit()
    conn_raw.close()

    # 2. Open via get_connection, which triggers sequential migrations
    conn_upgraded = get_connection(db_file)
    try:
        # Schema version must now be 2
        assert get_user_version(conn_upgraded) == 2

        # Existing v1 data is completely preserved
        msg = conn_upgraded.execute("SELECT * FROM messages WHERE user_id = 111;").fetchone()
        assert msg["content"] == "Svarbi žinutė iš v1"

        summary = conn_upgraded.execute("SELECT * FROM summaries WHERE user_id = 111;").fetchone()
        assert summary["content"] == "Sena santrauka iš v1"

        usage = conn_upgraded.execute("SELECT * FROM usage WHERE user_id = 111;").fetchone()
        assert usage["cost_usd"] == 0.42
        assert usage["events_created"] == 3

        # New tables exist and can accept records
        conn_upgraded.execute(
            "INSERT INTO events (user_id, google_event_id, title, starts_at, created_at) VALUES (?, ?, ?, ?, ?);",
            (111, "gid_123", "Susitikimas", "2026-09-21T15:00", "2026-09-20T11:00"),
        )
        conn_upgraded.execute(
            "INSERT INTO event_attendees (event_id, user_id) VALUES (?, ?);",
            (1, 222),
        )
        conn_upgraded.execute(
            "INSERT INTO pending_approvals (user_id, tool_name, arguments, expires_at) VALUES (?, ?, ?, ?);",
            (111, "delete_event", '{"event_id": 1}', "2026-09-20T11:15"),
        )
        conn_upgraded.commit()

        event_row = conn_upgraded.execute("SELECT title FROM events WHERE id = 1;").fetchone()
        assert event_row["title"] == "Susitikimas"
    finally:
        conn_upgraded.close()


def test_daily_event_limit(tmp_path: Path):
    db_file = tmp_path / "limit.db"
    conn = get_connection(db_file)

    try:
        user_id = 999
        day = "2026-09-20"

        # Initially 0 events
        assert get_user_daily_events(conn, user_id, day=day) == 0
        check_daily_event_limit(conn, user_id, day=day)

        # Record 19 events - still under limit (max is 20)
        record_usage(conn, user_id=user_id, cost_usd=0.01, events_created=19, day=day)
        assert get_user_daily_events(conn, user_id, day=day) == 19
        check_daily_event_limit(conn, user_id, day=day)

        # Record 1 more event - reaches 20, limit reached
        record_usage(conn, user_id=user_id, cost_usd=0.01, events_created=1, day=day)
        assert get_user_daily_events(conn, user_id, day=day) == 20
        with pytest.raises(LimitExceededError) as exc_info:
            check_daily_event_limit(conn, user_id, day=day)
        assert "20" in str(exc_info.value)
        assert "Viršyta" in str(exc_info.value)
    finally:
        conn.close()
