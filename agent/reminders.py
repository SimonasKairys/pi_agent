"""One-time reminders for pi_agent: storage helpers and the delivery loop.

Reminders are stored in UTC and sent by a background task inside the bot
process. The task checks every REMINDER_CHECK_SECONDS; a reminder that fell due
while the bot was down (for example, before the encrypted disk was unlocked) is
sent on the next check with a note that it is late.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import zoneinfo
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from telegram.error import Forbidden

from agent.config import User, load_users
from agent.db import get_connection

logger = logging.getLogger(__name__)

REMINDER_CHECK_SECONDS = 30
# A reminder sent later than this after its time says it is late.
LATE_AFTER = timedelta(minutes=5)


def local_time(value_utc: str, timezone_name: str) -> str:
    """Formats a stored UTC timestamp as 'YYYY-MM-DD HH:MM' in the given time zone."""
    try:
        tz = zoneinfo.ZoneInfo(timezone_name)
    except Exception:
        tz = zoneinfo.ZoneInfo("Europe/Vilnius")
    return datetime.fromisoformat(value_utc).astimezone(tz).strftime("%Y-%m-%d %H:%M")


def _reminder_message(row: sqlite3.Row, users: dict[int, User], now: datetime) -> str:
    """Builds the reminder text; a reminder from someone else names the sender."""
    recipient = users.get(row["user_id"])
    creator = users.get(row["created_by"])
    if row["created_by"] != row["user_id"]:
        sender = creator.name if creator else str(row["created_by"])
        text = f"⏰ Priminimas nuo {sender}: {row['text']}"
    else:
        text = f"⏰ Priminimas: {row['text']}"
    due = datetime.fromisoformat(row["due_at"])
    if now - due > LATE_AFTER:
        tz_name = recipient.timezone if recipient else "Europe/Vilnius"
        text += f"\n(Vėluoja: turėjo būti {local_time(row['due_at'], tz_name)})"
    return text


async def deliver_due_reminders(
    conn: sqlite3.Connection,
    bot: Any,
    now: datetime | None = None,
    users_loader: Callable[[], list[User]] = load_users,
) -> int:
    """Sends every due reminder once and returns how many were sent.

    sent_at is written only after Telegram accepts the message, so a crash
    re-sends rather than drops. A recipient who never opened the bot makes
    Telegram answer Forbidden; that reminder is marked failed, and the creator
    is told, so it is not retried forever.
    """
    now = now or datetime.now(timezone.utc)
    rows = conn.execute(
        """
        SELECT id, user_id, created_by, text, due_at FROM reminders
        WHERE sent_at IS NULL AND failed_at IS NULL AND due_at <= ?
        ORDER BY due_at
        """,
        (now.isoformat(),),
    ).fetchall()
    if not rows:
        return 0

    try:
        users = {u.telegram_id: u for u in users_loader()}
    except Exception:
        logger.exception("Klaida nuskaitant vartotojų sąrašą priminimams")
        users = {}

    sent = 0
    for row in rows:
        try:
            await bot.send_message(chat_id=row["user_id"], text=_reminder_message(row, users, now))
        except Forbidden:
            logger.warning("Priminimo %d gavėjas %d nepasiekiamas", row["id"], row["user_id"])
            conn.execute(
                "UPDATE reminders SET failed_at = ? WHERE id = ?", (now.isoformat(), row["id"])
            )
            conn.commit()
            if row["created_by"] != row["user_id"]:
                recipient = users.get(row["user_id"])
                name = recipient.name if recipient else str(row["user_id"])
                try:
                    await bot.send_message(
                        chat_id=row["created_by"],
                        text=(
                            f"Priminimo {name} išsiųsti nepavyko: šis vartotojas dar nėra "
                            f"pradėjęs pokalbio su botu (/start). Priminimas: {row['text']}"
                        ),
                    )
                except Exception:
                    logger.exception("Nepavyko pranešti kūrėjui apie priminimą %d", row["id"])
            continue
        except Exception:
            # Network trouble: the reminder stays unsent and is retried on the next check.
            logger.exception("Klaida siunčiant priminimą %d", row["id"])
            continue

        conn.execute(
            "UPDATE reminders SET sent_at = ? WHERE id = ?", (now.isoformat(), row["id"])
        )
        conn.commit()
        sent += 1
    return sent


async def reminder_loop(bot: Any) -> None:
    """Checks for due reminders until cancelled."""
    while True:
        try:
            conn = get_connection()
            try:
                await deliver_due_reminders(conn, bot)
            finally:
                conn.close()
        except Exception:
            logger.exception("Klaida priminimų cikle")
        await asyncio.sleep(REMINDER_CHECK_SECONDS)


async def start_reminder_loop(app: Any) -> None:
    """Application post_init hook: starts the delivery loop."""
    app.bot_data["reminder_task"] = asyncio.create_task(reminder_loop(app.bot))


async def stop_reminder_loop(app: Any) -> None:
    """Application post_shutdown hook: stops the delivery loop."""
    task = app.bot_data.pop("reminder_task", None)
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
