"""Tests for golden trajectories.

All tests go through agent/telegram_ui.py handle_message with a deterministic
fake LLM client and mock Composio client, verifying end-to-end execution.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.config import User
from agent.db import get_connection
from agent.llm import LLMResponse
import agent.telegram_ui as ui
from agent.tools.calendar import (
    make_create_event_tool,
    make_delete_event_tool,
    make_list_events_tool,
    make_update_event_tool,
)
from agent.tools.registry import Tool, ToolRegistry

GOLDEN_DIR = Path(__file__).parent / "golden"
USERS = [
    User(111, "Simonas", "simonas@example.com", "Europe/Vilnius"),
    User(222, "Rūta", "ruta@example.com", "Europe/Vilnius"),
]


def load_golden_cases():
    cases = []
    for file_path in sorted(GOLDEN_DIR.glob("*.json")):
        with open(file_path, "r", encoding="utf-8") as f:
            cases.append(json.load(f))
    return cases


GOLDEN_CASES = load_golden_cases()


def make_fake_llm(plan: list[dict[str, Any]], calls_tracker: list[dict[str, Any]]):
    step = {"i": 0}

    async def generate(messages, tools=None, max_tokens=1500):
        # Ignore fact extraction prompts during golden trajectory execution
        if any(
            "Tu esi asistento atminties modulis" in (m.get("content") or "")
            for m in messages
            if isinstance(m, dict)
        ):
            return LLMResponse("[]", 10, 5, 15, 0.0)

        i = step["i"]
        step["i"] += 1
        if i < len(plan):
            item = plan[i]
            tool_calls = None
            if item.get("tool_calls"):
                tool_calls = []
                for idx, tc in enumerate(item["tool_calls"]):
                    calls_tracker.append({
                        "name": tc["name"],
                        "arguments": tc.get("arguments", {}),
                    })
                    tool_calls.append({
                        "id": f"call_{i}_{idx}",
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc.get("arguments", {})),
                        },
                    })
            return LLMResponse(
                item.get("content", ""),
                prompt_tokens=20,
                completion_tokens=10,
                total_tokens=30,
                cost_usd=0.0001,
                tool_calls=tool_calls,
            )
        return LLMResponse("Užbaigta.", 10, 5, 15, 0.0, tool_calls=None)

    client = MagicMock()
    client.generate = generate
    client.summarize = AsyncMock(return_value=LLMResponse("Santrauka", 5, 5, 10, 0.0))
    return client


def make_update(user_id: int, text: str):
    update = MagicMock()
    update.effective_chat.type = "private"
    update.effective_chat.id = 1000 + user_id
    update.effective_user.id = user_id
    update.message.text = text
    sent = MagicMock()
    sent.message_id = 42
    update.message.reply_text = AsyncMock(return_value=sent)
    update.message.chat.send_action = AsyncMock()
    return update


@pytest.mark.parametrize(
    "case",
    GOLDEN_CASES,
    ids=[c["id"] for c in GOLDEN_CASES],
)
def test_golden_trajectory(case: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "golden.db"
    conn = get_connection(db_file)

    monkeypatch.setattr(ui, "load_users", lambda: USERS)
    monkeypatch.setattr("agent.config.load_users", lambda *a, **k: USERS)
    monkeypatch.setattr(ui, "get_connection", lambda: get_connection(db_file))

    # Prepopulate initial events if present
    for ev in case.get("initial_events", []):
        conn.execute(
            "INSERT INTO events (id, user_id, google_event_id, title, starts_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, '2026-09-20T08:00:00')",
            (ev["id"], ev["user_id"], ev["google_event_id"], ev["title"], ev["starts_at"]),
        )
        for att in ev.get("attendees", []):
            conn.execute(
                "INSERT INTO event_attendees (event_id, user_id) VALUES (?, ?)",
                (ev["id"], att),
            )
    conn.commit()

    # Mock Composio client
    composio = MagicMock()

    def mock_composio_execute(slug, arguments, **kwargs):
        if slug == "GOOGLECALENDAR_EVENTS_LIST":
            items = []
            for ev in case.get("initial_events", []):
                items.append({
                    "id": ev["google_event_id"],
                    "summary": ev["title"],
                    "start": {"dateTime": ev["starts_at"]},
                    "end": {"dateTime": ev["starts_at"]},
                    "status": "confirmed",
                })
            return {"data": {"items": items}}
        if slug == "GOOGLECALENDAR_CREATE_EVENT":
            return {"data": {"id": f"g_created_{case['id']}"}}
        if slug in ("GOOGLECALENDAR_PATCH_EVENT", "GOOGLECALENDAR_DELETE_EVENT"):
            return {"data": {"status": "success"}}
        return {"data": {}}

    composio.tools.execute.side_effect = mock_composio_execute

    # Setup ToolRegistry
    registry = ToolRegistry()
    registry.register(Tool(
        "search_web",
        "paieška internete",
        {"type": "object", "properties": {"query": {"type": "string"}}},
        "read_only",
        lambda query="": f"Paieškos rezultatai temai: {query}",
    ))
    registry.register(make_list_events_tool(
        conn=conn, user_id=111, timezone_str="Europe/Vilnius", composio_client=composio
    ))
    registry.register(make_create_event_tool(
        conn=conn, user_id=111, timezone_str="Europe/Vilnius", composio_client=composio
    ))
    registry.register(make_update_event_tool(
        conn=conn, user_id=111, timezone_str="Europe/Vilnius", composio_client=composio
    ))
    registry.register(make_delete_event_tool(
        conn=conn, user_id=111, timezone_str="Europe/Vilnius", composio_client=composio
    ))

    called_tools: list[dict[str, Any]] = []
    fake_llm = make_fake_llm(case["llm_plan"], called_tools)

    context = MagicMock()
    context.bot_data = {
        "llm_client": fake_llm,
        "tool_registry": registry,
        "composio_client": composio,
    }
    context.bot.edit_message_text = AsyncMock()

    update = make_update(111, case["user_message"])

    # Execute through telegram_ui.handle_message
    asyncio.run(ui.handle_message(update, context))

    # 1. Verify expected tools called
    actual_tool_names = [call["name"] for call in called_tools]
    assert actual_tool_names == case["expected_tools"], (
        f"Laukti įrankiai {case['expected_tools']}, gauti {actual_tool_names}"
    )

    # 2. Verify approval behavior
    if case.get("expected_approval", False):
        approvals = conn.execute(
            "SELECT tool_name, status, user_id FROM pending_approvals"
        ).fetchall()
        assert len(approvals) == 1, "Tikėtasi vieno laukiančio patvirtinimo įrašo"
        assert approvals[0]["status"] == "pending"
        assert approvals[0]["user_id"] == 111

        # Check that confirmation card with inline keyboard was sent
        reply_calls = update.message.reply_text.call_args_list
        has_card = any(call.kwargs.get("reply_markup") is not None for call in reply_calls)
        assert has_card, "Tikėtasi patvirtinimo kortelės su mygtukais"

        # Check system state: if delete, event was not deleted
        if "delete_event" in case["expected_tools"]:
            row = conn.execute("SELECT deleted_at FROM events WHERE id=1").fetchone()
            assert row is not None and row["deleted_at"] is None

        # If create after search, event was not created
        if "create_event" in case["expected_tools"]:
            row = conn.execute(
                "SELECT COUNT(*) as c FROM events WHERE title='Konferencija'"
            ).fetchone()
            assert row["c"] == 0
    else:
        # If approval was not expected, no pending approvals should exist
        approvals_count = conn.execute(
            "SELECT COUNT(*) as c FROM pending_approvals"
        ).fetchone()["c"]
        assert approvals_count == 0, "Neturėjo būti sukurtas joks pending_approval"

        # If create_event was expected, verify event was created in DB
        if "create_event" in case["expected_tools"]:
            created_count = conn.execute(
                "SELECT COUNT(*) as c FROM events WHERE user_id=111"
            ).fetchone()["c"]
            assert created_count >= 1, "Įvykis turėjo būti sukurtas bazėje"

    # 3. Verify user message was stored
    stored_msgs = conn.execute(
        "SELECT role, content FROM messages WHERE user_id=111 ORDER BY id ASC"
    ).fetchall()
    assert len(stored_msgs) >= 2, "Turėjo būti išsaugota vartotojo žinutė ir asistento atsakymas"
    assert stored_msgs[0]["role"] == "user"
    assert stored_msgs[0]["content"] == case["user_message"]

    # 4. Verify bot replied
    assert update.message.reply_text.call_count >= 1

    conn.close()
