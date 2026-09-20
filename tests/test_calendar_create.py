"""Tests for create_event calendar tool in agent/tools/calendar.py."""

from pathlib import Path
import pytest

from agent.config import User
from agent.db import get_current_day, get_connection, LimitExceededError
from agent.tools.calendar import (
    CREATE_EVENT_TOOL,
    create_event,
    make_create_event_tool,
)
from agent.tools.registry import ToolRegistry, UNTRUSTED_TAG_OPEN


class FakeComposioClient:
    """Mock Composio client for calendar event creation."""

    def __init__(self, google_id: str = "google_created_123", successful: bool = True, error: str | None = None):
        self.google_id = google_id
        self.successful = successful
        self.error = error
        self.last_arguments = None
        self.last_slug = None
        self.tools = self

    def execute(self, slug: str, arguments: dict, **kwargs):
        self.last_slug = slug
        self.last_arguments = arguments
        if not self.successful:
            return {"successful": False, "error": self.error or "Klaida"}
        return {"successful": True, "data": {"id": self.google_id}}


@pytest.fixture
def test_users():
    return [
        User(telegram_id=101, name="Simonas", email="simonas@example.com", timezone="Europe/Vilnius", role="admin"),
        User(telegram_id=102, name="Ruta", email="ruta@example.com", timezone="Europe/Vilnius", role="member"),
        User(telegram_id=103, name="Tomas", email="tomas@example.com", timezone="Europe/Vilnius", role="member"),
    ]


@pytest.fixture
def setup_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, test_users):
    db_file = tmp_path / "test_calendar_create.db"
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


def test_create_event_tool_definition():
    assert CREATE_EVENT_TOOL.name == "create_event"
    assert CREATE_EVENT_TOOL.risk == "destructive"
    assert "title" in CREATE_EVENT_TOOL.parameters["properties"]
    assert "start" in CREATE_EVENT_TOOL.parameters["properties"]
    assert "end" in CREATE_EVENT_TOOL.parameters["properties"]
    assert "attendees" in CREATE_EVENT_TOOL.parameters["properties"]
    assert set(CREATE_EVENT_TOOL.parameters["required"]) == {"title", "start", "end", "attendees"}


def test_create_event_success(setup_db, test_users):
    conn = setup_db
    fake_client = FakeComposioClient(google_id="g_evt_success_1")

    res = create_event(
        title="Strateginis susitikimas",
        start="2026-09-22T10:00",
        end="2026-09-22T11:00",
        attendees=["Ruta"],
        description="Aptarsime Q4 tikslus",
        conn=conn,
        user_id=101,
        composio_client=fake_client,
    )

    assert "Strateginis susitikimas" in res
    assert "[Nr. 1]" in res

    # Verify Composio call
    assert fake_client.last_slug == "GOOGLECALENDAR_CREATE_EVENT"
    args = fake_client.last_arguments
    assert args["summary"] == "Strateginis susitikimas"
    assert args["timezone"] == "Europe/Vilnius"
    assert args["attendees"] == ["ruta@example.com"]
    assert args["description"] == "Aptarsime Q4 tikslus"

    # Verify four required parameters from TASK.md / PLAN.md
    assert args.get("send_updates") == "all" or args.get("sendUpdates") == "all"
    assert args.get("guests_can_invite_others") is False
    assert args.get("guests_can_see_other_guests") is False
    assert args.get("create_meeting_room") is False

    # Verify database state
    row = conn.execute("SELECT * FROM events WHERE id = 1").fetchone()
    assert row is not None
    assert row["user_id"] == 101
    assert row["google_event_id"] == "g_evt_success_1"
    assert row["title"] == "Strateginis susitikimas"

    att = conn.execute("SELECT * FROM event_attendees WHERE event_id = 1").fetchall()
    assert len(att) == 1
    assert att[0]["user_id"] == 102  # Ruta's telegram_id

    # Verify usage increment
    usage = conn.execute("SELECT events_created FROM usage WHERE user_id = 101").fetchone()
    assert usage is not None
    assert usage["events_created"] == 1


def test_create_event_four_required_parameters(setup_db):
    conn = setup_db
    fake_client = FakeComposioClient(google_id="g_evt_params")

    create_event(
        title="Parametrų patikra",
        start="2026-09-23T14:00",
        end="2026-09-23T15:00",
        attendees=[],
        conn=conn,
        user_id=101,
        composio_client=fake_client,
    )

    args = fake_client.last_arguments
    # 1. send_updates='all'
    assert args["send_updates"] == "all"
    # 2. guests_can_invite_others=False
    assert args["guests_can_invite_others"] is False
    # 3. guests_can_see_other_guests=False
    assert args["guests_can_see_other_guests"] is False
    # 4. create_meeting_room=False
    assert args["create_meeting_room"] is False


def test_create_event_blocks_external_attendee(setup_db):
    conn = setup_db
    fake_client = FakeComposioClient()

    with pytest.raises(ValueError) as exc_info:
        create_event(
            title="Slaptas susitikimas",
            start="2026-09-22T10:00",
            end="2026-09-22T11:00",
            attendees=["Nepažįstamasis"],
            conn=conn,
            user_id=101,
            composio_client=fake_client,
        )

    assert "Nepažįstamasis" in str(exc_info.value)
    # Composio must not be invoked
    assert fake_client.last_arguments is None

    # Database must have no created events
    count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert count == 0


def test_create_event_enforces_daily_limit(setup_db):
    conn = setup_db
    fake_client = FakeComposioClient()

    # Pre-populate usage with 20 events created today
    # get_current_day() dirba Europe/Vilnius zona. SQLite strftime('now') yra
    # UTC, todėl naktį tarp abiejų vidurnakčių testas rodytų kitą dieną.
    conn.execute(
        "INSERT INTO usage (user_id, day, cost_usd, events_created) "
        "VALUES (101, ?, 0.1, 20)",
        (get_current_day(),),
    )
    conn.commit()

    with pytest.raises(LimitExceededError):
        create_event(
            title="Viršytas limitas",
            start="2026-09-22T10:00",
            end="2026-09-22T11:00",
            attendees=[],
            conn=conn,
            user_id=101,
            composio_client=fake_client,
        )

    # Composio must not be invoked
    assert fake_client.last_arguments is None


def test_create_event_invalid_times(setup_db):
    conn = setup_db
    fake_client = FakeComposioClient()

    # End before start
    with pytest.raises(ValueError) as exc:
        create_event(
            title="Atvirkščias laikas",
            start="2026-09-22T15:00",
            end="2026-09-22T14:00",
            attendees=[],
            conn=conn,
            user_id=101,
            composio_client=fake_client,
        )
    assert "ankstesnis" in str(exc.value)


@pytest.mark.anyio
async def test_create_event_via_registry(setup_db, test_users):
    conn = setup_db
    fake_client = FakeComposioClient(google_id="g_evt_registry")

    bound_tool = make_create_event_tool(
        conn=conn,
        user_id=101,
        timezone_str="Europe/Vilnius",
        composio_client=fake_client,
    )

    registry = ToolRegistry()
    registry.register(bound_tool)

    output = await registry.execute(
        "create_event",
        {
            "title": "Susitikimas per registrą",
            "start": "2026-09-24T09:00",
            "end": "2026-09-24T10:00",
            "attendees": ["Tomas"],
            "undeclared_injected_param": "attacker_data",
        },
    )

    assert UNTRUSTED_TAG_OPEN in output
    assert "Susitikimas per registrą" in output
    assert "[Nr. 1]" in output
    # Undeclared parameter must not reach Composio arguments
    assert "undeclared_injected_param" not in fake_client.last_arguments
