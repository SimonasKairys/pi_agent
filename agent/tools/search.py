"""Web search tool using Tavily API for pi_agent.

Adheres to limits from TASK.md "Sprendimai ir skaičiai":
- Results count: 5
- Single snippet length: 500 characters
- Total block length: 3000 characters
- Wrapped with untrusted data marker
"""

from __future__ import annotations

import logging
import os
from typing import Any
import httpx

from agent.tools.registry import Tool

logger = logging.getLogger(__name__)

# Constants from TASK.md "Sprendimai ir skaičiai" (Paieška)
TAVILY_API_URL = "https://api.tavily.com/search"
MAX_SEARCH_RESULTS = 5
MAX_SNIPPET_LENGTH = 500
MAX_TOTAL_LENGTH = 3000
SEARCH_TIMEOUT_SECONDS = 30.0


async def query_tavily(
    query: str,
    api_key: str,
    max_results: int = MAX_SEARCH_RESULTS,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Performs HTTP request to Tavily API and returns raw list of result objects."""
    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": max_results,
    }

    if client is not None:
        resp = await client.post(TAVILY_API_URL, json=payload, timeout=SEARCH_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
    else:
        async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT_SECONDS) as c:
            resp = await c.post(TAVILY_API_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()

    results = data.get("results", [])
    if not isinstance(results, list):
        return []
    return results


def format_search_results(results: list[dict[str, Any]]) -> str:
    """Formats and truncates search results to configured limits."""
    if not results:
        return "Paieškos rezultatų nerasta."

    parts: list[str] = []
    for item in results[:MAX_SEARCH_RESULTS]:
        title = str(item.get("title", "")).strip()
        url = str(item.get("url", "")).strip()
        snippet = str(item.get("content", "")).strip()

        if len(snippet) > MAX_SNIPPET_LENGTH:
            snippet = snippet[:MAX_SNIPPET_LENGTH].rstrip() + "..."

        parts.append(f"Antraštė: {title}\nNuoroda: {url}\nIštrauka: {snippet}")

    full_text = "\n\n".join(parts)
    if len(full_text) > MAX_TOTAL_LENGTH:
        full_text = full_text[:MAX_TOTAL_LENGTH].rstrip() + "..."

    return full_text


async def search_web(
    query: str,
    api_key: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Searches the web and returns the formatted result block.

    Scrubbing and the untrusted-data marker are applied by ToolRegistry.execute().
    """
    key = api_key or os.environ.get("TAVILY_API_KEY", "")
    if not key:
        return "Klaida: trūksta TAVILY_API_KEY aplinkos kintamojo."

    try:
        raw_results = await query_tavily(
            query=query,
            api_key=key,
            max_results=MAX_SEARCH_RESULTS,
            client=client,
        )
        formatted = format_search_results(raw_results)
    except Exception as e:
        logger.exception("Klaida vykdant interneto paiešką per Tavily")
        formatted = f"Klaida vykdant paiešką: {e}"

    # ToolRegistry.execute() wraps and scrubs every result, so wrapping here too
    # would give the model two nested untrusted-data markers.
    return formatted


SEARCH_TOOL = Tool(
    name="search_web",
    description=(
        "ieško informacijos internete. Naudok, kai reikia šviežių arba tau nežinomų faktų. "
        "Nenaudok datų skaičiavimams ar klausimams apie vartotojo kalendorių."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Paieškos frazė ar klausimas",
            }
        },
        "required": ["query"],
    },
    risk="read_only",
    func=search_web,
)
