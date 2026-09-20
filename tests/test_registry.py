"""Tests for agent/tools/registry.py."""

import asyncio
from agent.tools.registry import (
    Tool,
    ToolRegistry,
    clean_tool_result,
    UNTRUSTED_TAG_OPEN,
    UNTRUSTED_TAG_CLOSE,
)


def test_clean_tool_result_removes_email():
    raw = "Vartotojo el. paštas yra simonas@example.com arba test.user+label@domain.co.uk."
    cleaned = clean_tool_result(raw, tool_name="search_web")

    # The character '@' must not be present in the output
    assert "@" not in cleaned
    assert "simonas" not in cleaned
    assert "[el. paštas pašalintas]" in cleaned


def test_clean_tool_result_removes_google_ids():
    raw = (
        "Rastas įvykis: google_event_id: 70abc123def456ghi789jkl0123456\n"
        "Nuoroda: https://google.com/calendar/event?eid=abc123xyz\n"
        "Gid: aaaaaaaaaaaaaaaaaaaaaaaaaa"
    )
    cleaned = clean_tool_result(raw, tool_name="list_events")

    assert "70abc123def456ghi789jkl0123456" not in cleaned
    assert "https://google.com/calendar/event" not in cleaned
    assert "[kalendoriaus_nuoroda]" in cleaned or "[paslėpta]" in cleaned or "[google_id]" in cleaned


def test_untrusted_data_tag_added_for_each_tool():
    registry = ToolRegistry()

    def dummy_read():
        return "Rezultatas iš paieškos"

    async def dummy_write():
        return "Įvykis sukurtas"

    registry.register(Tool(
        name="search_web",
        description="Paieška",
        parameters={"type": "object", "properties": {}},
        risk="read_only",
        func=dummy_read,
    ))
    registry.register(Tool(
        name="create_event",
        description="Kurti įvykį",
        parameters={"type": "object", "properties": {}},
        risk="destructive",
        func=dummy_write,
    ))

    # Test tool 1 execution
    res1 = asyncio.run(registry.execute("search_web", {}))
    assert UNTRUSTED_TAG_OPEN in res1
    assert UNTRUSTED_TAG_CLOSE in res1
    assert 'irankis="search_web"' in res1
    assert "Rezultatas iš paieškos" in res1

    # Test tool 2 execution
    res2 = asyncio.run(registry.execute("create_event", {}))
    assert UNTRUSTED_TAG_OPEN in res2
    assert UNTRUSTED_TAG_CLOSE in res2
    assert 'irankis="create_event"' in res2
    assert "Įvykis sukurtas" in res2


def test_registry_schemas():
    registry = ToolRegistry()
    registry.register(Tool(
        name="test_tool",
        description="Aprašas testui",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        risk="read_only",
        func=lambda query: query,
    ))

    schemas = registry.get_schemas()
    assert len(schemas) == 1
    s = schemas[0]
    assert s["type"] == "function"
    assert s["function"]["name"] == "test_tool"
    assert s["function"]["description"] == "Aprašas testui"
    assert s["function"]["parameters"]["required"] == ["query"]


def test_unknown_tool_execution():
    registry = ToolRegistry()
    res = asyncio.run(registry.execute("non_existent", {}))
    assert UNTRUSTED_TAG_OPEN in res
    assert "Klaida: įrankis 'non_existent' neegzistuoja." in res
