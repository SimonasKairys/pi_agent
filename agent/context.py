"""Context management module for pi_agent.

Assembles messages for LLM context, enforces token budgets,
and manages conversation summarization.
"""

from __future__ import annotations

import inspect
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable

# Context window limits
VERBATIM_MESSAGES_COUNT = 20
SUMMARIZE_THRESHOLD = 40
MAX_CONTEXT_TOKENS = 32_000
MAX_RESPONSE_TOKENS = 1_500

logger = logging.getLogger(__name__)


def estimate_tokens(text: str) -> int:
    """Estimates tokens as len(text) // 4."""
    return len(text) // 4


def add_message(
    conn: sqlite3.Connection,
    user_id: int,
    role: str,
    content: str,
    tokens: int | None = None,
) -> int:
    """Adds a message to the messages table and returns the new message ID."""
    if tokens is None:
        tokens = estimate_tokens(content)
    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO messages (user_id, role, content, tokens, created_at) VALUES (?, ?, ?, ?, ?);",
        (user_id, role, content, tokens, now),
    )
    conn.commit()
    return cursor.lastrowid  # type: ignore[return-value]


def get_latest_summary(conn: sqlite3.Connection, user_id: int) -> dict[str, Any] | None:
    """Retrieves the latest summary for the user, if one exists."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, user_id, content, covers_until_msg_id, created_at "
        "FROM summaries WHERE user_id = ? ORDER BY id DESC LIMIT 1;",
        (user_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return dict(row)


async def check_and_summarize(
    conn: sqlite3.Connection,
    user_id: int,
    summarize_fn: Callable[..., Any],
) -> str | None:
    """Checks if un-summarized messages exceed SUMMARIZE_THRESHOLD (40).

    If so, summarizes older messages beyond the last VERBATIM_MESSAGES_COUNT (20)
    and stores a new entry in the summaries table.

    summarize_fn may be synchronous or asynchronous: the production summarizer
    calls the model, so an awaitable result is awaited here.
    """
    latest_summary = get_latest_summary(conn, user_id)
    last_covered_id = latest_summary["covers_until_msg_id"] if latest_summary else 0

    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, role, content FROM messages "
        "WHERE user_id = ? AND id > ? ORDER BY id ASC;",
        (user_id, last_covered_id),
    )
    uncovered = [dict(row) for row in cursor.fetchall()]

    if len(uncovered) <= SUMMARIZE_THRESHOLD:
        return None

    # Leave the last VERBATIM_MESSAGES_COUNT messages intact
    messages_to_summarize = uncovered[:-VERBATIM_MESSAGES_COUNT]
    cutoff_msg_id = messages_to_summarize[-1]["id"]

    existing_summary_text = latest_summary["content"] if latest_summary else None
    try:
        result = summarize_fn(messages_to_summarize, existing_summary=existing_summary_text)
    except TypeError:
        result = summarize_fn(messages_to_summarize)

    if inspect.isawaitable(result):
        result = await result
    new_summary = str(result).strip()

    # An empty summary must not advance covers_until_msg_id: those messages
    # would then be covered by nothing and never summarized again.
    if not new_summary:
        logger.warning("Vartotojo %d sutraukimas grąžino tuščią santrauką", user_id)
        return None

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO summaries (user_id, content, covers_until_msg_id, created_at) VALUES (?, ?, ?, ?);",
        (user_id, new_summary, cutoff_msg_id, now),
    )
    conn.commit()
    return new_summary


async def build_context(
    conn: sqlite3.Connection,
    user_id: int,
    system_prompt: str,
    current_query: str | None = None,
    summarize_fn: Callable[..., Any] | None = None,
    max_context_tokens: int = MAX_CONTEXT_TOKENS,
) -> list[dict[str, str]]:
    """Builds the messages list for the model within context token limits.

    Structure:
    1. System prompt
    2. Summary (if available)
    3. Last verbatim messages (up to 20)
    4. Current query (if provided)
    """
    if summarize_fn is not None:
        await check_and_summarize(conn, user_id, summarize_fn)

    latest_summary = get_latest_summary(conn, user_id)
    last_covered_id = latest_summary["covers_until_msg_id"] if latest_summary else 0

    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, role, content FROM messages "
        "WHERE user_id = ? AND id > ? ORDER BY id DESC LIMIT ?;",
        (user_id, last_covered_id, VERBATIM_MESSAGES_COUNT),
    )
    verbatim_rows = cursor.fetchall()
    # Reverse to chronological order (ASC)
    verbatim_msgs = [
        {"role": row["role"], "content": row["content"]}
        for row in reversed(verbatim_rows)
    ]

    # Assemble base messages
    system_messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    if latest_summary is not None:
        system_messages.append({
            "role": "system",
            "content": f"Ankstesnių pokalbių santrauka:\n{latest_summary['content']}",
        })

    query_messages: list[dict[str, str]] = []
    if current_query is not None:
        query_messages.append({"role": "user", "content": current_query})

    # Token budgeting and trimming older verbatim messages if necessary
    def total_tokens(msgs: list[dict[str, str]]) -> int:
        return sum(estimate_tokens(m["content"]) for m in msgs)

    # If all combined exceed max_context_tokens, trim verbatim messages from front
    while verbatim_msgs and (
        total_tokens(system_messages) + total_tokens(verbatim_msgs) + total_tokens(query_messages)
        > max_context_tokens
    ):
        verbatim_msgs.pop(0)

    return system_messages + verbatim_msgs + query_messages
