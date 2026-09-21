"""Tests for notes, reminders, reminder delivery, and their approval rules."""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from telegram.error import Forbidden, NetworkError

from agent.approvals import enrich_arguments, format_approval_card, needs_approval
from agent.config import User
from agent.db import get_connection
from agent.memory import FACT_EXTRACTION_SYSTEM_PROMPT
from agent.prompts import SYSTEM_PROMPT_TEMPLATE, build_system_prompt
from agent.reminders import deliver_due_reminders
from agent.telegram_ui import get_default_registry
from agent.tools.notes import add_note, delete_note, list_notes, search_notes
from agent.tools.reminders import create_reminder, delete_reminder, list_reminders

SIMONAS = User(1001, "Simonas", "simonas@example.com", "Europe/Vilnius", role="admin")
RUTA = User(2002, "Rūta", "ruta@example.com", "Europe/Vilnius")
USERS = [SIMONAS, RUTA]
# 2026-09-21 12:00 in Vilnius (EEST, UTC+3)
NOW = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn(tmp_path: Path):
    connection = get_connection(tmp_path / "nr.db")
    yield connection
    connection.close()


def _create(conn, when, text="Paskambinti Jokūbui", user=SIMONAS, for_name=None):
    return create_reminder(
        conn, user.telegram_id, user.timezone, when, text,
        for_name=for_name, now=NOW, users_loader=lambda: USERS,
    )


# --- Notes -----------------------------------------------------------------


def test_add_and_list_notes_are_per_user(conn):
    assert add_note(conn, SIMONAS.telegram_id, "Idėja: straipsnis apie šifravimą").startswith("Užrašas 1")
    add_note(conn, RUTA.telegram_id, "Rūtos užrašas")

    mine = list_notes(conn, SIMONAS.telegram_id)
    assert "Idėja: straipsnis apie šifravimą" in mine
    assert "Rūtos" not in mine


def test_search_notes_matches_lithuanian_word_forms(conn):
    add_note(conn, SIMONAS.telegram_id, "Parašyti straipsnį apie šifravimą")
    add_note(conn, SIMONAS.telegram_id, "Nupirkti pieno")
    add_note(conn, RUTA.telegram_id, "Rūtos straipsnis")

    result = search_notes(conn, SIMONAS.telegram_id, "straipsnis")

    assert "Parašyti straipsnį" in result
    assert "pieno" not in result
    assert "Rūtos" not in result


def test_search_notes_ignores_fts_syntax(conn):
    add_note(conn, SIMONAS.telegram_id, "Kelionė į Rygą")
    # Quotes, operators, and parentheses from the model must not raise.
    assert "Rygą" in search_notes(conn, SIMONAS.telegram_id, 'kelionė" OR (NEAR*')


def test_note_validation(conn):
    assert add_note(conn, SIMONAS.telegram_id, "   ").startswith("Klaida")
    assert add_note(conn, SIMONAS.telegram_id, "x" * 2001).startswith("Klaida")


def test_delete_note_only_own(conn):
    add_note(conn, SIMONAS.telegram_id, "Mano")
    add_note(conn, RUTA.telegram_id, "Rūtos")

    assert delete_note(conn, SIMONAS.telegram_id, 2) == "Užrašo 2 nerasta."
    assert delete_note(conn, SIMONAS.telegram_id, 1) == "Užrašas 1 ištrintas."
    assert list_notes(conn, SIMONAS.telegram_id) == "Užrašų nėra."


def test_notes_never_enter_system_prompt(conn):
    add_note(conn, SIMONAS.telegram_id, "SLAPTAS_UŽRAŠAS")
    prompt = build_system_prompt(name="Simonas", facts=["Gyvena Vilniuje"])
    assert "SLAPTAS_UŽRAŠAS" not in prompt


# --- Reminders -------------------------------------------------------------


def test_create_reminder_stores_utc_and_shows_local(conn):
    result = _create(conn, "2026-09-22T09:00")

    assert result == "Priminimas 1 sukurtas: 2026-09-22 09:00 – Paskambinti Jokūbui"
    row = conn.execute("SELECT user_id, created_by, due_at FROM reminders").fetchone()
    assert row["due_at"] == "2026-09-22T06:00:00+00:00"
    assert row["user_id"] == row["created_by"] == SIMONAS.telegram_id


