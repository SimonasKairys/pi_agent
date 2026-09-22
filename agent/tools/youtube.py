"""YouTube video search tool using the YouTube Data API v3 for pi_agent.

Limits:
- Results count: 5
- Single description length: 300 characters
- Total block length: 3000 characters
- Wrapped with untrusted data marker by ToolRegistry.execute()

Quota: search.list costs 100 of the default 10,000 daily units, about 100 searches a day.
"""

from __future__ import annotations

import html
import logging
import os
from typing import Any
import httpx

from agent.tools.registry import Tool

logger = logging.getLogger(__name__)

# Search limits
YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_WATCH_URL = "https://www.youtube.com/watch?v="
MAX_YOUTUBE_RESULTS = 5
MAX_DESCRIPTION_LENGTH = 300
MAX_TOTAL_LENGTH = 3000
YOUTUBE_TIMEOUT_SECONDS = 30.0


async def query_youtube(
    query: str,
    api_key: str,
    max_results: int = MAX_YOUTUBE_RESULTS,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Performs HTTP request to YouTube Data API and returns raw list of video items."""
    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": max_results,
    }
    # The key goes in a header, not in ?key=: an HTTP error message quotes the
    # request URL, and that message reaches the model and the run log.
    headers = {"X-Goog-Api-Key": api_key}

    if client is not None:
        resp = await client.get(
            YOUTUBE_API_URL, params=params, headers=headers, timeout=YOUTUBE_TIMEOUT_SECONDS
        )
        resp.raise_for_status()
        data = resp.json()
    else:
        async with httpx.AsyncClient(timeout=YOUTUBE_TIMEOUT_SECONDS) as c:
            resp = await c.get(YOUTUBE_API_URL, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()

    items = data.get("items", [])
    if not isinstance(items, list):
        return []
    return items


def format_youtube_results(items: list[dict[str, Any]]) -> str:
    """Formats and truncates YouTube results to configured limits."""
    parts: list[str] = []
    for item in items:
        if len(parts) >= MAX_YOUTUBE_RESULTS:
            break
        video_id = str((item.get("id") or {}).get("videoId", "")).strip()
        if not video_id:
            continue
        snippet = item.get("snippet") or {}
        # The API returns HTML-escaped text, for example &#39; instead of '.
        title = html.unescape(str(snippet.get("title", ""))).strip()
        channel = html.unescape(str(snippet.get("channelTitle", ""))).strip()
        published = str(snippet.get("publishedAt", ""))[:10]
        description = html.unescape(str(snippet.get("description", ""))).strip()

        if len(description) > MAX_DESCRIPTION_LENGTH:
            description = description[:MAX_DESCRIPTION_LENGTH].rstrip() + "..."

        parts.append(
            f"Pavadinimas: {title}\nKanalas: {channel}\nData: {published}\n"
            f"Nuoroda: {YOUTUBE_WATCH_URL}{video_id}\nAprašymas: {description}"
        )

    if not parts:
        return "YouTube vaizdo įrašų nerasta."

    full_text = "\n\n".join(parts)
    if len(full_text) > MAX_TOTAL_LENGTH:
        full_text = full_text[:MAX_TOTAL_LENGTH].rstrip() + "..."

    return full_text


async def search_youtube(
    query: str,
    api_key: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Searches YouTube videos and returns the formatted result block.

    Scrubbing and the untrusted-data marker are applied by ToolRegistry.execute().
    """
    key = api_key or os.environ.get("YOUTUBE_API_KEY", "")
    if not key:
        return "Klaida: trūksta YOUTUBE_API_KEY aplinkos kintamojo."

    try:
        items = await query_youtube(
            query=query,
            api_key=key,
            max_results=MAX_YOUTUBE_RESULTS,
            client=client,
        )
        return format_youtube_results(items)
    except httpx.HTTPStatusError as e:
        # 403 usually means the daily quota is used up or the key is restricted.
        logger.warning("YouTube API grąžino klaidą %s", e.response.status_code)
        return f"Klaida vykdant YouTube paiešką: HTTP {e.response.status_code}."
    except Exception as e:
        logger.exception("Klaida vykdant YouTube paiešką")
        return f"Klaida vykdant YouTube paiešką: {type(e).__name__}."


YOUTUBE_TOOL = Tool(
    name="search_youtube",
    description=(
        "ieško vaizdo įrašų YouTube ir grąžina pavadinimus, kanalus, datas ir nuorodas. "
        "Naudok, kai vartotojas prašo rasti vaizdo įrašą, pamoką, dainą ar kanalą."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Paieškos frazė",
            }
        },
        "required": ["query"],
    },
    risk="read_only",
    func=search_youtube,
)
