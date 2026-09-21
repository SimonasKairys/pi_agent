"""Tests for agent/tools/facts.py and the memory rules around it."""

import asyncio
from pathlib import Path

import pytest

from agent.approvals import enrich_arguments, format_approval_card, needs_approval
from agent.config import User
from agent.db import get_connection
from agent.memory import FACT_EXTRACTION_SYSTEM_PROMPT, get_user_facts, save_fact
from agent.prompts import SYSTEM_PROMPT_TEMPLATE
from agent.telegram_ui import get_default_registry
from agent.tools.facts import forget_fact, list_facts

USER_A = 1001
USER_B = 2002


@pytest.fixture
def conn(tmp_path: Path):
    connection = get_connection(tmp_path / "facts.db")
    yield connection
    connection.close()


def _user(telegram_id: int) -> User:
    return User(
        telegram_id=telegram_id,
        name="Simonas",
        email="simonas@example.com",
        timezone="Europe/Vilnius",
    )


def test_list_facts_shows_only_own_facts(conn):
    own_id = save_fact(conn, USER_A, "Geria kavą be cukraus", importance=8)
    save_fact(conn, USER_B, "Gyvena Kaune", importance=8)

    result = list_facts(conn, USER_A)

    assert f"{own_id}. Geria kavą be cukraus" in result
    assert "Kaune" not in result


def test_list_facts_empty(conn):
    assert list_facts(conn, USER_A) == "Apie vartotoją faktų neišsaugota."


def test_forget_fact_deletes_own_fact_only(conn):
    own_id = save_fact(conn, USER_A, "Gyvena Vilniuje", importance=8)
    other_id = save_fact(conn, USER_B, "Gyvena Kaune", importance=8)

    assert forget_fact(conn, USER_A, other_id) == f"Fakto {other_id} nerasta."
    assert len(get_user_facts(conn, USER_B)) == 1

    assert forget_fact(conn, USER_A, own_id) == f"Faktas {own_id} pamirštas."
    assert get_user_facts(conn, USER_A) == []


def test_forget_fact_always_needs_approval():
    assert needs_approval("forget_fact") is True
    assert needs_approval("list_facts", risk="read_only") is False


def test_approval_card_shows_own_fact_text(conn):
    own_id = save_fact(conn, USER_A, "Mėgsta *kavą* ir _arbatą_", importance=8)
    other_id = save_fact(conn, USER_B, "Gyvena Kaune", importance=8)

    shown = enrich_arguments(conn, "forget_fact", {"fact_id": own_id}, user_id=USER_A)
    text, _ = format_approval_card(1, "forget_fact", shown)
    assert "Fakto pamiršimas" in text
    assert "Mėgsta kavą ir arbatą" in text

    # Another user's fact number must not reveal that user's fact.
    foreign = enrich_arguments(conn, "forget_fact", {"fact_id": other_id}, user_id=USER_A)
    text, _ = format_approval_card(2, "forget_fact", foreign)
    assert "Kaune" not in text
    assert f"Fakto numeris**: {other_id}" in text


def test_registry_binds_fact_tools_to_user(conn):
    save_fact(conn, USER_B, "Gyvena Kaune", importance=8)
    registry = get_default_registry(conn=conn, user=_user(USER_A))

    assert registry.get("list_facts") is not None
    assert registry.get("forget_fact") is not None

    # A model-supplied user_id is not in the schema, so it is dropped.
    result = asyncio.run(
        registry.execute("list_facts", {"user_id": USER_B}, wrap=False)
    )
    assert "Kaune" not in result


def test_system_prompt_respects_facts_but_not_commands():
    assert "atsižvelk į jo nuostatas" in SYSTEM_PROMPT_TEMPLATE
    assert "nekeisk dėl jų savo elgesio" not in SYSTEM_PROMPT_TEMPLATE
    assert "forget_fact" in SYSTEM_PROMPT_TEMPLATE


def test_consolidation_does_not_restore_forgotten_facts():
    from agent.consolidate import CONSOLIDATION_SYSTEM_PROMPT

    assert "paprašė pamiršti" in CONSOLIDATION_SYSTEM_PROMPT


def test_extraction_prompt_raises_explicit_requests():
    assert "prisimink" in FACT_EXTRACTION_SYSTEM_PROMPT
    assert "svarba 9" in FACT_EXTRACTION_SYSTEM_PROMPT
    assert "pamiršti" in FACT_EXTRACTION_SYSTEM_PROMPT