def test_create_reminder_rejects_bad_times(conn):
    assert _create(conn, "2026-09-21T11:00") == "Klaida: šis laikas jau praėjo."
    assert _create(conn, "2028-01-01T09:00").startswith("Klaida: priminti galima ne vėliau")
    assert _create(conn, "rytoj devintą").startswith("Klaida: laiką nurodyk")
    assert conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == 0


def test_create_reminder_for_other_user(conn):
    result = _create(conn, "2026-09-22T09:00", "Atnešti raktus", for_name="rūta")

    assert result.startswith("Priminimas 1 vartotojui Rūta sukurtas")
    row = conn.execute("SELECT user_id, created_by FROM reminders").fetchone()
    assert row["user_id"] == RUTA.telegram_id
    assert row["created_by"] == SIMONAS.telegram_id


def test_create_reminder_unknown_or_guest_name_is_rejected(conn):
    result = _create(conn, "2026-09-22T09:00", for_name="Jonas")
    assert result.startswith("Klaida: vartotojo „Jonas“ nėra")
    assert conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == 0


def test_list_and_delete_reminders(conn):
    _create(conn, "2026-09-22T09:00", "Mano priminimas")
    _create(conn, "2026-09-23T10:00", "Rūtai", for_name="Rūta")

    simonas = list_reminders(conn, SIMONAS.telegram_id, "Europe/Vilnius", users_loader=lambda: USERS)
    assert "1. 2026-09-22 09:00 – Mano priminimas" in simonas
    assert "2. 2026-09-23 10:00 – Rūtai (vartotojui Rūta)" in simonas

    ruta = list_reminders(conn, RUTA.telegram_id, "Europe/Vilnius", users_loader=lambda: USERS)
    assert "(nuo Simonas)" in ruta
    assert "Mano priminimas" not in ruta

    # Rūta can cancel the reminder meant for her, but not Simonas' own one.
    assert delete_reminder(conn, RUTA.telegram_id, 1) == "Priminimo 1 nerasta."
    assert delete_reminder(conn, RUTA.telegram_id, 2) == "Priminimas 2 atšauktas."


# --- Delivery --------------------------------------------------------------


def test_delivers_due_reminder_once(conn):
    _create(conn, "2026-09-21T12:30")
    bot = AsyncMock()

    before = asyncio.run(deliver_due_reminders(conn, bot, now=NOW, users_loader=lambda: USERS))
    assert before == 0

    due = NOW + timedelta(minutes=31)
    assert asyncio.run(deliver_due_reminders(conn, bot, now=due, users_loader=lambda: USERS)) == 1
    bot.send_message.assert_awaited_once_with(
        chat_id=SIMONAS.telegram_id, text="⏰ Priminimas: Paskambinti Jokūbui"
    )
    # A second check does not resend.
    assert asyncio.run(deliver_due_reminders(conn, bot, now=due, users_loader=lambda: USERS)) == 0


def test_late_reminder_from_other_user_names_sender(conn):
    _create(conn, "2026-09-21T12:30", "Atnešti raktus", for_name="Rūta")
    bot = AsyncMock()

    late = NOW + timedelta(hours=2)
    asyncio.run(deliver_due_reminders(conn, bot, now=late, users_loader=lambda: USERS))

    text = bot.send_message.await_args.kwargs["text"]
    assert text.startswith("⏰ Priminimas nuo Simonas: Atnešti raktus")
    assert "Vėluoja: turėjo būti 2026-09-21 12:30" in text


