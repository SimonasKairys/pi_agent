"""Tests for profile loading and user isolation in system prompt (Step 4.3)."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest

from agent.config import User
from agent.db import get_connection
from agent.llm import LLMResponse
from agent.memory import save_fact
from agent.prompts import build_system_prompt
from agent.telegram_ui import handle_message


def test_build_system_prompt_with_facts():
    """System prompt includes user facts under a dedicated section."""
    facts = [
        "Mėgsta dirbti nuotoliniu būdu",
        "Kavos geria tik iki 14:00",
    ]
    prompt = build_system_prompt(
        name="Simonas",
        timezone_name="Europe/Vilnius",
        allowed_names=["Rūta"],
        facts=facts,
        current_time_str="2026-09-20 12:00",
    )

    assert "Žinomi faktai apie vartotoją:" in prompt
    assert "- Mėgsta dirbti nuotoliniu būdu" in prompt
    assert "- Kavos geria tik iki 14:00" in prompt
    assert "Simonas" in prompt


def test_build_system_prompt_without_facts():
    """System prompt omits facts block when facts list is empty or None."""
    prompt_none = build_system_prompt(
        name="Simonas",
        facts=None,
    )
    assert "Žinomi faktai apie vartotoją:" not in prompt_none

    prompt_empty = build_system_prompt(
        name="Simonas",
        facts=[],
    )
    assert "Žinomi faktai apie vartotoją:" not in prompt_empty


def test_profile_user_isolation_direct(tmp_path: Path):
    """Factual profiles must remain completely isolated between different users."""
    db_file = tmp_path / "test.db"
    conn = get_connection(db_file)

    try:
        user_a_id = 111
        user_b_id = 222

        save_fact(conn, user_a_id, "Vartotojas A mėgsta espresą", importance=8)
        save_fact(conn, user_a_id, "Vartotojas A vairuoja dviratį", importance=9)

        save_fact(conn, user_b_id, "Vartotojas B mėgsta žaliąją arbatą", importance=8)
        save_fact(conn, user_b_id, "Vartotojas B gyvena Klaipėdoje", importance=9)

        from agent.memory import get_user_facts

        facts_a = get_user_facts(conn, user_a_id)
        facts_b = get_user_facts(conn, user_b_id)

        prompt_a = build_system_prompt(
            name="Vartotojas A",
            facts=[f["fact"] for f in facts_a],
        )
        prompt_b = build_system_prompt(
            name="Vartotojas B",
            facts=[f["fact"] for f in facts_b],
        )

        # User A's prompt must contain User A's facts and NONE of User B's facts
        assert "Vartotojas A mėgsta espresą" in prompt_a
        assert "Vartotojas A vairuoja dviratį" in prompt_a
        assert "žaliąją arbatą" not in prompt_a
        assert "Klaipėdoje" not in prompt_a

        # User B's prompt must contain User B's facts and NONE of User A's facts
        assert "Vartotojas B mėgsta žaliąją arbatą" in prompt_b
        assert "Vartotojas B gyvena Klaipėdoje" in prompt_b
        assert "espresą" not in prompt_b
        assert "dviratį" not in prompt_b
    finally:
        conn.close()


def test_profile_loaded_in_telegram_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """End-to-end test verifying handle_message loads the user's facts into context without leakage."""
    db_file = tmp_path / "test.db"
    monkeypatch.setattr("agent.db.get_db_path", lambda: db_file)
    monkeypatch.setattr("agent.telegram_ui.get_connection", lambda: get_connection(db_file))

    user_a = User(telegram_id=111, name="Simonas", email="s@example.com", timezone="Europe/Vilnius")
    user_b = User(telegram_id=222, name="Rūta", email="r@example.com", timezone="Europe/Vilnius")
    monkeypatch.setattr("agent.telegram_ui.load_users", lambda: [user_a, user_b])

    # Pre-populate facts in SQLite
    conn = get_connection(db_file)
    save_fact(conn, 111, "Simonas yra veganas", importance=9)
    save_fact(conn, 222, "Rūta dirba architekte", importance=8)
    conn.close()

    captured_prompts: list[str] = []

    async def mock_generate(messages, tools=None, max_tokens=1500):
        # Capture the system prompt (first message)
        system_msg = messages[0]["content"]
        captured_prompts.append(system_msg)
        return LLMResponse(
            content="Atsakymas",
            prompt_tokens=30,
            completion_tokens=10,
            total_tokens=40,
            cost_usd=0.00001,
        )

    fake_llm = MagicMock()
    fake_llm.generate = AsyncMock(side_effect=mock_generate)

    # 1. Simonas sends a message
    update_a = MagicMock()
    update_a.effective_chat.type = "private"
    update_a.effective_user.id = 111
    update_a.message.text = "Sveikas, kur šiandien papietauti?"
    update_a.message.reply_text = AsyncMock()
    update_a.message.chat.send_action = AsyncMock()

    context_a = MagicMock()
    context_a.bot_data = {"llm_client": fake_llm}

    asyncio.run(handle_message(update_a, context_a))

    assert len(captured_prompts) == 1
    prompt_simonas = captured_prompts[0]
    assert "Simonas yra veganas" in prompt_simonas
    assert "Rūta dirba architekte" not in prompt_simonas
    assert "architekte" not in prompt_simonas

    # 2. Rūta sends a message
    update_b = MagicMock()
    update_b.effective_chat.type = "private"
    update_b.effective_user.id = 222
    update_b.message.text = "Sveikas, man reikia patarimo."
    update_b.message.reply_text = AsyncMock()
    update_b.message.chat.send_action = AsyncMock()

    context_b = MagicMock()
    context_b.bot_data = {"llm_client": fake_llm}

    asyncio.run(handle_message(update_b, context_b))

    assert len(captured_prompts) == 2
    prompt_ruta = captured_prompts[1]
    assert "Rūta dirba architekte" in prompt_ruta
    assert "Simonas yra veganas" not in prompt_ruta
    assert "veganas" not in prompt_ruta
