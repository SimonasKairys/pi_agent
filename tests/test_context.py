"""Tests for agent/context.py."""

import asyncio
from pathlib import Path
from agent.db import get_connection
from agent.context import (
    estimate_tokens,
    add_message,
    get_latest_summary,
    check_and_summarize,
    build_context,
    SUMMARIZE_THRESHOLD,
    VERBATIM_MESSAGES_COUNT,
)


def test_estimate_tokens():
    assert estimate_tokens("1234") == 1
    assert estimate_tokens("12345678") == 2
    assert estimate_tokens("abc") == 0


def test_short_history(tmp_path: Path):
    db_file = tmp_path / "test.db"
    conn = get_connection(db_file)

    user_id = 100
    for i in range(5):
        role = "user" if i % 2 == 0 else "assistant"
        add_message(conn, user_id=user_id, role=role, content=f"Žinutė {i}")

    context = asyncio.run(build_context(
        conn=conn,
        user_id=user_id,
        system_prompt="Sistemos raginimas",
        current_query="Naujas klausimas",
    ))

    # Expected: 1 system + 5 verbatim + 1 current user query = 7
    assert len(context) == 7
    assert context[0] == {"role": "system", "content": "Sistemos raginimas"}
    assert context[1] == {"role": "user", "content": "Žinutė 0"}
    assert context[5] == {"role": "user", "content": "Žinutė 4"}
    assert context[6] == {"role": "user", "content": "Naujas klausimas"}


def test_history_exceeding_summarization_threshold(tmp_path: Path):
    db_file = tmp_path / "test.db"
    conn = get_connection(db_file)

    user_id = 100
    # Insert 45 messages (exceeds SUMMARIZE_THRESHOLD of 40)
    for i in range(45):
        role = "user" if i % 2 == 0 else "assistant"
        add_message(conn, user_id=user_id, role=role, content=f"Istorija {i:02d}")

    summarize_called = []

    def fake_summarize(msgs, existing_summary=None):
        summarize_called.append(len(msgs))
        return f"Sutraukta {len(msgs)} žinučių"

    context = asyncio.run(build_context(
        conn=conn,
        user_id=user_id,
        system_prompt="Sistemos raginimas",
        current_query="Dabartinis klausimas",
        summarize_fn=fake_summarize,
    ))

    # 45 total messages -> 25 summarized (45 - 20), 20 verbatim left
    assert len(summarize_called) == 1
    assert summarize_called[0] == 25

    summary_record = get_latest_summary(conn, user_id)
    assert summary_record is not None
    assert summary_record["content"] == "Sutraukta 25 žinučių"

    # In context: 1 system prompt, 1 summary, 20 verbatim messages, 1 query = 23 messages
    assert len(context) == 23
    assert context[0] == {"role": "system", "content": "Sistemos raginimas"}
    assert "Sutraukta 25 žinučių" in context[1]["content"]
    # Verbatim messages are the last 20 (index 25 to 44)
    assert context[2] == {"role": "assistant", "content": "Istorija 25"}
    assert context[-2] == {"role": "user", "content": "Istorija 44"}
    assert context[-1] == {"role": "user", "content": "Dabartinis klausimas"}


def test_token_budget_trimming(tmp_path: Path):
    db_file = tmp_path / "test.db"
    conn = get_connection(db_file)

    user_id = 100
    # Add 5 long messages
    for i in range(5):
        add_message(conn, user_id=user_id, role="user", content="X" * 400)  # 100 tokens each

    # System prompt: 20 tokens
    # Query: 20 tokens
    # Total without trimming = 20 + 5*100 + 20 = 540 tokens
    # If max_context_tokens is 250, it should trim older verbatim messages
    context = asyncio.run(build_context(
        conn=conn,
        user_id=user_id,
        system_prompt="S" * 80,  # 20 tokens
        current_query="Q" * 80,  # 20 tokens
        max_context_tokens=250,
    ))

    # 20 (system) + 20 (query) = 40 tokens.
    # Room for 2 verbatim messages (2 * 100 = 200), total = 240 <= 250.
    # Total messages in context should be 1 system + 2 verbatim + 1 query = 4
    assert len(context) == 4
    assert context[0]["content"] == "S" * 80
    assert context[-1]["content"] == "Q" * 80


def test_user_isolation(tmp_path: Path):
    db_file = tmp_path / "test.db"
    conn = get_connection(db_file)

    add_message(conn, user_id=1, role="user", content="User 1 secret")
    add_message(conn, user_id=2, role="user", content="User 2 secret")

    ctx1 = asyncio.run(build_context(conn, user_id=1, system_prompt="Sys"))
    contents1 = [m["content"] for m in ctx1]
    assert "User 1 secret" in contents1
    assert "User 2 secret" not in contents1

    ctx2 = asyncio.run(build_context(conn, user_id=2, system_prompt="Sys"))
    contents2 = [m["content"] for m in ctx2]
    assert "User 2 secret" in contents2
    assert "User 1 secret" not in contents2


def test_async_summarize_fn(tmp_path: Path):
    """The production summarizer calls the model, so it must be awaitable."""
    db_file = tmp_path / "test.db"
    conn = get_connection(db_file)

    user_id = 100
    for i in range(45):
        add_message(conn, user_id=user_id, role="user", content=f"Istorija {i:02d}")

    async def async_summarize(msgs, existing_summary=None):
        return f"Sutraukta {len(msgs)} žinučių"

    summary = asyncio.run(check_and_summarize(conn, user_id, async_summarize))

    assert summary == "Sutraukta 25 žinučių"
    record = get_latest_summary(conn, user_id)
    assert record is not None
    assert record["content"] == "Sutraukta 25 žinučių"


def test_existing_summary_is_passed_on_second_round(tmp_path: Path):
    """A second summarization must fold in the previous summary, not drop it."""
    db_file = tmp_path / "test.db"
    conn = get_connection(db_file)

    user_id = 100
    seen: list[str | None] = []

    async def async_summarize(msgs, existing_summary=None):
        seen.append(existing_summary)
        return f"Santrauka {len(seen)}"

    for i in range(45):
        add_message(conn, user_id=user_id, role="user", content=f"Pirma {i:02d}")
    asyncio.run(check_and_summarize(conn, user_id, async_summarize))

    for i in range(45):
        add_message(conn, user_id=user_id, role="user", content=f"Antra {i:02d}")
    asyncio.run(check_and_summarize(conn, user_id, async_summarize))

    assert seen == [None, "Santrauka 1"]
    assert get_latest_summary(conn, user_id)["content"] == "Santrauka 2"
