"""Tests for agent/memory.py."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest

from agent.db import get_connection
from agent.llm import LLMResponse
from agent.memory import (
    IMPORTANCE_THRESHOLD,
    MAX_USER_FACTS,
    delete_fact,
    extract_and_save_facts,
    extract_facts,
    get_user_facts,
    save_fact,
    search_user_facts,
)


def test_skip_low_importance_facts(tmp_path: Path):
    """Facts with importance below 7 must be discarded."""
    db_file = tmp_path / "test.db"
    conn = get_connection(db_file)

    try:
        user_id = 1001

        # Importance 1 to 6 should be skipped
        for imp in range(1, 7):
            res = save_fact(conn, user_id, f"Faktas su svarba {imp}", importance=imp)
            assert res is None

        # Verify no facts were stored
        facts = get_user_facts(conn, user_id)
        assert len(facts) == 0

        # Importance 7 and above should be saved
        res7 = save_fact(conn, user_id, "Faktas su svarba 7", importance=7)
        assert res7 is not None
        assert res7 > 0

        res10 = save_fact(conn, user_id, "Faktas su svarba 10", importance=10)
        assert res10 is not None
        assert res10 > 0

        facts = get_user_facts(conn, user_id)
        assert len(facts) == 2
        # Ordered by importance DESC
        assert facts[0]["importance"] == 10
        assert facts[1]["importance"] == 7
    finally:
        conn.close()


def test_duplicate_facts_skipped(tmp_path: Path):
    """Duplicate facts for the same user must not be inserted."""
    db_file = tmp_path / "test.db"
    conn = get_connection(db_file)

    try:
        user_id = 1002
        fact_text = "Mėgsta gerti žaliąją arbatą"

        id1 = save_fact(conn, user_id, fact_text, importance=8)
        assert id1 is not None

        # Same text exact match
        id2 = save_fact(conn, user_id, fact_text, importance=9)
        assert id2 is None

        # Same text case-insensitive
        id3 = save_fact(conn, user_id, fact_text.upper(), importance=8)
        assert id3 is None

        # Same text with whitespace
        id4 = save_fact(conn, user_id, f"  {fact_text}  ", importance=8)
        assert id4 is None

        # Only one fact exists
        facts = get_user_facts(conn, user_id)
        assert len(facts) == 1
        assert facts[0]["id"] == id1

        # Another user CAN have the same fact text (user isolation)
        other_user_id = 2002
        other_id = save_fact(conn, other_user_id, fact_text, importance=8)
        assert other_id is not None
        assert other_id != id1

        other_facts = get_user_facts(conn, other_user_id)
        assert len(other_facts) == 1
        assert other_facts[0]["id"] == other_id
    finally:
        conn.close()


def test_capacity_limit_eviction(tmp_path: Path):
    """When max_facts limit is reached, the least important and oldest fact must be evicted."""
    db_file = tmp_path / "test.db"
    conn = get_connection(db_file)

    try:
        user_id = 1003
        limit = 3

        # Insert 3 facts with distinct importance and timestamps
        id1 = save_fact(
            conn, user_id, "Faktas 1 (svarba 7, senas)",
            importance=7, created_at="2026-09-20T10:00:00", max_facts=limit,
        )
        id2 = save_fact(
            conn, user_id, "Faktas 2 (svarba 7, naujesnis)",
            importance=7, created_at="2026-09-20T11:00:00", max_facts=limit,
        )
        id3 = save_fact(
            conn, user_id, "Faktas 3 (svarba 9, naujausias)",
            importance=9, created_at="2026-09-20T12:00:00", max_facts=limit,
        )

        assert len(get_user_facts(conn, user_id)) == 3

        # Insert 4th fact: should trigger eviction of id1 (lowest importance 7, oldest timestamp)
        id4 = save_fact(
            conn, user_id, "Faktas 4 (svarba 8)",
            importance=8, created_at="2026-09-20T13:00:00", max_facts=limit,
        )
        assert id4 is not None

        remaining_facts = get_user_facts(conn, user_id)
        assert len(remaining_facts) == 3
        remaining_ids = {f["id"] for f in remaining_facts}

        # id1 was evicted!
        assert id1 not in remaining_ids
        assert remaining_ids == {id2, id3, id4}

        # Insert 5th fact with low importance: id2 (importance 7) should be evicted
        id5 = save_fact(
            conn, user_id, "Faktas 5 (svarba 10)",
            importance=10, created_at="2026-09-20T14:00:00", max_facts=limit,
        )
        remaining_facts2 = get_user_facts(conn, user_id)
        assert len(remaining_facts2) == 3
        remaining_ids2 = {f["id"] for f in remaining_facts2}
        assert id2 not in remaining_ids2
        assert remaining_ids2 == {id3, id4, id5}
    finally:
        conn.close()


def test_user_isolation(tmp_path: Path):
    """User facts must remain strictly isolated across all queries and FTS search."""
    db_file = tmp_path / "test.db"
    conn = get_connection(db_file)

    try:
        user_a = 100
        user_b = 200

        save_fact(conn, user_a, "Simonas gyvena Vilniuje", importance=8)
        save_fact(conn, user_a, "Simonas vairuoja elektromobilį", importance=7)
        save_fact(conn, user_b, "Rūta gyvena Kaune", importance=9)
        save_fact(conn, user_b, "Rūta mėgsta tenisą", importance=8)

        facts_a = get_user_facts(conn, user_a)
        facts_b = get_user_facts(conn, user_b)

        assert len(facts_a) == 2
        assert len(facts_b) == 2

        # A facts do not contain B facts
        assert all("Rūta" not in f["fact"] for f in facts_a)
        assert all("Simonas" not in f["fact"] for f in facts_b)

        # FTS search isolation
        fts_a = search_user_facts(conn, user_a, "Vilniuje")
        assert len(fts_a) == 1
        assert "Vilniuje" in fts_a[0]["fact"]

        fts_b_attempt = search_user_facts(conn, user_b, "Vilniuje")
        assert len(fts_b_attempt) == 0

        fts_b = search_user_facts(conn, user_b, "Kaune")
        assert len(fts_b) == 1
        assert "Kaune" in fts_b[0]["fact"]

        fts_a_attempt = search_user_facts(conn, user_a, "Kaune")
        assert len(fts_a_attempt) == 0

        # Delete isolation: user B cannot delete user A's fact
        a_fact_id = facts_a[0]["id"]
        deleted_by_wrong_user = delete_fact(conn, user_id=user_b, fact_id=a_fact_id)
        assert deleted_by_wrong_user is False
        assert len(get_user_facts(conn, user_a)) == 2

        deleted_by_owner = delete_fact(conn, user_id=user_a, fact_id=a_fact_id)
        assert deleted_by_owner is True
        assert len(get_user_facts(conn, user_a)) == 1
    finally:
        conn.close()


def test_extract_facts_from_llm():
    """Test extracting facts from mocked LLM response."""
    fake_llm = MagicMock()
    fake_llm.generate = AsyncMock(return_value=LLMResponse(
        content="""```json
[
  {"fact": "Dirba duomenų inžinieriumi", "importance": 9},
  {"fact": "Paklausė kelinta dabar valanda", "importance": 3},
  {"fact": "Lankosi sporto salėje pirmadieniais", "importance": 7}
]
```""",
        prompt_tokens=50,
        completion_tokens=40,
        total_tokens=90,
        cost_usd=0.00003,
    ))

    extracted = asyncio.run(extract_facts(
        fake_llm,
        user_message="Aš dirbu duomenų inžinieriumi ir lankausi salėje pirmadieniais. Kelinta dabar valanda?",
        assistant_message="Dabar yra 15:00. Sekmės darbe ir treniruotėse!",
    ))

    assert len(extracted) == 3
    assert extracted[0]["fact"] == "Dirba duomenų inžinieriumi"
    assert extracted[0]["importance"] == 9
    assert extracted[1]["importance"] == 3
    assert extracted[2]["importance"] == 7


def test_extract_and_save_facts(tmp_path: Path):
    """Test extracting facts via LLM and persisting only those with importance >= 7."""
    db_file = tmp_path / "test.db"
    conn = get_connection(db_file)

    try:
        fake_llm = MagicMock()
        fake_llm.generate = AsyncMock(return_value=LLMResponse(
            content='[{"fact": "Alergiškas medui", "importance": 10}, {"fact": "Nori miego", "importance": 4}]',
            prompt_tokens=40,
            completion_tokens=25,
            total_tokens=65,
            cost_usd=0.00002,
        ))

        user_id = 555
        saved_ids = asyncio.run(extract_and_save_facts(
            conn=conn,
            user_id=user_id,
            user_message="Negaliu valgyti medaus, nes esu alergiškas. Be to dabar noriu miego.",
            assistant_message="Supratau, pasižymėjau dėl medaus.",
            llm_client=fake_llm,
        ))

        # Only the fact with importance 10 was saved (importance 4 discarded)
        assert len(saved_ids) == 1
        facts = get_user_facts(conn, user_id)
        assert len(facts) == 1
        assert facts[0]["fact"] == "Alergiškas medui"
        assert facts[0]["importance"] == 10
    finally:
        conn.close()
