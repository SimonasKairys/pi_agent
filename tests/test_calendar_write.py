"""Tests for update_event and delete_event calendar tools in agent/tools/calendar.py."""

from pathlib import Path
import pytest

from agent.config import User
from agent.db import get_connection
from agent.tools.calendar import (
    DELETE_EVENT_TOOL,
    UPDATE_EVENT_TOOL,
    delete_event,
    make_delete_event_tool,
    make_update_event_tool,
    update_event,
)
from agent.tools.registry import ToolRegistry, UNTRUSTED_TAG_OPEN


class FakeComposioClient:
    """Mock Composio client recording patch and delete executions."""

    def __init__(self, successful: bool = True, error: str | None = None):
        self.successful = successful
        self.error = error
        self.last_slug = None
        self.last_arguments = None
        self.tools = self

    def execute(self, slug: str, arguments: dict, **kwargs):
        self.last_slug = slug
        self.last_arguments = arguments
        if not self.successful:
            return {"successful": False, "error": self.error or "Klaida"}
        return {"successful": True, "data": {"status": "ok"}}


@pytest.fixture
def test_users():
    return [
        User(telegram_id=101, name="Simonas", email="simonas@example.com", timezone="Europe/Vilnius", role="admin"),
        User(telegram_id=102, name="Ruta", email="ruta@example.com", timezone="Europe/Vilnius", role="member"),
        User(telegram_id=103, name="Tomas", email="tomas@example.com", timezone="Europe/Vilnius", role="member"),
    ]


@pytest.fixture
def setup_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, test_users):
    db_file = tmp_path / "test_calendar_write.db"
    monkeypatch.setattr("agent.db.get_db_path", lambda: db_file)
    monkeypatch.setattr(
        "agent.tools.calendar.get_user_by_name",
        lambda name: next(u for u in test_users if u.name.lower() == name.lower()),
    )
    monkeypatch.setattr(
        "agent.tools.calendar.get_user_by_id",
        lambda uid: next(u for u in test_users if u.telegram_id == uid),
    )
    conn = get_connection(db_file)
    yield conn
    conn.close()


def test_write_tools_definitions():
    assert UPDATE_EVENT_TOOL.name == "update_event"
    assert UPDATE_EVENT_TOOL.risk == "destructive"
    assert "event_id" in UPDATE_EVENT_TOOL.parameters["properties"]
    assert UPDATE_EVENT_TOOL.parameters["required"] == ["event_id"]

    assert DELETE_EVENT_TOOL.name == "delete_event"
    assert DELETE_EVENT_TOOL.risk == "destructive"
    assert "event_id" in DELETE_EVENT_TOOL.parameters["properties"]
    assert DELETE_EVENT_TOOL.parameters["required"] == ["event_id"]


def test_update_own_event_success(setup_db, test_users):
    conn = setup_db
    conn.execute(
        "INSERT INTO events (id, user_id, google_event_id, title, starts_at, created_at) "
        "VALUES (1, 101, 'g_1', 'Pradinis pavadinimas', '2026-09-25T10:00:00', '2026-09-20T08:00:00')"
    )
    conn.commit()

    fake_client = FakeComposioClient()

    res = update_event(
        event_id=1,
        title="Naujas pavadinimas",
        start="2026-09-25T15:00",
        conn=conn,
        user_id=101,
        composio_client=fake_client,
    )

    assert "Naujas pavadinimas" in res
    assert "[Nr. 1]" in res

    # Verify Composio call
    assert fake_client.last_slug == "GOOGLECALENDAR_PATCH_EVENT"
    args = fake_client.last_arguments
    assert args["event_id"] == "g_1"
    assert args["summary"] == "Naujas pavadinimas"
    assert args["send_updates"] == "all"
    assert args["start_datetime"] == "2026-09-25T15:00:00"
    assert args["timezone"] == "Europe/Vilnius"

    # Verify DB update
    row = conn.execute("SELECT title, starts_at FROM events WHERE id = 1").fetchone()
    assert row["title"] == "Naujas pavadinimas"
    assert "2026-09-25T15:00:00" in row["starts_at"]


def test_update_title_preserves_existing_attendees(setup_db, test_users):
    """GOOGLECALENDAR_PATCH_EVENT overwrites attendees list, so existing attendees must be preserved."""
    conn = setup_db
    conn.execute(
        "INSERT INTO events (id, user_id, google_event_id, title, starts_at, created_at) "
        "VALUES (2, 101, 'g_2', 'Pristatymas', '2026-09-25T10:00:00', '2026-09-20T08:00:00')"
    )
    conn.execute("INSERT INTO event_attendees (event_id, user_id) VALUES (2, 102)")
    conn.execute("INSERT INTO event_attendees (event_id, user_id) VALUES (2, 103)")
    conn.commit()

    fake_client = FakeComposioClient()

    # Update ONLY the title, omitting attendees
    update_event(
        event_id=2,
        title="Atnaujintas pristatymas",
        conn=conn,
        user_id=101,
        composio_client=fake_client,
    )

    args = fake_client.last_arguments
    # Must preserve Ruta (102) and Tomas (103) emails in Composio call
    assert "ruta@example.com" in args["attendees"]
    assert "tomas@example.com" in args["attendees"]
    assert len(args["attendees"]) == 2

    # In database, attendees must remain untouched
    rows = conn.execute("SELECT user_id FROM event_attendees WHERE event_id = 2 ORDER BY user_id").fetchall()
    assert [r["user_id"] for r in rows] == [102, 103]


