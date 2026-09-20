"""Tests for agent/telegram_ui.py."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest

from agent.config import User
from agent.db import get_connection, record_usage, MAX_USER_DAILY_COST_USD
from agent.llm import LLMResponse
from agent.prompts import (
    START_MESSAGE,
    UNAUTHORIZED_MESSAGE,
    ERROR_MESSAGE,
)
from agent.telegram_ui import (
    split_message,
    get_user_lock,
    handle_start,
    handle_message,
    SPLIT_LIMIT,
)


def test_split_message_short():
    text = "Trumpas tekstas"
    chunks = split_message(text, limit=100)
    assert chunks == ["Trumpas tekstas"]


def test_split_message_long_paragraphs():
    p1 = "A" * 3000
    p2 = "B" * 3000
    full_text = f"{p1}\n\n{p2}"

    chunks = split_message(full_text, limit=SPLIT_LIMIT)
    assert len(chunks) == 2
    assert chunks[0] == p1
    assert chunks[1] == p2
    assert len(chunks[0]) <= SPLIT_LIMIT
    assert len(chunks[1]) <= SPLIT_LIMIT


def test_split_message_hard_fallback():
    long_single_word = "Z" * 9000
    chunks = split_message(long_single_word, limit=4000)
    assert len(chunks) == 3
    assert len(chunks[0]) == 4000
    assert len(chunks[1]) == 4000
    assert len(chunks[2]) == 1000
    assert "".join(chunks) == long_single_word


def test_user_locks():
    lock1 = get_user_lock(101)
    lock1_again = get_user_lock(101)
    lock2 = get_user_lock(102)

    assert lock1 is lock1_again
    assert lock1 is not lock2


def test_handle_start_authorized(monkeypatch: pytest.MonkeyPatch):
    authorized_user = User(
        telegram_id=111,
        name="Simonas",
        email="simonas@example.com",
        timezone="Europe/Vilnius",
    )
    monkeypatch.setattr("agent.telegram_ui.load_users", lambda: [authorized_user])

    update = MagicMock()
    update.effective_chat.type = "private"
    update.effective_user.id = 111
    update.message.reply_text = AsyncMock()

    context = MagicMock()

    asyncio.run(handle_start(update, context))

    update.message.reply_text.assert_awaited_once_with(START_MESSAGE)


def test_handle_start_unauthorized(monkeypatch: pytest.MonkeyPatch):
    authorized_user = User(
        telegram_id=111,
        name="Simonas",
        email="simonas@example.com",
        timezone="Europe/Vilnius",
    )
    monkeypatch.setattr("agent.telegram_ui.load_users", lambda: [authorized_user])

    update = MagicMock()
    update.effective_chat.type = "private"
    update.effective_user.id = 999  # Not authorized
    update.message.reply_text = AsyncMock()

    context = MagicMock()

    asyncio.run(handle_start(update, context))

    update.message.reply_text.assert_awaited_once_with(UNAUTHORIZED_MESSAGE)


def test_handle_group_chat_ignored():
    update = MagicMock()
    update.effective_chat.type = "group"
    update.effective_user.id = 111
    update.message.reply_text = AsyncMock()

    context = MagicMock()

    asyncio.run(handle_start(update, context))
    update.message.reply_text.assert_not_awaited()

    asyncio.run(handle_message(update, context))
    update.message.reply_text.assert_not_awaited()


def test_handle_message_authorized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setattr("agent.db.get_db_path", lambda: db_file)
    monkeypatch.setattr("agent.telegram_ui.get_connection", lambda: get_connection(db_file))

    authorized_user = User(
        telegram_id=111,
        name="Simonas",
        email="simonas@example.com",
        timezone="Europe/Vilnius",
    )
    monkeypatch.setattr("agent.telegram_ui.load_users", lambda: [authorized_user])

    fake_llm = MagicMock()
    fake_llm.generate = AsyncMock(return_value=LLMResponse(
        content="Atsakymas iš modelio",
        prompt_tokens=20,
        completion_tokens=10,
        total_tokens=30,
        cost_usd=0.00001,
    ))

    update = MagicMock()
    update.effective_chat.type = "private"
    update.effective_user.id = 111
    update.message.text = "Koks šiandien oras?"
    update.message.reply_text = AsyncMock()
    update.message.chat.send_action = AsyncMock()

    context = MagicMock()
    context.bot_data = {"llm_client": fake_llm}

    asyncio.run(handle_message(update, context))

    fake_llm.generate.assert_awaited_once()
    update.message.reply_text.assert_awaited_once_with("Atsakymas iš modelio")

    # Verify messages saved to database
    conn = get_connection(db_file)
    cursor = conn.cursor()
    cursor.execute("SELECT role, content FROM messages WHERE user_id = 111 ORDER BY id ASC;")
    rows = cursor.fetchall()
    assert len(rows) == 2
    assert rows[0]["role"] == "user"
    assert rows[0]["content"] == "Koks šiandien oras?"
    assert rows[1]["role"] == "assistant"
    assert rows[1]["content"] == "Atsakymas iš modelio"


def test_handle_message_limit_exceeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setattr("agent.db.get_db_path", lambda: db_file)
    monkeypatch.setattr("agent.telegram_ui.get_connection", lambda: get_connection(db_file))

    authorized_user = User(
        telegram_id=111,
        name="Simonas",
        email="simonas@example.com",
        timezone="Europe/Vilnius",
    )
    monkeypatch.setattr("agent.telegram_ui.load_users", lambda: [authorized_user])

    # Record usage exceeding limit
    conn = get_connection(db_file)
    record_usage(conn, user_id=111, cost_usd=MAX_USER_DAILY_COST_USD + 0.5)

    update = MagicMock()
    update.effective_chat.type = "private"
    update.effective_user.id = 111
    update.message.text = "Dar viena užklausa"
    update.message.reply_text = AsyncMock()

    context = MagicMock()

    asyncio.run(handle_message(update, context))

    update.message.reply_text.assert_awaited_once()
    reply = update.message.reply_text.call_args[0][0]
    assert "Viršyta" in reply

