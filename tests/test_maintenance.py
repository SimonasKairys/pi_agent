"""Tests for agent/maintenance.py."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent.db import get_connection
from agent.maintenance import (
    delete_old_approvals,
    delete_old_messages,
    delete_old_reminders,
    run_maintenance,
    trim_journal,
)

NOW = datetime(2026, 9, 21, 1, 15, tzinfo=timezone.utc)
USER_A = 1001
USER_B = 2002


@pytest.fixture
def conn(tmp_path: Path):
    connection = get_connection(tmp_path / "maintenance.db")
    yield connection
    connection.close()


def _ago(days: int) -> str:
    return (NOW - timedelta(days=days)).isoformat()


def _message(conn, user_id: int, days_ago: int) -> int:
    cursor = conn.execute(
        "INSERT INTO messages (user_id, role, content, tokens, created_at) VALUES (?, 'user', 'x', 1, ?)",
        (user_id, _ago(days_ago)),
    )
    conn.commit()
    return cursor.lastrowid


def _summary(conn, user_id: int, covers_until: int) -> None:
    conn.execute(
        "INSERT INTO summaries (user_id, content, covers_until_msg_id, created_at) VALUES (?, 's', ?, ?)",
        (user_id, covers_until, _ago(0)),
    )
    conn.commit()


def _ids(conn) -> set[int]:
    return {row["id"] for row in conn.execute("SELECT id FROM messages")}


def test_deletes_only_old_summarized_messages(conn):
    # Each new summary includes the previous one, so the newest one covers every
    # message up to its covers_until_msg_id.
    old_first = _message(conn, USER_A, 200)
    old_second = _message(conn, USER_A, 150)
    recent = _message(conn, USER_A, 10)
    _summary(conn, USER_A, covers_until=old_first)
    _summary(conn, USER_A, covers_until=recent)
    # USER_B has an old message but no summary, and USER_A's summary must not count for it.
    other_user_old = _message(conn, USER_B, 300)

    deleted = delete_old_messages(conn, NOW)

    assert deleted == 2
    # The recent message is summarized but younger than 120 days.
    assert _ids(conn) == {recent, other_user_old}
    assert old_first not in _ids(conn) and old_second not in _ids(conn)


def test_keeps_messages_without_summary_at_any_age(conn):
    msg = _message(conn, USER_A, 400)

    assert delete_old_messages(conn, NOW) == 0
    assert _ids(conn) == {msg}


def test_deleting_message_keeps_fact_that_cites_it(conn):
    msg = _message(conn, USER_A, 200)
    _summary(conn, USER_A, covers_until=msg)
    conn.execute(
        "INSERT INTO facts (user_id, fact, importance, source_msg_id, created_at, updated_at) "
        "VALUES (?, 'Gyvena Vilniuje', 8, ?, ?, ?)",
        (USER_A, msg, _ago(200), _ago(200)),
    )
    conn.commit()

    assert delete_old_messages(conn, NOW) == 1
    row = conn.execute("SELECT fact, source_msg_id FROM facts").fetchone()
    assert row["fact"] == "Gyvena Vilniuje"
    assert row["source_msg_id"] is None


def test_deletes_only_finished_old_approvals(conn):
    rows = [
        ("approved", 40), ("rejected", 40), ("expired", 40),
        ("approved", 5), ("pending", 40),
    ]
    for status, days in rows:
        conn.execute(
            "INSERT INTO pending_approvals (user_id, tool_name, arguments, expires_at, status) "
            "VALUES (?, 'delete_event', '{}', ?, ?)",
            (USER_A, _ago(days), status),
        )
    conn.commit()

    assert delete_old_approvals(conn, NOW) == 3
    left = sorted(row["status"] for row in conn.execute("SELECT status FROM pending_approvals"))
    assert left == ["approved", "pending"]


def test_trim_journal_drops_old_lines_and_keeps_unreadable(tmp_path: Path):
    journal = tmp_path / "journal.jsonl"
    lines = [
        json.dumps({"timestamp": _ago(100), "run_id": "old"}),
        json.dumps({"timestamp": _ago(10), "run_id": "new"}),
        "sugadinta eilutė",
    ]
    journal.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert trim_journal(journal, NOW) == 1
    remaining = journal.read_text(encoding="utf-8")
    assert '"old"' not in remaining
    assert '"new"' in remaining
    assert "sugadinta eilutė" in remaining


def test_trim_journal_without_file(tmp_path: Path):
    assert trim_journal(tmp_path / "nera.jsonl", NOW) == 0


def test_run_maintenance_compacts_database(conn, tmp_path: Path):
    for _ in range(300):
        _message(conn, USER_A, 200)
    last = _message(conn, USER_A, 200)
    _summary(conn, USER_A, covers_until=last)

    result = run_maintenance(conn, tmp_path / "journal.jsonl", now=NOW)

    assert result == {"messages": 301, "approvals": 0, "reminders": 0, "journal_lines": 0}
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA freelist_count").fetchone()[0] == 0


def test_deletes_only_finished_old_reminders(conn):
    rows = [
        (_ago(40), None), (None, _ago(40)),   # sent / failed long ago: deleted
        (_ago(5), None),                      # sent recently: kept
        (None, None),                         # pending: kept
    ]
    for sent_at, failed_at in rows:
        conn.execute(
            "INSERT INTO reminders (user_id, created_by, text, due_at, created_at, sent_at, failed_at) "
            "VALUES (?, ?, 'x', ?, ?, ?, ?)",
            (USER_A, USER_A, _ago(50), _ago(60), sent_at, failed_at),
        )
    conn.commit()

    assert delete_old_reminders(conn, NOW) == 2
    assert conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == 2
