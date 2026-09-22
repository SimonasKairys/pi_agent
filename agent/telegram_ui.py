"""Telegram UI layer for pi_agent.

Manages message splitting, concurrent updates, user authorization,
per-user locks, and command/message handling.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import uuid
from typing import Any
from telegram import BotCommandScopeChat, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from agent.config import User, load_guests, load_users
from agent.context import add_message, build_context
from agent.db import (
    MAX_TOTAL_DAILY_COST_USD,
    MAX_USER_DAILY_COST_USD,
    MAX_USER_DAILY_EVENTS,
    check_daily_cost_limit,
    get_connection,
    get_current_day,
    get_total_daily_cost,
    get_user_daily_cost,
    get_user_daily_events,
    record_usage,
    LimitExceededError,
)
from agent.approvals import (
    create_pending_approval,
    enrich_arguments,
    execute_approved_action,
    expire_stale_approvals,
    format_approval_card,
    needs_approval,
    process_approval_action,
)
from agent.journal import record_journal_entry
from agent.llm import LLMClient
from agent.loop import run_loop
from agent.memory import extract_and_save_facts, get_user_facts
from agent.prompts import (
    APPROVAL_PENDING_MESSAGE,
    ERROR_MESSAGE,
    build_system_prompt,
    bot_commands,
)
from agent.i18n import format_usd, format_usd_precise, language_from_telegram
from agent.i18n import t as tr
from agent.tools.registry import ToolRegistry
from agent.tools.search import SEARCH_TOOL
from agent.tools.youtube import YOUTUBE_TOOL
from agent.tools.calendar import (
    CREATE_EVENT_TOOL,
    DELETE_EVENT_TOOL,
    LIST_EVENTS_TOOL,
    UPDATE_EVENT_TOOL,
    make_create_event_tool,
    make_delete_event_tool,
    make_list_events_tool,
    make_update_event_tool,
)
from agent.tools.facts import make_forget_fact_tool, make_list_facts_tool
from agent.tools.notes import make_note_tools
from agent.tools.reminders import make_reminder_tools
from agent.reminders import start_reminder_loop, stop_reminder_loop

logger = logging.getLogger(__name__)

# Telegram UI limits
SPLIT_LIMIT = 4000
CONCURRENT_UPDATES = 8

# Per-user locks to ensure sequential message processing per user
_user_locks: dict[int, asyncio.Lock] = {}


def get_user_lock(user_id: int) -> asyncio.Lock:
    """Returns the dedicated asyncio.Lock for a given user ID."""
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


def make_summarizer(llm_client: LLMClient, conn: Any, user_id: int, run_id: str | None = None):
    """Returns an async summarizer for context.build_context.

    The summarization call costs tokens, so it is recorded twice: against the
    daily budget in `usage`, and in the journal that 5.3 reads costs from.
    """
    async def summarize(messages: list[dict], existing_summary: str | None = None) -> str:
        response = await llm_client.summarize(messages, existing_summary=existing_summary)
        record_usage(conn, user_id=user_id, cost_usd=response.cost_usd)
        try:
            record_journal_entry(
                run_id=run_id,
                user_id=user_id,
                tool_name="summarize",
                tokens=response.total_tokens,
                cost_usd=response.cost_usd,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
            )
        except Exception as log_err:
            logger.warning("Klaida rašant sutraukimą į žurnalą: %s", log_err)
        return response.content

    return summarize


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


def _language_of(telegram_id: int | None) -> str:
    """Returns a user's language from users.toml, or Lithuanian if unknown."""
    user = get_authorized_user(telegram_id) if telegram_id is not None else None
    return user.language if user else "lt"


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


def _unauthorized_text(update: Update) -> str:
    """The refusal for people not in users.toml, in their Telegram app language."""
    code = getattr(update.effective_user, "language_code", None) if update.effective_user else None
    return tr(language_from_telegram(code), "unauthorized")


