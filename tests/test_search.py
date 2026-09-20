"""Tests for agent/tools/search.py."""

import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest

from agent.tools.search import (
    search_web,
    format_search_results,
    SEARCH_TOOL,
    MAX_SEARCH_RESULTS,
    MAX_SNIPPET_LENGTH,
    MAX_TOTAL_LENGTH,
)
from agent.tools.registry import UNTRUSTED_TAG_OPEN, UNTRUSTED_TAG_CLOSE


def test_search_tool_definition():
    assert SEARCH_TOOL.name == "search_web"
    assert SEARCH_TOOL.risk == "read_only"
    assert "query" in SEARCH_TOOL.parameters["properties"]
    assert SEARCH_TOOL.parameters["required"] == ["query"]


def test_format_results_count_limit():
    raw_results = [
        {"title": f"T{i}", "url": f"http://example.com/{i}", "content": f"C{i}"}
        for i in range(10)
    ]
    formatted = format_search_results(raw_results)

    # Must contain at most 5 results
    assert "T0" in formatted
    assert f"T{MAX_SEARCH_RESULTS - 1}" in formatted
    assert f"T{MAX_SEARCH_RESULTS}" not in formatted


def test_format_single_snippet_truncation():
    long_content = "X" * 800
    raw_results = [{"title": "Test", "url": "http://example.com", "content": long_content}]
    formatted = format_search_results(raw_results)

    assert "X" * MAX_SNIPPET_LENGTH in formatted
    assert "X" * (MAX_SNIPPET_LENGTH + 1) not in formatted
    assert "..." in formatted


def test_format_total_block_truncation():
    # 5 results, each with snippet of 500 characters plus headers -> ~3000+ chars
    raw_results = [
        {"title": f"Labai ilga antraste {i}", "url": f"http://example.com/very/long/url/{i}", "content": "A" * 500}
        for i in range(5)
    ]
    formatted = format_search_results(raw_results)

    # Length of formatted results must not exceed MAX_TOTAL_LENGTH + ellipsis
    assert len(formatted) <= MAX_TOTAL_LENGTH + 3


def test_search_web_with_mock_client():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "results": [
            {
                "title": "Vilniaus orai",
                "url": "https://orai.lt",
                "content": "Šiandien Vilniuje saulėta, 20 laipsnių.",
            }
        ]
    }
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp

    result = asyncio.run(search_web("orai Vilniuje", api_key="fake-key", client=mock_client))

    # Verify untrusted data tag is present
    assert UNTRUSTED_TAG_OPEN in result
    assert UNTRUSTED_TAG_CLOSE in result
    assert 'irankis="search_web"' in result
    assert "Vilniaus orai" in result
    assert "Šiandien Vilniuje saulėta" in result


def test_search_web_missing_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    result = asyncio.run(search_web("klausimas", api_key=None))

    assert UNTRUSTED_TAG_OPEN in result
    assert "trūksta TAVILY_API_KEY" in result
