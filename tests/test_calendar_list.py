"""Tests for list_events calendar tool in agent/tools/calendar.py."""

from pathlib import Path
import pytest

from agent.config import User
from agent.db import get_connection
from agent.tools.calendar import (
    MAX_INTERVAL_DAYS,
    MAX_LIST_EVENTS,
    list_events,
    make_list_events_tool,
    LIST_EVENTS_TOOL,
)
from agent.tools.registry import ToolRegistry, UNTRUSTED_TAG_OPEN


class FakeComposioClient:
    """Mock Composio client returning predetermined events without network calls."""

    def __init__(self, items=None, successful: bool = True, error: str | None = None):
        self.items = items if items is not None else []
        self.successful = successful
        self.error = error
        self.last_arguments = None
        self.tools = self

    def execute(self, slug: str, arguments: dict, **kwargs):
        self.last_arguments = arguments
        if not self.successful:
            return {"successful": False, "error": self.error or "Klaida"}
        return {"successful": True, "data": {"items": self.items}}


@pytest.fixture
def test_users():
    return [
        User(telegram_id=101, name="Simonas", email="simonas@example.com", timezone="Europe/Vilnius", role="admin"),
        User(telegram_id=102, name="Ruta", email="ruta@example.com", timezone="Europe/Vilnius", role="member"),
        User(telegram_id=103, name="Tomas", email="tomas@example.com", timezone="Europe/Vilnius", role="member"),
    ]


@pytest.fixture
def setup_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, test_users):
    db_file = tmp_path / "test_calendar.db"
    monkeypatch.setattr("agent.db.get_db_path", lambda: db_file)
    monkeypatch.setattr("agent.tools.calendar.get_user_by_id", lambda uid: next(u for u in test_users if u.telegram_id == uid))
    conn = get_connection(db_file)
    yield conn
    conn.close()


def test_list_events_tool_definition():
    assert LIST_EVENTS_TOOL.name == "list_events"
    assert LIST_EVENTS_TOOL.risk == "read_only"
    assert "date_from" in LIST_EVENTS_TOOL.parameters["properties"]
    assert "date_to" in LIST_EVENTS_TOOL.parameters["properties"]
    assert LIST_EVENTS_TOOL.parameters["required"] == ["date_from", "date_to"]


def test_list_events_creator_view(setup_db, test_users):
    conn = setup_db

    # Insert event created by Simonas (101) with attendees Ruta (102) and Tomas (103)
    conn.execute(
        "INSERT INTO events (id, user_id, google_event_id, title, starts_at, created_at) "
        "VALUES (1, 101, 'g_evt_1', 'Projekto aptarimas', '2026-09-20T10:00:00', '2026-09-20T08:00:00')"
    )
    conn.execute("INSERT INTO event_attendees (event_id, user_id) VALUES (1, 102)")
    conn.execute("INSERT INTO event_attendees (event_id, user_id) VALUES (1, 103)")
    conn.commit()

    fake_items = [
        {
            "id": "g_evt_1",
            "summary": "Projekto aptarimas",
            "start": {"dateTime": "2026-09-20T10:00:00+03:00"},
            "end": {"dateTime": "2026-09-20T11:00:00+03:00"},
        }
    ]
    fake_client = FakeComposioClient(items=fake_items)

    result = list_events(
        date_from="2026-09-20",
        date_to="2026-09-21",
        conn=conn,
        user_id=101,
        composio_client=fake_client,
    )

    # Creator sees local ID, title, time, and attendee names
    assert "[1]" in result
    assert "Projekto aptarimas" in result
    assert "2026-09-20 10:00 - 11:00" in result
    assert "dalyviai: Ruta, Tomas" in result
    # Never leaks Google event ID or email
    assert "g_evt_1" not in result
    assert "@" not in result


def test_list_events_attendee_view(setup_db, test_users):
    conn = setup_db

    conn.execute(
        "INSERT INTO events (id, user_id, google_event_id, title, starts_at, created_at) "
        "VALUES (1, 101, 'g_evt_1', 'Projekto aptarimas', '2026-09-20T10:00:00', '2026-09-20T08:00:00')"
    )
    conn.execute("INSERT INTO event_attendees (event_id, user_id) VALUES (1, 102)")
    conn.execute("INSERT INTO event_attendees (event_id, user_id) VALUES (1, 103)")
    conn.commit()

    fake_items = [
        {
            "id": "g_evt_1",
            "summary": "Projekto aptarimas",
            "start": {"dateTime": "2026-09-20T10:00:00+03:00"},
            "end": {"dateTime": "2026-09-20T11:00:00+03:00"},
        }
    ]
    fake_client = FakeComposioClient(items=fake_items)

    # User 102 (Ruta) is invited participant, not creator
    result = list_events(
        date_from="2026-09-20",
        date_to="2026-09-21",
        conn=conn,
        user_id=102,
        composio_client=fake_client,
    )

    # Attendee sees local ID, title, time, but NO attendee list
    assert "[1]" in result
    assert "Projekto aptarimas" in result
    assert "2026-09-20 10:00 - 11:00" in result
    assert "dalyviai" not in result
    assert "Ruta" not in result
    assert "Tomas" not in result
    assert "Simonas" not in result
    assert "g_evt_1" not in result
    assert "@" not in result