async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /pagalba and /help: a fixed list of what the bot can do, without a model call."""
    if update.effective_chat is None or update.effective_chat.type != "private":
        return
    if update.effective_user is None or update.message is None:
        return

    user = get_authorized_user(update.effective_user.id)
    if user is None:
        await update.message.reply_text(_unauthorized_text(update))
        return
    await update.message.reply_text(tr(user.language, "help"))


async def register_command_menus(bot: Any, users: list[User]) -> None:
    """Sets the Lithuanian menu as the default and each user's menu in their own chat.

    Telegram picks a language_code menu by the app's interface language, which
    often differs from the language in users.toml, so menus are set per chat.
    """
    await bot.set_my_commands(bot_commands("lt"))
    for user in users:
        if user.language == "lt":
            continue
        try:
            await bot.set_my_commands(
                bot_commands(user.language),
                scope=BotCommandScopeChat(chat_id=user.telegram_id),
            )
        except Exception:
            # A user who never opened the bot has no chat yet; the default menu applies.
            logger.warning("Nepavyko nustatyti komandų meniu vartotojui %d", user.telegram_id)


async def post_init(app: Application) -> None:
    """Registers the command menus and starts the reminder loop."""
    try:
        await register_command_menus(app.bot, load_users())
    except Exception:
        # The menu is a convenience; the bot must start even if Telegram refuses it.
        logger.exception("Nepavyko užregistruoti komandų meniu")
    await start_reminder_loop(app)


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /start command for authorized users in private chats."""
    if update.effective_chat is None or update.effective_chat.type != "private":
        return

    if update.effective_user is None:
        return

    user = get_authorized_user(update.effective_user.id)
    if user is None:
        if update.message:
            await update.message.reply_text(_unauthorized_text(update))
        return

    if update.message:
        await update.message.reply_text(tr(user.language, "start"))


def build_costs_report(conn: Any, user: User, day: str | None = None) -> str:
    """Returns today's spending and limits; admins also see every user's spending."""
    lang = user.language
    day = day or get_current_day()
    user_cost = get_user_daily_cost(conn, user.telegram_id, day=day)
    events = get_user_daily_events(conn, user.telegram_id, day=day)
    lines = [
        tr(lang, "costs_title", day=day),
        tr(lang, "costs_yours", spent=format_usd_precise(user_cost, lang),
           limit=format_usd(MAX_USER_DAILY_COST_USD, lang)),
        tr(lang, "costs_events", count=events, limit=MAX_USER_DAILY_EVENTS),
    ]
    if user.role == "admin":
        total = get_total_daily_cost(conn, day=day)
        lines.append(tr(lang, "costs_system", spent=format_usd_precise(total, lang),
                        limit=format_usd(MAX_TOTAL_DAILY_COST_USD, lang)))
        try:
            names = {u.telegram_id: u.name for u in load_users()}
        except Exception:
            logger.exception("Klaida nuskaitant vartotojų sąrašą išlaidų ataskaitai")
            names = {}
        rows = conn.execute(
            "SELECT user_id, cost_usd FROM usage WHERE day = ? ORDER BY cost_usd DESC",
            (day,),
        ).fetchall()
        for row in rows:
            name = names.get(row["user_id"], str(row["user_id"]))
            lines.append(f"  – {name}: {format_usd_precise(row['cost_usd'], lang)} USD")
    return "\n".join(lines)


