"""Tests for agent/db.py."""

from pathlib import Path
import sqlite3
import pytest
from agent.db import (
    CURRENT_SCHEMA_VERSION,
    LimitExceededError,
    _migrate_to_v1,
    _migrate_to_v2,
    _migrate_to_v3,
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
        # Check user_version matches CURRENT_SCHEMA_VERSION (3)
        assert get_user_version(conn) == CURRENT_SCHEMA_VERSION
        assert get_user_version(conn) == 3

        # Check WAL mode
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode;")
        mode = cursor.fetchone()[0]
        assert mode.lower() == "wal"

        # Check all tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row[0] for row in cursor.fetchall()}
        assert {
            "messages",
            "summaries",
            "usage",
            "events",
            "event_attendees",
            "pending_approvals",
            "facts",
            "facts_fts",
        }.issubset(tables)

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

        # Verify facts table columns
        cursor.execute("PRAGMA table_info(facts);")
        facts_cols = {row[1] for row in cursor.fetchall()}
        assert {"id", "user_id", "fact", "importance", "source_msg_id", "created_at", "updated_at"}.issubset(facts_cols)
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
        assert get_user_version(conn2) == 3
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
        assert get_user_version(conn) == 3
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


def test_migration_v1_to_v3_preserves_data(tmp_path: Path):
    """Old version 1 database must upgrade cleanly to version 3 without data loss."""
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
        # Schema version must now be 3
        assert get_user_version(conn_upgraded) == 3

        # Existing v1 data is completely preserved
        msg = conn_upgraded.execute("SELECT * FROM messages WHERE user_id = 111;").fetchone()
        assert msg["content"] == "Svarbi žinutė iš v1"

        summary = conn_upgraded.execute("SELECT * FROM summaries WHERE user_id = 111;").fetchone()
        assert summary["content"] == "Sena santrauka iš v1"

        usage = conn_upgraded.execute("SELECT * FROM usage WHERE user_id = 111;").fetchone()
        assert usage["cost_usd"] == 0.42
        assert usage["events_created"] == 3

        # Tables from v2 and v3 exist and can accept records
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
        conn_upgraded.execute(
            "INSERT INTO facts (user_id, fact, importance, source_msg_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?);",
            (111, "Mėgsta rytinę kavą 8:00", 8, None, "2026-09-20T11:20", "2026-09-20T11:20"),
        )
        conn_upgraded.commit()

        event_row = conn_upgraded.execute("SELECT title FROM events WHERE id = 1;").fetchone()
        assert event_row["title"] == "Susitikimas"

        fact_row = conn_upgraded.execute("SELECT * FROM facts WHERE id = 1;").fetchone()
        assert fact_row["fact"] == "Mėgsta rytinę kavą 8:00"
    finally:
        conn_upgraded.close()


def test_migration_v2_to_v3_preserves_data(tmp_path: Path):
    """Old version 2 database must upgrade cleanly to version 3 without data loss and with working FTS5."""
    db_file = tmp_path / "v2.db"

    # 1. Create a pure v2 database manually
    conn_raw = sqlite3.connect(str(db_file))
    _migrate_to_v1(conn_raw)
    _migrate_to_v2(conn_raw)
    conn_raw.execute("PRAGMA user_version = 2;")
    conn_raw.execute(
        "INSERT INTO messages (user_id, role, content, tokens, created_at) VALUES (?, ?, ?, ?, ?);",
        (222, "user", "Žinutė iš v2", 4, "2026-09-20T10:00:00"),
    )
    conn_raw.execute(
        "INSERT INTO summaries (user_id, content, covers_until_msg_id, created_at) VALUES (?, ?, ?, ?);",
        (222, "Santrauka iš v2", 1, "2026-09-20T10:05:00"),
    )
    conn_raw.execute(
        "INSERT INTO usage (user_id, day, cost_usd, events_created) VALUES (?, ?, ?, ?);",
        (222, "2026-09-20", 0.15, 1),
    )
    conn_raw.execute(
        "INSERT INTO events (user_id, google_event_id, title, starts_at, created_at) VALUES (?, ?, ?, ?, ?);",
        (222, "gid_v2", "Planavimas v2", "2026-09-22T09:00", "2026-09-20T10:10"),
    )
    conn_raw.execute(
        "INSERT INTO event_attendees (event_id, user_id) VALUES (?, ?);",
        (1, 333),
    )
    conn_raw.execute(
        "INSERT INTO pending_approvals (user_id, tool_name, arguments, expires_at, status) VALUES (?, ?, ?, ?, ?);",
        (222, "update_event", '{"event_id": 1}', "2026-09-20T10:30", "pending"),
    )
    conn_raw.commit()
    conn_raw.close()

    # 2. Open via get_connection, which triggers sequential migrations
    conn_upgraded = get_connection(db_file)
    try:
        # Schema version must now be 3
        assert get_user_version(conn_upgraded) == 3

        # Existing v2 data is completely preserved
        assert conn_upgraded.execute("SELECT content FROM messages WHERE user_id = 222;").fetchone()["content"] == "Žinutė iš v2"
        assert conn_upgraded.execute("SELECT content FROM summaries WHERE user_id = 222;").fetchone()["content"] == "Santrauka iš v2"
        assert conn_upgraded.execute("SELECT cost_usd FROM usage WHERE user_id = 222;").fetchone()["cost_usd"] == 0.15
        assert conn_upgraded.execute("SELECT title FROM events WHERE id = 1;").fetchone()["title"] == "Planavimas v2"
        assert conn_upgraded.execute("SELECT user_id FROM event_attendees WHERE event_id = 1;").fetchone()["user_id"] == 333
        assert conn_upgraded.execute("SELECT status FROM pending_approvals WHERE id = 1;").fetchone()["status"] == "pending"

        # 3. Test facts and facts_fts external content table
        conn_upgraded.execute(
            "INSERT INTO facts (user_id, fact, importance, source_msg_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?);",
            (222, "Gyvena Vilniuje, senamiestyje", 9, 1, "2026-09-20T11:00", "2026-09-20T11:00"),
        )
        conn_upgraded.execute(
            "INSERT INTO facts (user_id, fact, importance, source_msg_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?);",
            (333, "Dirba nuotoliniu būdu iš Kauno", 8, None, "2026-09-20T11:01", "2026-09-20T11:01"),
        )
        conn_upgraded.commit()

        # FTS search matches
        res_vilnius = conn_upgraded.execute(
            "SELECT rowid, fact FROM facts_fts WHERE facts_fts MATCH 'Vilniuje';"
        ).fetchall()
        assert len(res_vilnius) == 1
        assert res_vilnius[0]["rowid"] == 1
        assert "senamiestyje" in res_vilnius[0]["fact"]

        # Search Kaunas
        res_kaunas = conn_upgraded.execute(
            "SELECT rowid, fact FROM facts_fts WHERE facts_fts MATCH 'Kauno';"
        ).fetchall()
        assert len(res_kaunas) == 1
        assert res_kaunas[0]["rowid"] == 2

        # Test trigger on UPDATE
        conn_upgraded.execute(
            "UPDATE facts SET fact = 'Gyvena Klaipėdoje prie jūros', updated_at = '2026-09-20T12:00' WHERE id = 1;"
        )
        conn_upgraded.commit()

        res_old = conn_upgraded.execute(
            "SELECT rowid, fact FROM facts_fts WHERE facts_fts MATCH 'Vilniuje';"
        ).fetchall()
        assert len(res_old) == 0

        res_new = conn_upgraded.execute(
            "SELECT rowid, fact FROM facts_fts WHERE facts_fts MATCH 'Klaipėdoje';"
        ).fetchall()
        assert len(res_new) == 1
        assert res_new[0]["rowid"] == 1

        # Test trigger on DELETE
        conn_upgraded.execute("DELETE FROM facts WHERE id = 1;")
        conn_upgraded.commit()

        res_deleted = conn_upgraded.execute(
            "SELECT rowid, fact FROM facts_fts WHERE facts_fts MATCH 'Klaipėdoje';"
        ).fetchall()
        assert len(res_deleted) == 0
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
