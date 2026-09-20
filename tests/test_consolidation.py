"""Tests for agent/consolidate.py (Step 4.4)."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest

from agent.config import User
from agent.consolidate import consolidate_user, run_consolidation
from agent.db import get_connection
from agent.llm import LLMResponse
from agent.memory import get_user_facts, save_fact


def test_empty_history_causes_no_error(tmp_path: Path):
    """When a user has no messages in the last 24 hours, consolidation completes cleanly without LLM calls."""
    db_file = tmp_path / "test.db"
    conn = get_connection(db_file)

    try:
        user = User(telegram_id=101, name="Simonas", email="s@example.com", timezone="Europe/Vilnius")
        fake_llm = MagicMock()
        fake_llm.generate = AsyncMock()

        created_ids = asyncio.run(consolidate_user(conn, user, fake_llm))

        assert created_ids == []
        fake_llm.generate.assert_not_called()
        assert len(get_user_facts(conn, 101)) == 0
    finally:
        conn.close()


def test_consolidation_creates_insights_and_records_usage(tmp_path: Path):
    """Consolidation generates insights from recent history and logs usage + journal entry."""
    db_file = tmp_path / "test.db"
    conn = get_connection(db_file)

    try:
        user_id = 102
        user = User(telegram_id=user_id, name="Simonas", email="s@example.com", timezone="Europe/Vilnius")

        # Add recent messages (within 24h)
        conn.execute(
            "INSERT INTO messages (user_id, role, content, tokens, created_at) VALUES (?, ?, ?, ?, ?);",
            (user_id, "user", "Nuo šiol keliuosi 06:00 ir einu pabėgioti.", 10, "2026-09-20T08:00:00"),
        )
        conn.execute(
            "INSERT INTO messages (user_id, role, content, tokens, created_at) VALUES (?, ?, ?, ?, ?);",
            (user_id, "assistant", "Puiku, pažymėjau tavo naują įprotį.", 10, "2026-09-20T08:01:00"),
        )
        conn.execute(
            "INSERT INTO messages (user_id, role, content, tokens, created_at) VALUES (?, ?, ?, ?, ?);",
            (user_id, "user", "Ir planuoju bėgioti kiekvieną dieną.", 10, "2026-09-20T08:05:00"),
        )
        conn.commit()

        # Pre-existing fact
        save_fact(conn, user_id=user_id, fact="Mėgsta aktyvų sportą", importance=8)

        fake_llm = MagicMock()
        fake_llm.generate = AsyncMock(return_value=LLMResponse(
            content="""[
              {"fact": "Kiekvieną rytą keliasi 06:00 ir bėgioja", "importance": 9},
              {"fact": "Siekia kasdienio bėgiojimo rutinos", "importance": 8}
            ]""",
            prompt_tokens=80,
            completion_tokens=40,
            total_tokens=120,
            cost_usd=0.000036,
        ))

        created_ids = asyncio.run(consolidate_user(
            conn=conn,
            user=user,
            llm_client=fake_llm,
            since_timestamp="2026-09-20T00:00:00",
        ))

        assert len(created_ids) == 2
        fake_llm.generate.assert_called_once()

        # Check that facts are now stored
        user_facts = get_user_facts(conn, user_id)
        assert len(user_facts) == 3  # 1 old + 2 new
        fact_texts = [f["fact"] for f in user_facts]
        assert "Kiekvieną rytą keliasi 06:00 ir bėgioja" in fact_texts
        assert "Siekia kasdienio bėgiojimo rutinos" in fact_texts

        # Check that usage was recorded in db
        cursor = conn.cursor()
        cursor.execute("SELECT cost_usd FROM usage WHERE user_id = ?;", (user_id,))
        row = cursor.fetchone()
        assert row is not None
        assert row["cost_usd"] == pytest.approx(0.000036)
    finally:
        conn.close()


def test_consolidation_processes_each_user_separately(tmp_path: Path):
    """Consolidation processes each user strictly in isolation without mixing data."""
    db_file = tmp_path / "test.db"
    conn = get_connection(db_file)

    try:
        user_a = User(telegram_id=111, name="Simonas", email="s@example.com", timezone="Europe/Vilnius")
        user_b = User(telegram_id=222, name="Rūta", email="r@example.com", timezone="Europe/Vilnius")

        # History for User A
        conn.execute(
            "INSERT INTO messages (user_id, role, content, tokens, created_at) VALUES (?, ?, ?, ?, ?);",
            (111, "user", "Noriu pradėti mokytis japonų kalbos.", 10, "2026-09-20T10:00:00"),
        )
        # History for User B
        conn.execute(
            "INSERT INTO messages (user_id, role, content, tokens, created_at) VALUES (?, ?, ?, ?, ?);",
            (222, "user", "Persikėliau gyventi į naują butą.", 10, "2026-09-20T11:00:00"),
        )
        conn.commit()

        prompts_by_user: dict[str, str] = {}

        async def mock_generate(messages):
            user_prompt = messages[1]["content"]
            if "Simonas" in user_prompt:
                prompts_by_user["Simonas"] = user_prompt
                return LLMResponse(
                    content='[{"fact": "Mokosi japonų kalbos", "importance": 8}]',
                    prompt_tokens=50,
                    completion_tokens=20,
                    total_tokens=70,
                    cost_usd=0.00002,
                )
            else:
                prompts_by_user["Rūta"] = user_prompt
                return LLMResponse(
                    content='[{"fact": "Gyvena naujame bute", "importance": 8}]',
                    prompt_tokens=50,
                    completion_tokens=20,
                    total_tokens=70,
                    cost_usd=0.00002,
                )

        fake_llm = MagicMock()
        fake_llm.generate = AsyncMock(side_effect=mock_generate)

        results = asyncio.run(run_consolidation(
            conn=conn,
            llm_client=fake_llm,
            users=[user_a, user_b],
            since_timestamp="2026-09-20T00:00:00",
        ))

        assert len(results[111]) == 1
        assert len(results[222]) == 1

        # Prompts verification: user A's prompt has NO user B info, and vice versa
        assert "Simonas" in prompts_by_user["Simonas"]
        assert "Rūta" not in prompts_by_user["Simonas"]
        assert "japonų kalbos" in prompts_by_user["Simonas"]
        assert "naują butą" not in prompts_by_user["Simonas"]

        assert "Rūta" in prompts_by_user["Rūta"]
        assert "Simonas" not in prompts_by_user["Rūta"]
        assert "naują butą" in prompts_by_user["Rūta"]
        assert "japonų kalbos" not in prompts_by_user["Rūta"]

        # Facts verification in DB: strictly isolated
        facts_a = get_user_facts(conn, 111)
        facts_b = get_user_facts(conn, 222)

        assert len(facts_a) == 1
        assert facts_a[0]["fact"] == "Mokosi japonų kalbos"

        assert len(facts_b) == 1
        assert facts_b[0]["fact"] == "Gyvena naujame bute"
    finally:
        conn.close()


def test_consolidation_error_in_one_user_does_not_abort_others(tmp_path: Path):
    """Failure during one user's consolidation does not prevent other users from being processed."""
    db_file = tmp_path / "test.db"
    conn = get_connection(db_file)

    try:
        user_failing = User(telegram_id=999, name="Klaidingas", email="k@example.com", timezone="Europe/Vilnius")
        user_ok = User(telegram_id=888, name="Geras", email="g@example.com", timezone="Europe/Vilnius")

        conn.execute(
            "INSERT INTO messages (user_id, role, content, tokens, created_at) VALUES (?, ?, ?, ?, ?);",
            (999, "user", "Žinutė 1", 10, "2026-09-20T10:00:00"),
        )
        conn.execute(
            "INSERT INTO messages (user_id, role, content, tokens, created_at) VALUES (?, ?, ?, ?, ?);",
            (888, "user", "Žinutė 2", 10, "2026-09-20T10:00:00"),
        )
        conn.commit()

        async def mock_generate(messages):
            if "Klaidingas" in messages[1]["content"]:
                raise RuntimeError("API gedimas")
            return LLMResponse(
                content='[{"fact": "Geras faktas", "importance": 8}]',
                prompt_tokens=50,
                completion_tokens=20,
                total_tokens=70,
                cost_usd=0.00002,
            )

        fake_llm = MagicMock()
        fake_llm.generate = AsyncMock(side_effect=mock_generate)

        results = asyncio.run(run_consolidation(
            conn=conn,
            llm_client=fake_llm,
            users=[user_failing, user_ok],
            since_timestamp="2026-09-20T00:00:00",
        ))

        assert results[999] == []
        assert len(results[888]) == 1
        assert len(get_user_facts(conn, 888)) == 1
    finally:
        conn.close()