async def handle_costs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /islaidos and /costs: shows today's spending against the daily limits."""
    if update.effective_chat is None or update.effective_chat.type != "private":
        return
    if update.effective_user is None or update.message is None:
        return

    user = get_authorized_user(update.effective_user.id)
    if user is None:
        await update.message.reply_text(_unauthorized_text(update))
        return

    conn = get_connection()
    try:
        report = build_costs_report(conn, user)
    finally:
        conn.close()
    await update.message.reply_text(report)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles regular text messages for authorized users."""
    if update.effective_chat is None or update.effective_chat.type != "private":
        return

    if update.effective_user is None or update.message is None or not update.message.text:
        return

    user = get_authorized_user(update.effective_user.id)
    if user is None:
        await update.message.reply_text(_unauthorized_text(update))
        return

    lock = get_user_lock(user.telegram_id)

    async with lock:
        conn = get_connection()
        try:
            await _answer(update, context, user, conn)
        finally:
            conn.close()


# Telegram shows "typing..." for about 5 s, so it is resent until the answer is ready.
TYPING_INTERVAL_SECONDS = 4.0


@contextlib.asynccontextmanager
async def keep_typing(chat: Any):
    """Shows the typing indicator for as long as the block runs."""

    async def pulse() -> None:
        while True:
            try:
                await chat.send_action(action="typing")
            except Exception:
                logger.debug("Nepavyko išsiųsti rašymo indikatoriaus", exc_info=True)
            await asyncio.sleep(TYPING_INTERVAL_SECONDS)

    task = asyncio.create_task(pulse())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def get_default_registry(
    conn: Any = None,
    user: User | None = None,
    composio_client: Any = None,
) -> ToolRegistry:
    """Builds the default tool registry populated with search, calendar, and user tools.

    search_youtube is optional: it is registered only when YOUTUBE_API_KEY is set.
    """
    registry = ToolRegistry()
    registry.register(SEARCH_TOOL)
    if os.environ.get("YOUTUBE_API_KEY"):
        registry.register(YOUTUBE_TOOL)
    if conn is not None and user is not None:
        registry.register(
            make_list_events_tool(
                conn=conn,
                user_id=user.telegram_id,
                timezone_str=user.timezone,
                composio_client=composio_client,
            )
        )
        registry.register(
            make_create_event_tool(
                conn=conn,
                user_id=user.telegram_id,
                timezone_str=user.timezone,
                composio_client=composio_client,
            )
        )
        registry.register(
            make_update_event_tool(
                conn=conn,
                user_id=user.telegram_id,
                timezone_str=user.timezone,
                composio_client=composio_client,
            )
        )
        registry.register(
            make_delete_event_tool(
                conn=conn,
                user_id=user.telegram_id,
                timezone_str=user.timezone,
                composio_client=composio_client,
            )
        )
        # Facts belong to one user, so these tools exist only with a bound user.
        registry.register(
            make_list_facts_tool(conn=conn, user_id=user.telegram_id, language=user.language)
        )
        registry.register(
            make_forget_fact_tool(conn=conn, user_id=user.telegram_id, language=user.language)
        )
        for tool in make_note_tools(conn=conn, user_id=user.telegram_id, language=user.language):
            registry.register(tool)
        for tool in make_reminder_tools(conn=conn, user=user):
            registry.register(tool)
    else:
        registry.register(LIST_EVENTS_TOOL)
        registry.register(CREATE_EVENT_TOOL)
        registry.register(UPDATE_EVENT_TOOL)
        registry.register(DELETE_EVENT_TOOL)
    return registry


def make_approval_hook(update: Update, conn: Any, user: User):
    """Returns a run_loop hook that turns a write action into a confirmation card.

    Returning a string tells the loop not to execute the tool.
    """
    async def hook(tool_name, arguments, risk, naudotas_internetas):
        if not needs_approval(
            tool_name, naudotas_internetas, risk, arguments=arguments, caller_name=user.name
        ):
            return None

        shown = enrich_arguments(
            conn, tool_name, arguments, user_id=user.telegram_id, timezone_name=user.timezone
        )
        approval_id = create_pending_approval(
            conn,
            user_id=user.telegram_id,
            tool_name=tool_name,
            arguments=arguments,
            chat_id=update.effective_chat.id if update.effective_chat else None,
        )
        text, keyboard = format_approval_card(approval_id, tool_name, shown, user.language)
        sent = await update.message.reply_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
        conn.execute(
            "UPDATE pending_approvals SET message_id = ? WHERE id = ?",
            (getattr(sent, "message_id", None), approval_id),
        )
        conn.commit()
        return APPROVAL_PENDING_MESSAGE

    return hook


async def handle_approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles Tvirtinti / Atmesti button presses."""
    query = update.callback_query
    if query is None or not query.data:
        return

    try:
        action, raw_id = query.data.split(":", 1)
        approval_id = int(raw_id)
    except (ValueError, AttributeError):
        await query.answer(ERROR_MESSAGE)
        return

    user = get_authorized_user(query.from_user.id) if query.from_user else None
    if user is None:
        code = getattr(query.from_user, "language_code", None) if query.from_user else None
        await query.answer(tr(language_from_telegram(code), "unauthorized"), show_alert=True)
        return

    conn = get_connection()
    try:
        success, message, info = process_approval_action(
            conn, approval_id, action, query.from_user.id, language=user.language
        )
        await query.answer(message, show_alert=not success)

        if success and action == "approve" and info is not None:
            # Tas pats registro parinkimas kaip _answer: kitaip patvirtintas
            # veiksmas vykdytų kitą įrankių rinkinį nei tas, kurį patvirtino.
            if context and hasattr(context, "bot_data") and "tool_registry" in context.bot_data:
                registry = context.bot_data["tool_registry"]
            else:
                registry = get_default_registry(
                    conn=conn,
                    user=user,
                    composio_client=context.bot_data.get("composio_client")
                    if context and hasattr(context, "bot_data")
                    else None,
                )
            try:
                result = await execute_approved_action(
                    info, registry, get_lock_fn=get_user_lock
                )
            except Exception:
                logger.exception("Klaida vykdant patvirtintą veiksmą %d", approval_id)
                result = tr(user.language, "error")
            for chunk in split_message(result):
                await query.message.reply_text(chunk)

        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            logger.debug("Nepavyko pašalinti kortelės mygtukų")
    finally:
        conn.close()


