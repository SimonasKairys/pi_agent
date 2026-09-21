"""Tests for the [[guest]] list in users.toml: people events may invite without bot access."""

from pathlib import Path

import pytest

from agent.approvals import enrich_arguments, format_approval_card
from agent.config import ConfigError, get_guest_by_name, load_guests, load_users
from agent.db import get_connection
from agent.tools.calendar import create_event, list_events, update_event

USERS_TOML = """
[[user]]
telegram_id = 101
name = "Simonas"
email = "simonas@example.com"
timezone = "Europe/Vilnius"

[[user]]
telegram_id = 102
name = "Ruta"
email = "ruta@example.com"
timezone = "Europe/Vilnius"

[[guest]]
name = "Jonas"
email = "jonas@example.org"

[[guest]]
name = "Petras"
email = "petras@example.org"
"""


class FakeComposioClient:
    def __init__(self, google_id: str = "g_guest_1", items=None):
        self.google_id = google_id
        self.items = items or []
        self.last_slug = None
        self.last_arguments = None
        self.tools = self

    def execute(self, slug: str, arguments: dict, **kwargs):
        self.last_slug = slug
        self.last_arguments = arguments
        if slug == "GOOGLECALENDAR_EVENTS_LIST":
            return {"successful": True, "data": {"items": self.items}}
        return {"successful": True, "data": {"id": self.google_id}}


@pytest.fixture
def users_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "users.toml"
    path.write_text(USERS_TOML, encoding="utf-8")
    monkeypatch.setenv("PIAGENT_USERS_FILE", str(path))
    return path


@pytest.fixture
def conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "guests.db"
    monkeypatch.setattr("agent.db.get_db_path", lambda: db_file)
    c = get_connection(db_file)
    yield c
    c.close()


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "bad.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_load_guests_and_lookup(users_file):
    guests = load_guests(users_file)
    assert [g.name for g in guests] == ["Jonas", "Petras"]
    assert get_guest_by_name("jonas", file_path=users_file).email == "jonas@example.org"


def test_load_guests_accepts_guests_alias(tmp_path):
    path = _write(tmp_path, USERS_TOML.replace("[[guest]]", "[[guests]]"))
    assert [g.name for g in load_guests(path)] == ["Jonas", "Petras"]


def test_load_guests_absent_section_is_empty(tmp_path):
    path = _write(tmp_path, USERS_TOML.split("[[guest]]")[0])
    assert load_guests(path) == []


def test_guest_name_clashing_with_user_rejected(tmp_path):
    path = _write(tmp_path, USERS_TOML + '\n[[guest]]\nname = "ruta"\nemail = "x@example.org"\n')
    with pytest.raises(ConfigError):
        load_guests(path)


def test_guest_duplicate_and_bad_email_rejected(tmp_path):
    dup = _write(tmp_path, USERS_TOML + '\n[[guest]]\nname = "Jonas"\nemail = "j2@example.org"\n')
    with pytest.raises(ConfigError):
        load_guests(dup)
    bad = _write(tmp_path, USERS_TOML.split("[[guest]]")[0] + '\n[[guest]]\nname = "Ona"\nemail = "be-ma"\n')
    with pytest.raises(ConfigError):
        load_guests(bad)


def test_guests_are_not_bot_users(users_file):
    from agent.telegram_ui import get_authorized_user

    assert get_authorized_user(101) is not None
    assert all(u.name != "Jonas" for u in load_users(users_file))


def test_create_event_invites_guest(users_file, conn):
    client = FakeComposioClient()
    res = create_event(
        title="Vakarienė",
        start="2026-09-25T18:00",
        end="2026-09-25T19:00",
        attendees=["Jonas", "Ruta"],
        conn=conn,
        user_id=101,
        composio_client=client,
    )
    assert "Vakarienė" in res
    assert sorted(client.last_arguments["attendees"]) == ["jonas@example.org", "ruta@example.com"]
    assert [r["name"] for r in conn.execute("SELECT name FROM event_guests").fetchall()] == ["Jonas"]
    assert [r["user_id"] for r in conn.execute("SELECT user_id FROM event_attendees").fetchall()] == [102]


def test_create_event_rejects_address_outside_lists(users_file, conn):
    client = FakeComposioClient()
    with pytest.raises(ValueError, match="galima kviesti tik registruotus vartotojus"):
        create_event(
            title="X",
            start="2026-09-25T18:00",
            end="2026-09-25T19:00",
            attendees=["svetimas@evil.example"],
            conn=conn,
            user_id=101,
            composio_client=client,
        )
    assert client.last_slug is None
    assert conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"] == 0


