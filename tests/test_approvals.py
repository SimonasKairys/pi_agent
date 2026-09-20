"""Tests for agent/approvals.py."""

import asyncio
from pathlib import Path
import pytest

from agent.approvals import (
    APPROVAL_EXPIRY_MINUTES,
    create_pending_approval,
    execute_approved_action,
    format_approval_card,
    needs_approval,
    process_approval_action,
)
from agent.db import get_connection
from agent.tools.registry import Tool, ToolRegistry


@pytest.fixture
def setup_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "test_approvals.db"
    monkeypatch.setattr("agent.db.get_db_path", lambda: db_file)
    conn = get_connection(db_file)
    yield conn
    conn.close()


def test_needs_approval_policy():
    # 1. update_event and delete_event always require confirmation
    assert needs_approval("update_event", naudotas_internetas=False) is True
    assert needs_approval("update_event", naudotas_internetas=True) is True
    assert needs_approval("delete_event", naudotas_internetas=False) is True
    assert needs_approval("delete_event", naudotas_internetas=True) is True

    # 2. create_event without search runs automatically
    assert needs_approval("create_event", naudotas_internetas=False) is False

    # 3. create_event after web search (tainted run) requires confirmation
    assert needs_approval("create_event", naudotas_internetas=True) is True

    # 4. Any generic destructive tool after search requires confirmation
    assert needs_approval("custom_action", naudotas_internetas=True, risk="destructive") is True

    # 5. Read-only tools never require confirmation
    assert needs_approval("list_events", naudotas_internetas=True, risk="read_only") is False
    assert needs_approval("search_web", naudotas_internetas=True, risk="read_only") is False


def test_create_and_approve_action(setup_db):
    conn = setup_db

    approval_id = create_pending_approval(
        conn=conn,
        user_id=101,
        tool_name="update_event",
        arguments={"event_id": 5, "title": "Naujas laikas"},
        chat_id=1111,
        message_id=2222,
    )
    assert approval_id > 0

    success, msg, info = process_approval_action(
        conn=conn,
        approval_id=approval_id,
        action="approve",
        clicking_user_id=101,
    )
    assert success is True
    assert "patvirtintas" in msg
    assert info is not None
    assert info["id"] == approval_id
    assert info["tool_name"] == "update_event"
    assert info["arguments"]["title"] == "Naujas laikas"

    # Status in database must be approved
    row = conn.execute("SELECT status FROM pending_approvals WHERE id = ?", (approval_id,)).fetchone()
    assert row["status"] == "approved"


def test_create_and_reject_action(setup_db):
    conn = setup_db

    approval_id = create_pending_approval(
        conn=conn,
        user_id=101,
        tool_name="delete_event",
        arguments={"event_id": 5},
    )

    success, msg, info = process_approval_action(
        conn=conn,
        approval_id=approval_id,
        action="reject",
        clicking_user_id=101,
    )
    assert success is True
    assert "atmestas" in msg
    assert info is None

    row = conn.execute("SELECT status FROM pending_approvals WHERE id = ?", (approval_id,)).fetchone()
    assert row["status"] == "rejected"


def test_approval_expired_after_time_limit(setup_db):
    conn = setup_db

    # Create approval expired in the past (-1 minute)
    approval_id = create_pending_approval(
        conn=conn,
        user_id=101,
        tool_name="create_event",
        arguments={"title": "Vėluojantis"},
        expiry_minutes=-1,
    )

    success, msg, info = process_approval_action(
        conn=conn,
        approval_id=approval_id,
        action="approve",
        clicking_user_id=101,
    )
    assert success is False
    assert "pasibaigęs" in msg or "baigėsi" in msg
    assert info is None

    # Status transitioned to expired
    row = conn.execute("SELECT status FROM pending_approvals WHERE id = ?", (approval_id,)).fetchone()
    assert row["status"] == "expired"


