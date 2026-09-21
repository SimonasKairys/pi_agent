"""Tests for real OpenRouter costs, fact-extraction costs, and the /islaidos report."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.config import User
from agent.db import get_connection, get_user_daily_cost, record_usage
from agent.llm import LLMClient, LLMResponse
from agent.memory import extract_and_save_facts
from agent.telegram_ui import build_costs_report

DAY = "2026-09-21"
ADMIN = User(1, "Simonas", "simonas@example.com", "Europe/Vilnius", role="admin")
MEMBER = User(2, "Ruta", "ruta@example.com", "Europe/Vilnius", role="member")


@pytest.fixture
def conn(tmp_path: Path):
    connection = get_connection(tmp_path / "costs.db")
    yield connection
    connection.close()


def _client_returning(usage) -> tuple[LLMClient, AsyncMock]:
    client = LLMClient(api_key="test-key")
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="Labas", tool_calls=None))],
        usage=usage,
    )
    create = AsyncMock(return_value=response)
    client.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    return client, create


def test_generate_uses_cost_reported_by_openrouter():
    usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=500, total_tokens=1500, cost=0.000321)
    client, create = _client_returning(usage)

    result = asyncio.run(client.generate(messages=[{"role": "user", "content": "Labas"}]))

    assert result.cost_usd == pytest.approx(0.000321)
    assert create.call_args.kwargs["extra_body"]["usage"] == {"include": True}


def test_generate_falls_back_to_rates_without_reported_cost():
    usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=500, total_tokens=1500)
    client, _ = _client_returning(usage)

    result = asyncio.run(client.generate(messages=[{"role": "user", "content": "Labas"}]))

    # 1000 * 0.30 / 1M + 500 * 1.20 / 1M
    assert result.cost_usd == pytest.approx(0.0009)


def test_fact_extraction_cost_counts_toward_daily_limit(conn):
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=LLMResponse(
        content='[{"fact": "Gyvena Vilniuje", "importance": 8}]',
        prompt_tokens=100,
        completion_tokens=20,
        total_tokens=120,
        cost_usd=0.0004,
    ))

    saved = asyncio.run(extract_and_save_facts(
        conn, user_id=ADMIN.telegram_id, user_message="Gyvenu Vilniuje",
        assistant_message="Supratau", llm_client=llm,
    ))

    assert len(saved) == 1
    assert get_user_daily_cost(conn, ADMIN.telegram_id) == pytest.approx(0.0004)


def test_member_report_shows_only_own_spending(conn):
    record_usage(conn, ADMIN.telegram_id, 0.0123, day=DAY)
    record_usage(conn, MEMBER.telegram_id, 0.0045, events_created=2, day=DAY)

    report = build_costs_report(conn, MEMBER, day=DAY)

    assert "Jūsų: 0,0045 USD iš 1,00 USD" in report
    assert "Sukurta įvykių: 2 iš 20" in report
    assert "Visos sistemos" not in report
    assert "0,0123" not in report


def test_admin_report_shows_every_user(conn, monkeypatch):
    monkeypatch.setattr("agent.telegram_ui.load_users", lambda: [ADMIN, MEMBER])
    record_usage(conn, ADMIN.telegram_id, 0.0123, day=DAY)
    record_usage(conn, MEMBER.telegram_id, 0.0045, day=DAY)

    report = build_costs_report(conn, ADMIN, day=DAY)

    assert "Visos sistemos: 0,0168 USD iš 4,00 USD" in report
    assert "Simonas: 0,0123 USD" in report
    assert "Ruta: 0,0045 USD" in report
