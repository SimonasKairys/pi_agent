"""Tests for agent/tools/youtube.py."""

import asyncio
from dataclasses import replace
from functools import partial
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import agent.telegram_ui as telegram_ui
from agent.loop import INTERNET_TOOLS
from agent.tools.registry import UNTRUSTED_TAG_CLOSE, UNTRUSTED_TAG_OPEN, ToolRegistry
from agent.tools.youtube import (
    MAX_DESCRIPTION_LENGTH,
    MAX_TOTAL_LENGTH,
    MAX_YOUTUBE_RESULTS,
    YOUTUBE_TOOL,
    format_youtube_results,
    search_youtube,
)


def _item(i: int, description: str = "D") -> dict:
    return {
        "id": {"kind": "youtube#video", "videoId": f"vid{i:08d}"},
        "snippet": {
            "title": f"T{i}",
            "channelTitle": f"Kanalas {i}",
            "publishedAt": "2026-05-01T12:00:00Z",
            "description": description,
        },
    }


def _client(items: list[dict]) -> AsyncMock:
    resp = MagicMock()
    resp.json.return_value = {"items": items}
    resp.raise_for_status = MagicMock()
    client = AsyncMock()
    client.get.return_value = resp
    return client


def test_youtube_tool_definition():
    assert YOUTUBE_TOOL.name == "search_youtube"
    assert YOUTUBE_TOOL.risk == "read_only"
    # api_key and client stay out of the schema, so the registry drops them.
    assert YOUTUBE_TOOL.allowed_argument_names() == {"query"}
    assert YOUTUBE_TOOL.parameters["required"] == ["query"]


def test_youtube_results_mark_internet_use():
    assert "search_youtube" in INTERNET_TOOLS


def test_format_results_count_limit():
    formatted = format_youtube_results([_item(i) for i in range(10)])

    assert "T0" in formatted
    assert f"T{MAX_YOUTUBE_RESULTS - 1}" in formatted
    assert f"T{MAX_YOUTUBE_RESULTS}" not in formatted


def test_format_skips_items_without_video_id():
    channel = {"id": {"kind": "youtube#channel", "channelId": "c1"}, "snippet": {"title": "Kanalas"}}
    assert format_youtube_results([channel]) == "YouTube vaizdo įrašų nerasta."
    assert format_youtube_results([]) == "YouTube vaizdo įrašų nerasta."


def test_format_fields_and_unescape():
    item = _item(1)
    item["snippet"]["title"] = "Rock &amp; Roll: it&#39;s here"
    formatted = format_youtube_results([item])

    assert "Pavadinimas: Rock & Roll: it's here" in formatted
    assert "Kanalas: Kanalas 1" in formatted
    assert "Data: 2026-05-01" in formatted
    assert "Nuoroda: https://www.youtube.com/watch?v=vid00000001" in formatted


def test_format_description_and_total_truncation():
    formatted = format_youtube_results([_item(i, "X" * 800) for i in range(5)])

    assert "X" * MAX_DESCRIPTION_LENGTH in formatted
    assert "X" * (MAX_DESCRIPTION_LENGTH + 1) not in formatted
    assert len(formatted) <= MAX_TOTAL_LENGTH + 3


def test_search_youtube_sends_key_in_header():
    client = _client([_item(1)])

    asyncio.run(search_youtube("dviračio padanga", api_key="fake-key", client=client))

    kwargs = client.get.call_args.kwargs
    assert kwargs["headers"] == {"X-Goog-Api-Key": "fake-key"}
    assert "key" not in kwargs["params"]
    assert kwargs["params"]["q"] == "dviračio padanga"
    assert kwargs["params"]["type"] == "video"


def test_search_youtube_through_registry_wraps_once_and_keeps_link():
    registry = ToolRegistry()
    registry.register(replace(
        YOUTUBE_TOOL,
        func=partial(search_youtube, api_key="fake-key", client=_client([_item(1)])),
    ))
    wrapped = asyncio.run(registry.execute("search_youtube", {"query": "x"}))

    assert wrapped.count(UNTRUSTED_TAG_OPEN) == 1
    assert wrapped.count(UNTRUSTED_TAG_CLOSE) == 1
    assert 'irankis="search_youtube"' in wrapped
    # The scrubber must not treat the video link as a Google calendar ID.
    assert "https://www.youtube.com/watch?v=vid00000001" in wrapped


def test_search_youtube_http_error_hides_key():
    request = httpx.Request("GET", "https://www.googleapis.com/youtube/v3/search")
    response = httpx.Response(403, request=request)
    resp = MagicMock()
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "403 Forbidden", request=request, response=response
    )
    client = AsyncMock()
    client.get.return_value = resp

    result = asyncio.run(search_youtube("x", api_key="secret-key-123", client=client))

    assert result == "Klaida vykdant YouTube paiešką: HTTP 403."
    assert "secret-key-123" not in result


def test_search_youtube_missing_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)

    result = asyncio.run(search_youtube("x", api_key=None))

    assert "trūksta YOUTUBE_API_KEY" in result


def test_registry_includes_youtube_only_with_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    assert telegram_ui.get_default_registry().get("search_youtube") is None

    monkeypatch.setenv("YOUTUBE_API_KEY", "fake-key")
    assert telegram_ui.get_default_registry().get("search_youtube") is not None
