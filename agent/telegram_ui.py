"""Telegram UI layer for pi_agent.

Manages message splitting, concurrent updates, user authorization,
per-user locks, and command/message handling.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from agent.config import User, load_users
from agent.context import add_message, build_context
from agent.db import (
    check_daily_cost_limit,
    get_connection,
    record_usage,
    LimitExceededError,
)
from agent.llm import LLMClient
from agent.prompts import (
    BUSY_MESSAGE,
    ERROR_MESSAGE,
    START_MESSAGE,
    UNAUTHORIZED_MESSAGE,
    build_system_prompt,
)

logger = logging.getLogger(__name__)

# Constants from TASK.md "Sprendimai ir skaičiai"
SPLIT_LIMIT = 4000
CONCURRENT_UPDATES = 8

# Per-user locks to ensure sequential message processing per user
_user_locks: dict[int, asyncio.Lock] = {}


def get_user_lock(user_id: int) -> asyncio.Lock:
    """Returns the dedicated asyncio.Lock for a given user ID."""
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


def split_message(text: str, limit: int = SPLIT_LIMIT) -> list[str]:
    """Splits a long message into chunks under limit characters.

    Attempts to split on paragraph breaks, then newlines, then spaces,
    falling back to hard slicing if necessary.
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        # Prefer paragraph break
        split_idx = remaining.rfind("\n\n", 0, limit)
        if split_idx == -1 or split_idx < limit // 2:
            # Try single newline
            split_idx = remaining.rfind("\n", 0, limit)
        if split_idx == -1 or split_idx < limit // 2:
            # Try space
            split_idx = remaining.rfind(" ", 0, limit)
        if split_idx == -1 or split_idx < limit // 4:
            # Hard slice fallback
            split_idx = limit

        chunk = remaining[:split_idx].rstrip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_idx:].lstrip()

    if remaining:
        chunks.append(remaining)

    return chunks


def get_authorized_user(telegram_id: int) -> User | None:
    """Finds and returns a User by telegram_id from users.toml, or None."""
    try:
        users = load_users()
        for u in users:
            if u.telegram_id == telegram_id:
                return u
    except Exception:
        logger.exception("Klaida nuskaitant vartotojų sąrašą")
    return None


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /start command for authorized users in private chats."""
    if update.effective_chat is None or update.effective_chat.type != "private":
        return

    if update.effective_user is None:
        return

    user = get_authorized_user(update.effective_user.id)
    if user is None:
        if update.message:
            await update.message.reply_text(UNAUTHORIZED_MESSAGE)
        return

    if update.message:
        await update.message.reply_text(START_MESSAGE)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles regular text messages for authorized users."""
    if update.effective_chat is None or update.effective_chat.type != "private":
        return

    if update.effective_user is None or update.message is None or not update.message.text:
        return

    user = get_authorized_user(update.effective_user.id)
    if user is None:
        await update.message.reply_text(UNAUTHORIZED_MESSAGE)
        return

    user_id = user.telegram_id
    user_text = update.message.text
    lock = get_user_lock(user_id)

    async with lock:
        conn = get_connection()
        try:
            check_daily_cost_limit(conn, user_id)
        except LimitExceededError as e:
            await update.message.reply_text(str(e))
            return

        # Record incoming user message
        add_message(conn, user_id=user_id, role="user", content=user_text)

        # Prepare context and prompt
        try:
            all_users = load_users()
        except Exception:
            all_users = [user]

        allowed_names = [u.name for u in all_users if u.telegram_id != user_id]
        system_prompt = build_system_prompt(
            name=user.name,
            timezone_name=user.timezone,
            allowed_names=allowed_names,
        )
        messages = build_context(
            conn=conn,
            user_id=user_id,
            system_prompt=system_prompt,
        )

        llm_client: LLMClient
        if context and hasattr(context, "bot_data") and "llm_client" in context.bot_data:
            llm_client = context.bot_data["llm_client"]
        else:
            llm_client = LLMClient()

        await update.message.chat.send_action(action="typing")

        try:
            llm_response = await llm_client.generate(messages=messages)
            record_usage(conn, user_id=user_id, cost_usd=llm_response.cost_usd)
            add_message(
                conn,
                user_id=user_id,
                role="assistant",
                content=llm_response.content,
                tokens=llm_response.completion_tokens,
            )

            chunks = split_message(llm_response.content)
            for chunk in chunks:
                await update.message.reply_text(chunk)

        except Exception:
            logger.exception("Klaida apdorojant vartotojo %d užklausą", user_id)
            await update.message.reply_text(ERROR_MESSAGE)


def create_application(
    token: str,
    llm_client: LLMClient | None = None,
) -> Application:
    """Creates and configures the Telegram Application."""
    app = Application.builder().token(token).concurrent_updates(CONCURRENT_UPDATES).build()
    if llm_client is not None:
        app.bot_data["llm_client"] = llm_client

    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            handle_message,
        )
    )
    return app
