"""Tests for agent/db.py."""

from pathlib import Path
import sqlite3
from agent.db import get_connection, get_user_version


def test_create_new_database(tmp_path: Path):
    db_file = tmp_path / "test.db"
    conn = get_connection(db_file)

    try:
        # Check user_version
        assert get_user_version(conn) == 1

        # Check WAL mode
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode;")
        mode = cursor.fetchone()[0]
        assert mode.lower() == "wal"

        # Check tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row[0] for row in cursor.fetchall()}
        assert "messages" in tables
        assert "summaries" in tables
        assert "usage" in tables

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
        assert get_user_version(conn2) == 1
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
        assert get_user_version(conn) == 1
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
        import pytest
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO usage (user_id, day, cost_usd, events_created) VALUES (?, ?, ?, ?);",
                (1001, "2026-09-20", 0.10, 2),
            )
    finally:
        conn.close()