async def _answer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    conn: Any,
) -> None:
    """Answers one message for an authorized user, holding that user's lock."""
    user_id = user.telegram_id
    user_text = update.message.text

    try:
        check_daily_cost_limit(conn, user_id)
    except LimitExceededError as e:
        await update.message.reply_text(e.message(user.language))
        return

    # Record incoming user message
    add_message(conn, user_id=user_id, role="user", content=user_text)

    # Prepare context and prompt
    try:
        all_users = load_users()
    except Exception:
        all_users = [user]

    allowed_names = [u.name for u in all_users if u.telegram_id != user_id]
    try:
        allowed_names.extend(g.name for g in load_guests())
    except Exception:
        logger.exception("Klaida nuskaitant svečių sąrašą")
    user_facts = get_user_facts(conn, user_id)
    system_prompt = build_system_prompt(
        name=user.name,
        timezone_name=user.timezone,
        allowed_names=allowed_names,
        facts=[f["fact"] for f in user_facts],
        language=user.language,
    )
    llm_client: LLMClient
    if context and hasattr(context, "bot_data") and "llm_client" in context.bot_data:
        llm_client = context.bot_data["llm_client"]
    else:
        llm_client = LLMClient()

    for stale in expire_stale_approvals(conn):
        if stale.get("chat_id") and stale.get("message_id") and context is not None:
            try:
                await context.bot.edit_message_text(
                    chat_id=stale["chat_id"],
                    message_id=stale["message_id"],
                    text=tr(_language_of(stale.get("user_id")), "approval_expired"),
                )
            except Exception:
                logger.debug("Nepavyko atnaujinti pasenusios kortelės")

    run_id = f"run_{uuid.uuid4().hex[:12]}"

    # Older history is summarized here. A failed summary must not block the
    # answer, so the context is rebuilt without it.
    try:
        messages = await build_context(
            conn=conn,
            user_id=user_id,
            system_prompt=system_prompt,
            summarize_fn=make_summarizer(llm_client, conn, user_id, run_id),
        )
    except Exception:
        logger.exception("Nepavyko sutraukti vartotojo %d istorijos", user_id)
        messages = await build_context(
            conn=conn,
            user_id=user_id,
            system_prompt=system_prompt,
        )

    try:
        tool_registry: ToolRegistry
        if context and hasattr(context, "bot_data") and "tool_registry" in context.bot_data:
            tool_registry = context.bot_data["tool_registry"]
        else:
            composio_client = (
                context.bot_data.get("composio_client")
                if context and hasattr(context, "bot_data")
                else None
            )
            tool_registry = get_default_registry(
                conn=conn,
                user=user,
                composio_client=composio_client,
            )

        async with keep_typing(update.message.chat):
            loop_result = await run_loop(
                llm_client=llm_client,
                tool_registry=tool_registry,
                messages=messages,
                user_id=user_id,
                run_id=run_id,
                approval_hook=make_approval_hook(update, conn, user),
            )
        record_usage(conn, user_id=user_id, cost_usd=loop_result.total_cost_usd)
        add_message(
            conn,
            user_id=user_id,
            role="assistant",
            content=loop_result.content,
            tokens=loop_result.total_completion_tokens,
        )

        chunks = split_message(loop_result.content)
        for chunk in chunks:
            await update.message.reply_text(chunk)

        # Faktai įrašomi po atsakymo, kad papildomas modelio kvietimas
        # nevėlintų vartotojo ir kad jo klaida nenuslėptų jau gauto atsakymo.
        try:
            await extract_and_save_facts(
                conn,
                user_id=user_id,
                user_message=user_text,
                assistant_message=loop_result.content,
                llm_client=llm_client,
            )
        except Exception:
            logger.exception("Klaida įrašant vartotojo %d faktus", user_id)

    except Exception:
        logger.exception("Klaida apdorojant vartotojo %d užklausą", user_id)
        await update.message.reply_text(tr(user.language, "error"))


def create_application(
    token: str,
    llm_client: LLMClient | None = None,
    tool_registry: ToolRegistry | None = None,
    composio_client: Any = None,
) -> Application:
    """Creates and configures the Telegram Application."""
    app = (
        Application.builder()
        .token(token)
        .concurrent_updates(CONCURRENT_UPDATES)
        .post_init(post_init)
        .post_shutdown(stop_reminder_loop)
        .build()
    )
    if llm_client is not None:
        app.bot_data["llm_client"] = llm_client
    if tool_registry is not None:
        app.bot_data["tool_registry"] = tool_registry
    if composio_client is not None:
        app.bot_data["composio_client"] = composio_client

    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler(["islaidos", "costs"], handle_costs))
    app.add_handler(CommandHandler(["pagalba", "help"], handle_help))
    app.add_handler(
        CallbackQueryHandler(handle_approval_callback, pattern=r"^(approve|reject):\d+$")
    )
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            handle_message,
        )
    )
    return app