def test_foreign_user_cannot_approve(setup_db):
    conn = setup_db

    approval_id = create_pending_approval(
        conn=conn,
        user_id=101,
        tool_name="update_event",
        arguments={"event_id": 1, "title": "Atnaujinta"},
    )

    # User 102 clicks on 101's approval card
    success, msg, info = process_approval_action(
        conn=conn,
        approval_id=approval_id,
        action="approve",
        clicking_user_id=102,
    )
    assert success is False
    assert "Neturite teisės" in msg
    assert info is None

    # Status must remain pending for the true recipient
    row = conn.execute("SELECT status FROM pending_approvals WHERE id = ?", (approval_id,)).fetchone()
    assert row["status"] == "pending"


def test_double_click_executes_only_once(setup_db):
    conn = setup_db

    approval_id = create_pending_approval(
        conn=conn,
        user_id=101,
        tool_name="delete_event",
        arguments={"event_id": 7},
    )

    # First click succeeds
    success1, msg1, info1 = process_approval_action(
        conn=conn,
        approval_id=approval_id,
        action="approve",
        clicking_user_id=101,
    )
    assert success1 is True
    assert info1 is not None

    # Second click fails atomically
    success2, msg2, info2 = process_approval_action(
        conn=conn,
        approval_id=approval_id,
        action="approve",
        clicking_user_id=101,
    )
    assert success2 is False
    assert "jau" in msg2 or "apdorotas" in msg2
    assert info2 is None


def test_concurrent_update_race(setup_db):
    conn = setup_db
    approval_id = create_pending_approval(
        conn=conn,
        user_id=101,
        tool_name="update_event",
        arguments={"event_id": 10},
    )

    class ConnProxy:
        def __init__(self, real_conn):
            self._conn = real_conn

        def cursor(self):
            return self._conn.cursor()

        def commit(self):
            return self._conn.commit()

        def execute(self, sql, params=()):
            if "UPDATE pending_approvals SET status" in sql:
                self._conn.execute("UPDATE pending_approvals SET status = 'approved' WHERE id = ?", (params[1],))
            return self._conn.execute(sql, params)

    proxy = ConnProxy(conn)

    success, msg, info = process_approval_action(
        conn=proxy,
        approval_id=approval_id,
        action="approve",
        clicking_user_id=101,
    )
    assert success is False
    assert "jau buvo apdorotas" in msg
    assert info is None


def test_format_approval_card():
    text, keyboard = format_approval_card(
        approval_id=42,
        tool_name="create_event",
        arguments={
            "title": "Komandos planavimas",
            "start": "2026-09-25T10:00",
            "end": "2026-09-25T11:00",
            "attendees": ["Ruta", "Tomas"],
        },
    )

    assert "Naujo įvykio sukūrimas" in text
    assert "Komandos planavimas" in text
    assert "2026-09-25T10:00 - 2026-09-25T11:00" in text
    assert "Dalyvių skaičius" in text
    assert "2 (Ruta, Tomas)" in text
    assert str(APPROVAL_EXPIRY_MINUTES) in text

    buttons = keyboard.inline_keyboard[0]
    assert len(buttons) == 2
    assert buttons[0].text == "Tvirtinti"
    assert buttons[0].callback_data == "approve:42"
    assert buttons[1].text == "Atmesti"
    assert buttons[1].callback_data == "reject:42"


@pytest.mark.anyio
async def test_execute_approved_action_holds_lock():
    lock = asyncio.Lock()
    lock_fn_called = False

    def get_lock(uid: int):
        nonlocal lock_fn_called
        lock_fn_called = True
        return lock

    executed = False

    def dummy_tool(event_id: int):
        nonlocal executed
        executed = True
        return f"Ištrinta: {event_id}"

    tool = Tool(
        name="delete_event",
        description="test",
        parameters={"type": "object", "properties": {"event_id": {"type": "integer"}}},
        risk="destructive",
        func=dummy_tool,
    )

    registry = ToolRegistry()
    registry.register(tool)

    approval_info = {
        "user_id": 101,
        "tool_name": "delete_event",
        "arguments": {"event_id": 99},
    }

    res = await execute_approved_action(approval_info, registry, get_lock_fn=get_lock)
    assert lock_fn_called is True
    assert executed is True
    assert "Ištrinta: 99" in res