def test_update_foreign_event_rejected(setup_db, test_users):
    conn = setup_db
    # Event belongs to Simonas (101)
    conn.execute(
        "INSERT INTO events (id, user_id, google_event_id, title, starts_at, created_at) "
        "VALUES (3, 101, 'g_3', 'Simono susitikimas', '2026-09-25T10:00:00', '2026-09-20T08:00:00')"
    )
    conn.commit()

    fake_client = FakeComposioClient()

    # Ruta (102) attempts to update Simonas's event
    with pytest.raises(ValueError) as exc:
        update_event(
            event_id=3,
            title="Hackerio pakeitimas",
            conn=conn,
            user_id=102,
            composio_client=fake_client,
        )

    assert "savo" in str(exc.value)
    # API must not be called
    assert fake_client.last_arguments is None
    # DB must not change
    row = conn.execute("SELECT title FROM events WHERE id = 3").fetchone()
    assert row["title"] == "Simono susitikimas"


def test_delete_own_event_with_notifications(setup_db, test_users):
    conn = setup_db
    conn.execute(
        "INSERT INTO events (id, user_id, google_event_id, title, starts_at, created_at) "
        "VALUES (4, 101, 'g_4', 'Atšaukiamas susitikimas', '2026-09-25T10:00:00', '2026-09-20T08:00:00')"
    )
    conn.commit()

    fake_client = FakeComposioClient()

    res = delete_event(
        event_id=4,
        conn=conn,
        user_id=101,
        composio_client=fake_client,
    )

    assert "Atšaukiamas susitikimas" in res
    assert "[Nr. 4]" in res
    assert "atšauktas" in res

    # Verify Composio call
    assert fake_client.last_slug == "GOOGLECALENDAR_DELETE_EVENT"
    args = fake_client.last_arguments
    assert args["event_id"] == "g_4"
    assert args["send_updates"] == "all"

    # Verify soft delete in SQLite
    row = conn.execute("SELECT deleted_at FROM events WHERE id = 4").fetchone()
    assert row["deleted_at"] is not None

    # Second delete call must fail
    with pytest.raises(ValueError) as exc:
        delete_event(
            event_id=4,
            conn=conn,
            user_id=101,
            composio_client=fake_client,
        )
    assert "nerastas" in str(exc.value) or "ištrintas" in str(exc.value)


def test_delete_foreign_event_rejected(setup_db, test_users):
    conn = setup_db
    # Event belongs to Simonas (101)
    conn.execute(
        "INSERT INTO events (id, user_id, google_event_id, title, starts_at, created_at) "
        "VALUES (5, 101, 'g_5', 'Svetimas susitikimas', '2026-09-25T10:00:00', '2026-09-20T08:00:00')"
    )
    conn.commit()

    fake_client = FakeComposioClient()

    # Ruta (102) tries to delete Simonas's event
    with pytest.raises(ValueError) as exc:
        delete_event(
            event_id=5,
            conn=conn,
            user_id=102,
            composio_client=fake_client,
        )

    assert "savo" in str(exc.value)
    # API must not be called
    assert fake_client.last_arguments is None
    # Event must not be deleted
    row = conn.execute("SELECT deleted_at FROM events WHERE id = 5").fetchone()
    assert row["deleted_at"] is None


@pytest.mark.anyio
async def test_write_tools_via_registry(setup_db, test_users):
    conn = setup_db
    conn.execute(
        "INSERT INTO events (id, user_id, google_event_id, title, starts_at, created_at) "
        "VALUES (6, 101, 'g_6', 'Peržiūros susitikimas', '2026-09-25T10:00:00', '2026-09-20T08:00:00')"
    )
    conn.commit()

    fake_client = FakeComposioClient()

    update_tool = make_update_event_tool(
        conn=conn,
        user_id=101,
        timezone_str="Europe/Vilnius",
        composio_client=fake_client,
    )
    delete_tool = make_delete_event_tool(
        conn=conn,
        user_id=101,
        timezone_str="Europe/Vilnius",
        composio_client=fake_client,
    )

    registry = ToolRegistry()
    registry.register(update_tool)
    registry.register(delete_tool)

    # 1. Test update via registry
    res_update = await registry.execute(
        "update_event",
        {"event_id": 6, "title": "Pakoreguotas pavadinimas"},
    )
    assert UNTRUSTED_TAG_OPEN in res_update
    assert "Pakoreguotas pavadinimas" in res_update
    assert "[Nr. 6]" in res_update

    # 2. Test delete via registry
    res_delete = await registry.execute(
        "delete_event",
        {"event_id": 6},
    )
    assert UNTRUSTED_TAG_OPEN in res_delete
    assert "sėkmingai atšauktas" in res_delete
    assert "[Nr. 6]" in res_delete