def test_list_events_foreign_event_invisible(setup_db, test_users):
    conn = setup_db

    # Event belongs to Simonas (101) with Ruta (102) as attendee
    conn.execute(
        "INSERT INTO events (id, user_id, google_event_id, title, starts_at, created_at) "
        "VALUES (1, 101, 'g_evt_1', 'Privatus susitikimas', '2026-09-20T10:00:00', '2026-09-20T08:00:00')"
    )
    conn.execute("INSERT INTO event_attendees (event_id, user_id) VALUES (1, 102)")
    conn.commit()

    fake_items = [
        {
            "id": "g_evt_1",
            "summary": "Privatus susitikimas",
            "start": {"dateTime": "2026-09-20T10:00:00+03:00"},
            "end": {"dateTime": "2026-09-20T11:00:00+03:00"},
        }
    ]
    fake_client = FakeComposioClient(items=fake_items)

    # User 103 (Tomas) is neither creator nor attendee
    result = list_events(
        date_from="2026-09-20",
        date_to="2026-09-21",
        conn=conn,
        user_id=103,
        composio_client=fake_client,
    )

    assert result == "Nurodytu laikotarpiu įvykių nerasta."
    assert "Privatus susitikimas" not in result


def test_list_events_google_event_not_in_db_discarded(setup_db):
    conn = setup_db

    # Google calendar has an event not created by the bot
    fake_items = [
        {
            "id": "external_untracked_evt",
            "summary": "Svetimas Google įvykis",
            "start": {"dateTime": "2026-09-20T14:00:00+03:00"},
            "end": {"dateTime": "2026-09-20T15:00:00+03:00"},
        }
    ]
    fake_client = FakeComposioClient(items=fake_items)

    result = list_events(
        date_from="2026-09-20",
        date_to="2026-09-21",
        conn=conn,
        user_id=101,
        composio_client=fake_client,
    )

    assert result == "Nurodytu laikotarpiu įvykių nerasta."
    assert "Svetimas Google įvykis" not in result


def test_list_events_soft_deleted_event_discarded(setup_db):
    conn = setup_db

    conn.execute(
        "INSERT INTO events (id, user_id, google_event_id, title, starts_at, created_at, deleted_at) "
        "VALUES (1, 101, 'g_deleted_1', 'Atšauktas susitikimas', '2026-09-20T10:00:00', '2026-09-20T08:00:00', '2026-09-20T09:00:00')"
    )
    conn.commit()

    fake_items = [
        {
            "id": "g_deleted_1",
            "summary": "Atšauktas susitikimas",
            "start": {"dateTime": "2026-09-20T10:00:00+03:00"},
            "end": {"dateTime": "2026-09-20T11:00:00+03:00"},
        }
    ]
    fake_client = FakeComposioClient(items=fake_items)

    result = list_events(
        date_from="2026-09-20",
        date_to="2026-09-21",
        conn=conn,
        user_id=101,
        composio_client=fake_client,
    )

    assert result == "Nurodytu laikotarpiu įvykių nerasta."
    assert "Atšauktas susitikimas" not in result


def test_list_events_interval_limit(setup_db):
    conn = setup_db
    fake_client = FakeComposioClient(items=[])

    # Range > 90 days must be rejected
    with pytest.raises(ValueError) as exc_info:
        list_events(
            date_from="2026-01-01",
            date_to="2026-05-01",
            conn=conn,
            user_id=101,
            composio_client=fake_client,
        )
    assert str(MAX_INTERVAL_DAYS) in str(exc_info.value)

    # End date before start date must be rejected
    with pytest.raises(ValueError) as exc_info2:
        list_events(
            date_from="2026-09-25",
            date_to="2026-09-20",
            conn=conn,
            user_id=101,
            composio_client=fake_client,
        )
    assert "ankstesnė" in str(exc_info2.value)


def test_list_events_max_results_limit(setup_db):
    conn = setup_db

    fake_items = []
    # Insert 60 events in DB and mock
    for i in range(1, 65):
        conn.execute(
            "INSERT INTO events (id, user_id, google_event_id, title, starts_at, created_at) "
            f"VALUES ({i}, 101, 'g_{i}', 'Įvykis {i}', '2026-09-20T10:00:00', '2026-09-20T08:00:00')"
        )
        fake_items.append({
            "id": f"g_{i}",
            "summary": f"Įvykis {i}",
            "start": {"dateTime": "2026-09-20T10:00:00+03:00"},
            "end": {"dateTime": "2026-09-20T10:30:00+03:00"},
        })
    conn.commit()

    fake_client = FakeComposioClient(items=fake_items)

    result = list_events(
        date_from="2026-09-20",
        date_to="2026-09-21",
        conn=conn,
        user_id=101,
        composio_client=fake_client,
    )

    lines = [line for line in result.splitlines() if line.strip()]
    assert len(lines) == MAX_LIST_EVENTS
    assert f"[{MAX_LIST_EVENTS}]" in result
    assert f"[{MAX_LIST_EVENTS + 1}]" not in result


@pytest.mark.anyio
async def test_list_events_via_tool_registry(setup_db, test_users):
    conn = setup_db
    conn.execute(
        "INSERT INTO events (id, user_id, google_event_id, title, starts_at, created_at) "
        "VALUES (42, 101, 'g_42', 'Architektūros peržiūra', '2026-09-20T15:00:00', '2026-09-20T08:00:00')"
    )
    conn.commit()

    fake_items = [
        {
            "id": "g_42",
            "summary": "Architektūros peržiūra",
            "start": {"dateTime": "2026-09-20T15:00:00+03:00"},
            "end": {"dateTime": "2026-09-20T16:00:00+03:00"},
        }
    ]
    fake_client = FakeComposioClient(items=fake_items)

    bound_tool = make_list_events_tool(
        conn=conn,
        user_id=101,
        timezone_str="Europe/Vilnius",
        composio_client=fake_client,
    )

    registry = ToolRegistry()
    registry.register(bound_tool)

    output = await registry.execute(
        "list_events",
        {"date_from": "2026-09-20", "date_to": "2026-09-21"},
    )

    assert UNTRUSTED_TAG_OPEN in output
    assert "[42] Architektūros peržiūra" in output
    assert "g_42" not in output
