"""Tests for /pagalba and the Telegram command menu."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import agent.telegram_ui as telegram_ui
from agent.config import User
from agent.prompts import BOT_COMMANDS, HELP_MESSAGE, START_MESSAGE, UNAUTHORIZED_MESSAGE

SIMONAS = User(1001, "Simonas", "simonas@example.com", "Europe/Vilnius")


def _update(user_id: int):
    update = MagicMock()
    update.effective_chat.type = "private"
    update.effective_user.id = user_id
    update.message.reply_text = AsyncMock()
    return update


def test_help_lists_every_feature_and_command():
    for word in ("Kalendorius", "Priminimai", "Užrašai", "Atmintis", "Paieška", "/pagalba", "/islaidos"):
        assert word in HELP_MESSAGE, word
    assert "/pagalba" in START_MESSAGE


def test_help_for_authorized_user(monkeypatch):
    monkeypatch.setattr(telegram_ui, "load_users", lambda: [SIMONAS])
    update = _update(SIMONAS.telegram_id)

    asyncio.run(telegram_ui.handle_help(update, MagicMock()))

    update.message.reply_text.assert_awaited_once_with(HELP_MESSAGE)


def test_help_refuses_unknown_user(monkeypatch):
    monkeypatch.setattr(telegram_ui, "load_users", lambda: [SIMONAS])
    update = _update(9999)

    asyncio.run(telegram_ui.handle_help(update, MagicMock()))

    update.message.reply_text.assert_awaited_once_with(UNAUTHORIZED_MESSAGE)


def test_post_init_registers_menu_and_starts_reminders(monkeypatch):
    started = AsyncMock()
    monkeypatch.setattr(telegram_ui, "start_reminder_loop", started)
    monkeypatch.setattr(telegram_ui, "load_users", lambda: [SIMONAS])
    app = SimpleNamespace(bot=SimpleNamespace(set_my_commands=AsyncMock()), bot_data={})

    asyncio.run(telegram_ui.post_init(app))

    app.bot.set_my_commands.assert_awaited_once_with(list(BOT_COMMANDS))
    started.assert_awaited_once_with(app)


def test_post_init_survives_menu_failure(monkeypatch):
    started = AsyncMock()
    monkeypatch.setattr(telegram_ui, "start_reminder_loop", started)
    app = SimpleNamespace(
        bot=SimpleNamespace(set_my_commands=AsyncMock(side_effect=RuntimeError("network"))),
        bot_data={},
    )

    asyncio.run(telegram_ui.post_init(app))

    started.assert_awaited_once_with(app)