def test_update_keeps_guest_when_attendees_omitted(users_file, conn):
    client = FakeComposioClient()
    create_event(
        title="Vakarienė",
        start="2026-09-25T18:00",
        end="2026-09-25T19:00",
        attendees=["Jonas"],
        conn=conn,
        user_id=101,
        composio_client=client,
    )
    update_event(event_id=1, title="Pietūs", conn=conn, user_id=101, composio_client=client)
    assert client.last_slug == "GOOGLECALENDAR_PATCH_EVENT"
    assert client.last_arguments["attendees"] == ["jonas@example.org"]
    assert conn.execute("SELECT name FROM event_guests").fetchone()["name"] == "Jonas"


def test_update_replaces_guests(users_file, conn):
    client = FakeComposioClient()
    create_event(
        title="Vakarienė",
        start="2026-09-25T18:00",
        end="2026-09-25T19:00",
        attendees=["Jonas"],
        conn=conn,
        user_id=101,
        composio_client=client,
    )
    update_event(event_id=1, attendees=["Petras"], conn=conn, user_id=101, composio_client=client)
    assert client.last_arguments["attendees"] == ["petras@example.org"]
    assert [r["name"] for r in conn.execute("SELECT name FROM event_guests").fetchall()] == ["Petras"]


def test_update_refuses_when_guest_removed_from_file(users_file, conn):
    client = FakeComposioClient()
    create_event(
        title="Vakarienė",
        start="2026-09-25T18:00",
        end="2026-09-25T19:00",
        attendees=["Jonas"],
        conn=conn,
        user_id=101,
        composio_client=client,
    )
    users_file.write_text(USERS_TOML.replace('name = "Jonas"', 'name = "Kitas"'), encoding="utf-8")
    client.last_slug = None
    with pytest.raises(ValueError, match="nebėra sąraše"):
        update_event(event_id=1, title="Pietūs", conn=conn, user_id=101, composio_client=client)
    assert client.last_slug is None


def test_list_events_shows_guest_names_without_email(users_file, conn):
    client = FakeComposioClient(
        items=[
            {
                "id": "g_guest_1",
                "summary": "Vakarienė",
                "start": {"dateTime": "2026-09-25T18:00:00+03:00"},
                "end": {"dateTime": "2026-09-25T19:00:00+03:00"},
            }
        ]
    )
    create_event(
        title="Vakarienė",
        start="2026-09-25T18:00",
        end="2026-09-25T19:00",
        attendees=["Jonas"],
        conn=conn,
        user_id=101,
        composio_client=client,
    )
    result = list_events(
        date_from="2026-09-25",
        date_to="2026-09-26",
        conn=conn,
        user_id=101,
        composio_client=client,
    )
    assert "dalyviai: Jonas" in result
    assert "@" not in result


def test_approval_card_counts_guests(users_file, conn):
    client = FakeComposioClient()
    create_event(
        title="Vakarienė",
        start="2026-09-25T18:00",
        end="2026-09-25T19:00",
        attendees=["Jonas", "Ruta"],
        conn=conn,
        user_id=101,
        composio_client=client,
    )
    args = enrich_arguments(conn, "delete_event", {"event_id": 1})
    assert args["attendee_count"] == 2
    text, _ = format_approval_card(7, "delete_event", args)
    assert "Dalyvių: 2" in text


def test_approval_card_counts_guest_only_event(users_file, conn):
    create_event(
        title="Pietūs",
        start="2026-09-25T12:00",
        end="2026-09-25T13:00",
        attendees=["Jonas"],
        conn=conn,
        user_id=101,
        composio_client=FakeComposioClient(),
    )
    assert enrich_arguments(conn, "delete_event", {"event_id": 1})["attendee_count"] == 1


def test_update_reports_broken_guest_config_not_missing_guest(users_file, conn):
    client = FakeComposioClient()
    create_event(
        title="Vakarienė",
        start="2026-09-25T18:00",
        end="2026-09-25T19:00",
        attendees=["Jonas"],
        conn=conn,
        user_id=101,
        composio_client=client,
    )
    # A guest name that clashes with a user makes the guest section invalid.
    users_file.write_text(USERS_TOML + '\n[[guest]]\nname = "ruta"\nemail = "r2@example.org"\n', encoding="utf-8")
    client.last_slug = None
    with pytest.raises(ValueError, match="Klaidinga vartotojų konfigūracija"):
        update_event(event_id=1, title="Pietūs", conn=conn, user_id=101, composio_client=client)
    assert client.last_slug is None
