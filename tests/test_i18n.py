"""Tests for per-user languages (users.toml `language`)."""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import BotCommandScopeChat
from telegram.error import Forbidden

import agent.telegram_ui as telegram_ui
from agent.approvals import create_pending_approval, format_approval_card, process_approval_action
from agent.config import ConfigError, User, load_users
from agent.db import LimitExceededError, get_connection
from agent.i18n import MESSAGES, language_from_telegram, t
from agent.prompts import bot_commands, build_system_prompt
from agent.reminders import deliver_due_reminders
from agent.tools.facts import forget_fact
from agent.tools.notes import add_note
from agent.tools.reminders import create_reminder

SIMONAS = User(1001, "Simonas", "simonas@example.com", "Europe/Vilnius", role="admin")
ANNA = User(2002, "Anna", "anna@example.com", "Europe/London", language="en")
USERS = [SIMONAS, ANNA]
NOW = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn(tmp_path: Path):
    connection = get_connection(tmp_path / "i18n.db")
    yield connection
    connection.close()


def test_every_language_has_the_same_keys():
    assert set(MESSAGES["lt"]) == set(MESSAGES["en"])


def test_users_toml_language(tmp_path: Path):
    path = tmp_path / "users.toml"
    path.write_text(
        '[[user]]\ntelegram_id = 1\nname = "A"\nemail = "a@example.com"\ntimezone = "Europe/Vilnius"\n'
        '[[user]]\ntelegram_id = 2\nname = "B"\nemail = "b@example.com"\ntimezone = "Europe/London"\n'
        'language = "EN"\n',
        encoding="utf-8",
    )
    users = load_users(path)
    assert [u.language for u in users] == ["lt", "en"]

    path.write_text(
        '[[user]]\ntelegram_id = 1\nname = "A"\nemail = "a@example.com"\ntimezone = "Europe/Vilnius"\n'
        'language = "de"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="unsupported language"):
        load_users(path)


def test_system_prompt_names_the_answer_language():
    assert "Visada atsakyk anglų (English) kalba." in build_system_prompt("Anna", language="en")
    assert "Visada atsakyk lietuvių kalba." in build_system_prompt("Simonas")


def test_unauthorized_language_follows_telegram_app():
    assert language_from_telegram("en-GB") == "en"
    assert language_from_telegram("lt") == "lt"
    assert language_from_telegram(None) == "lt"


def _update(user_id: int, language_code: str | None = None):
    update = MagicMock()
    update.effective_chat.type = "private"
    update.effective_user.id = user_id
    update.effective_user.language_code = language_code
    update.message.reply_text = AsyncMock()
    return update


def test_start_and_help_in_user_language(monkeypatch):
    monkeypatch.setattr(telegram_ui, "load_users", lambda: USERS)

    update = _update(ANNA.telegram_id)
    asyncio.run(telegram_ui.handle_help(update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert text.startswith("What I can do") and "/costs" in text

    update = _update(ANNA.telegram_id)
    asyncio.run(telegram_ui.handle_start(update, MagicMock()))
    assert "/help" in update.message.reply_text.await_args.args[0]

    stranger = _update(9999, language_code="en")
    asyncio.run(telegram_ui.handle_help(stranger, MagicMock()))
    stranger.message.reply_text.assert_awaited_once_with(t("en", "unauthorized"))


def test_costs_report_in_english(conn):
    report = telegram_ui.build_costs_report(conn, ANNA, day="2026-09-21")
    assert "Spending today (2026-09-21):" in report
    assert "You: 0.0000 USD of 1.00 USD" in report


def test_limit_message_in_user_language():
    error = LimitExceededError("user", 1.0)
    assert str(error) == "Viršyta jūsų dienos naudojimo riba (1,00 USD). Bandykite rytoj."
    assert error.message("en") == "You have reached your daily usage limit (1.00 USD). Please try again tomorrow."


def test_approval_card_and_buttons_in_english(conn):
    text, keyboard = format_approval_card(7, "forget_fact", {"fact": "Likes tea"}, "en")
    assert "Confirmation required" in text and "Forget fact" in text and "Likes tea" in text
    buttons = [b.text for b in keyboard.inline_keyboard[0]]
    assert buttons == ["Confirm", "Reject"]

    approval_id = create_pending_approval(conn, ANNA.telegram_id, "forget_fact", {"fact_id": 1})
    ok, message, _ = process_approval_action(conn, approval_id, "reject", ANNA.telegram_id, language="en")
    assert ok and message == "Action rejected."


def test_tool_results_in_user_language(conn):
    assert forget_fact(conn, ANNA.telegram_id, 5, language="en") == "Fact 5 not found."
    assert add_note(conn, ANNA.telegram_id, "Buy milk", language="en") == "Note 1 saved."
    result = create_reminder(
        conn, ANNA.telegram_id, ANNA.timezone, "2026-09-22T09:00", "Call Tom",
        now=NOW, users_loader=lambda: USERS, language="en",
    )
    assert result == "Reminder 1 created: 2026-09-22 09:00 – Call Tom"


def test_reminder_frame_follows_recipient_language(conn):
    # Simonas (lt) reminds Anna (en): Anna gets an English frame, the text stays as written.
    create_reminder(
        conn, SIMONAS.telegram_id, SIMONAS.timezone, "2026-09-21T12:30", "Atnešti raktus",
        for_name="Anna", now=NOW, users_loader=lambda: USERS,
    )
    bot = AsyncMock()
    asyncio.run(deliver_due_reminders(conn, bot, now=NOW + timedelta(minutes=31), users_loader=lambda: USERS))
    assert bot.send_message.await_args.kwargs["text"] == "⏰ Reminder from Simonas: Atnešti raktus"


def test_failure_notice_follows_creator_language(conn):
    create_reminder(
        conn, ANNA.telegram_id, ANNA.timezone, "2026-09-21T10:30", "Bring keys",
        for_name="Simonas", now=NOW, users_loader=lambda: USERS,
    )
    bot = AsyncMock()
    bot.send_message.side_effect = [Forbidden("blocked"), None]
    asyncio.run(deliver_due_reminders(conn, bot, now=NOW + timedelta(minutes=31), users_loader=lambda: USERS))
    notice = bot.send_message.await_args_list[1].kwargs
    assert notice["chat_id"] == ANNA.telegram_id
    assert notice["text"].startswith("The reminder for Simonas could not be sent")


def test_command_menus_per_language():
    bot = MagicMock()
    bot.set_my_commands = AsyncMock()

    asyncio.run(telegram_ui.register_command_menus(bot, USERS))

    default_call, anna_call = bot.set_my_commands.await_args_list
    assert default_call.args[0] == bot_commands("lt")
    assert [c for c, _ in anna_call.args[0]] == ["help", "costs"]
    assert anna_call.kwargs["scope"] == BotCommandScopeChat(chat_id=ANNA.telegram_id)