def test_unreachable_recipient_fails_once_and_tells_creator(conn):
    _create(conn, "2026-09-21T12:30", "Atnešti raktus", for_name="Rūta")
    bot = AsyncMock()
    bot.send_message.side_effect = [Forbidden("bot was blocked"), None]

    due = NOW + timedelta(minutes=31)
    assert asyncio.run(deliver_due_reminders(conn, bot, now=due, users_loader=lambda: USERS)) == 0

    notice = bot.send_message.await_args_list[1].kwargs
    assert notice["chat_id"] == SIMONAS.telegram_id
    assert "nepavyko" in notice["text"]
    assert conn.execute("SELECT failed_at FROM reminders").fetchone()["failed_at"] is not None

    bot.send_message.reset_mock(side_effect=True)
    asyncio.run(deliver_due_reminders(conn, bot, now=due, users_loader=lambda: USERS))
    bot.send_message.assert_not_awaited()


def test_network_error_retries_on_next_check(conn):
    _create(conn, "2026-09-21T12:30")
    bot = AsyncMock()
    bot.send_message.side_effect = NetworkError("timeout")

    due = NOW + timedelta(minutes=31)
    assert asyncio.run(deliver_due_reminders(conn, bot, now=due, users_loader=lambda: USERS)) == 0
    row = conn.execute("SELECT sent_at, failed_at FROM reminders").fetchone()
    assert row["sent_at"] is None and row["failed_at"] is None

    bot.send_message.side_effect = None
    assert asyncio.run(deliver_due_reminders(conn, bot, now=due, users_loader=lambda: USERS)) == 1


# --- Approvals and routing -------------------------------------------------


def test_approval_rules():
    assert needs_approval("delete_note") is True
    assert needs_approval("delete_reminder") is True
    assert needs_approval("add_note", risk="destructive") is False
    assert needs_approval("add_note", naudotas_internetas=True, risk="destructive") is True

    own = {"when": "2026-09-22T09:00", "text": "x"}
    assert needs_approval("create_reminder", risk="destructive", arguments=own, caller_name="Simonas") is False
    self_named = {**own, "for_name": "simonas"}
    assert needs_approval("create_reminder", risk="destructive", arguments=self_named, caller_name="Simonas") is False
    other = {**own, "for_name": "Rūta"}
    assert needs_approval("create_reminder", risk="destructive", arguments=other, caller_name="Simonas") is True


def test_approval_cards_show_own_rows_only(conn):
    add_note(conn, SIMONAS.telegram_id, "Mano *idėja*")
    add_note(conn, RUTA.telegram_id, "Rūtos idėja")
    _create(conn, "2026-09-22T09:00", "Mano priminimas")

    shown = enrich_arguments(conn, "delete_note", {"note_id": 1}, user_id=SIMONAS.telegram_id)
    text, _ = format_approval_card(1, "delete_note", shown)
    assert "Užrašo trynimas" in text and "Mano idėja" in text

    foreign = enrich_arguments(conn, "delete_note", {"note_id": 2}, user_id=SIMONAS.telegram_id)
    text, _ = format_approval_card(2, "delete_note", foreign)
    assert "Rūtos" not in text and "Užrašo numeris" in text

    shown = enrich_arguments(conn, "delete_reminder", {"reminder_id": 1}, user_id=SIMONAS.telegram_id)
    text, _ = format_approval_card(3, "delete_reminder", shown)
    assert "Mano priminimas" in text and "2026-09-22 09:00" in text

    card, _ = format_approval_card(
        4, "create_reminder", {"when": "2026-09-22T09:00", "text": "Atnešti raktus", "for_name": "Rūta"}
    )
    assert "Priminimas kitam vartotojui" in card
    assert "Kam**: Rūta" in card and "2026-09-22 09:00" in card and "Atnešti raktus" in card


def test_registry_has_note_and_reminder_tools(conn):
    registry = get_default_registry(conn=conn, user=SIMONAS)
    for name in ("add_note", "search_notes", "list_notes", "delete_note",
                 "create_reminder", "list_reminders", "delete_reminder"):
        assert registry.get(name) is not None, name


def test_prompts_route_remember_remind_and_note():
    assert "create_reminder" in SYSTEM_PROMPT_TEMPLATE
    assert "add_note" in SYSTEM_PROMPT_TEMPLATE
    assert "priminimų bei užrašų" in FACT_EXTRACTION_SYSTEM_PROMPT
