"""Reminder tools for pi_agent.

- create_reminder: schedules a one-time reminder for the user or another bot user
  (a reminder for someone else always needs user approval)
- list_reminders: shows pending reminders the user created or will receive
- delete_reminder: cancels one reminder by number (always needs user approval)
Delivery lives in agent/reminders.py.
"""

from __future__ import annotations

import sqlite3
import zoneinfo
from datetime import datetime, timedelta, timezone
from functools import partial
from typing import Callable

from agent.config import User, load_users
from agent.reminders import local_time
from agent.tools.registry import Tool

MAX_REMINDER_LENGTH = 1000
MAX_PENDING_REMINDERS = 50
MAX_REMINDER_DAYS_AHEAD = 365


def _resolve_recipient(
    user_id: int,
    for_name: str | None,
    users_loader: Callable[[], list[User]],
) -> tuple[User | None, str | None]:
    """Returns the recipient, or an error message. Guests cannot get reminders."""
    users = users_loader()
    if not for_name or not str(for_name).strip():
        return next((u for u in users if u.telegram_id == user_id), None), None
    wanted = str(for_name).strip().casefold()
    for u in users:
        if u.name.casefold() == wanted:
            return u, None
    names = ", ".join(u.name for u in users)
    return None, f"Klaida: vartotojo „{for_name}“ nėra. Priminti galima tik boto vartotojams: {names}."


def create_reminder(
    conn: sqlite3.Connection,
    user_id: int,
    timezone_str: str,
    when: str,
    text: str,
    for_name: str | None = None,
    now: datetime | None = None,
    users_loader: Callable[[], list[User]] = load_users,
) -> str:
    """Schedules a one-time reminder at a local time in the creator's time zone."""
    clean = str(text).strip()
    if not clean:
        return "Klaida: priminimo tekstas tuščias."
    if len(clean) > MAX_REMINDER_LENGTH:
        return f"Klaida: priminimas per ilgas (daugiausia {MAX_REMINDER_LENGTH} simbolių)."

    try:
        tz = zoneinfo.ZoneInfo(timezone_str)
        due = datetime.fromisoformat(str(when).strip())
    except (ValueError, zoneinfo.ZoneInfoNotFoundError):
        return "Klaida: laiką nurodyk taip: 2026-09-22T09:00."
    if due.tzinfo is None:
        due = due.replace(tzinfo=tz)
    due_utc = due.astimezone(timezone.utc).replace(second=0, microsecond=0)

    now = now or datetime.now(timezone.utc)
    if due_utc < now - timedelta(minutes=1):
        return "Klaida: šis laikas jau praėjo."
    if due_utc > now + timedelta(days=MAX_REMINDER_DAYS_AHEAD):
        return f"Klaida: priminti galima ne vėliau kaip po {MAX_REMINDER_DAYS_AHEAD} dienų."

    recipient, error = _resolve_recipient(user_id, for_name, users_loader)
    if error:
        return error
    recipient_id = recipient.telegram_id if recipient else user_id

    pending = conn.execute(
        "SELECT COUNT(*) AS c FROM reminders WHERE created_by = ? AND sent_at IS NULL AND failed_at IS NULL",
        (user_id,),
    ).fetchone()["c"]
    if pending >= MAX_PENDING_REMINDERS:
        return f"Klaida: pasiekta laukiančių priminimų riba ({MAX_PENDING_REMINDERS})."

    cursor = conn.execute(
        "INSERT INTO reminders (user_id, created_by, text, due_at, created_at) VALUES (?, ?, ?, ?, ?)",
        (recipient_id, user_id, clean, due_utc.isoformat(), now.isoformat()),
    )
    conn.commit()
    shown = local_time(due_utc.isoformat(), timezone_str)
    if recipient_id != user_id:
        return f"Priminimas {cursor.lastrowid} vartotojui {recipient.name} sukurtas: {shown} – {clean}"
    return f"Priminimas {cursor.lastrowid} sukurtas: {shown} – {clean}"


def list_reminders(
    conn: sqlite3.Connection,
    user_id: int,
    timezone_str: str,
    users_loader: Callable[[], list[User]] = load_users,
) -> str:
    """Returns pending reminders the user created or will receive."""
    rows = conn.execute(
        """
        SELECT id, user_id, created_by, text, due_at FROM reminders
        WHERE sent_at IS NULL AND failed_at IS NULL AND (created_by = ? OR user_id = ?)
        ORDER BY due_at
        """,
        (user_id, user_id),
    ).fetchall()
    if not rows:
        return "Laukiančių priminimų nėra."
    try:
        names = {u.telegram_id: u.name for u in users_loader()}
    except Exception:
        names = {}
    lines = []
    for row in rows:
        line = f"{row['id']}. {local_time(row['due_at'], timezone_str)} – {row['text']}"
        if row["user_id"] != user_id:
            line += f" (vartotojui {names.get(row['user_id'], row['user_id'])})"
        elif row["created_by"] != user_id:
            line += f" (nuo {names.get(row['created_by'], row['created_by'])})"
        lines.append(line)
    return "\n".join(lines)


def delete_reminder(conn: sqlite3.Connection, user_id: int, reminder_id: int) -> str:
    """Cancels a pending reminder that the user created or would receive."""
    cursor = conn.execute(
        """
        DELETE FROM reminders
        WHERE id = ? AND sent_at IS NULL AND (created_by = ? OR user_id = ?)
        """,
        (int(reminder_id), user_id, user_id),
    )
    conn.commit()
    if cursor.rowcount:
        return f"Priminimas {reminder_id} atšauktas."
    return f"Priminimo {reminder_id} nerasta."


CREATE_REMINDER_TOOL = Tool(
    name="create_reminder",
    description=(
        "sukuria vienkartinį priminimą: nurodytu laiku botas atsiųs žinutę Telegram. "
        "Naudok, kai vartotojas prašo ką nors priminti. Kitam vartotojui priminti galima "
        "nurodžius for_name. Nenaudok faktams apie vartotoją ir užrašams."
    ),
    parameters={
        "type": "object",
        "properties": {
            "when": {
                "type": "string",
                "description": "Vietinis laikas be zonos, pavyzdžiui 2026-09-22T09:00",
            },
            "text": {"type": "string", "description": "Ką priminti"},
            "for_name": {
                "type": "string",
                "description": "Kitam vartotojui: jo vardas. Sau: nenurodyk",
            },
        },
        "required": ["when", "text"],
    },
    risk="destructive",
    func=create_reminder,
)

LIST_REMINDERS_TOOL = Tool(
    name="list_reminders",
    description="grąžina laukiančius priminimus su jų numeriais: sukurtus vartotojo ir skirtus jam.",
    parameters={"type": "object", "properties": {}, "required": []},
    risk="read_only",
    func=list_reminders,
)

DELETE_REMINDER_TOOL = Tool(
    name="delete_reminder",
    description=(
        "atšaukia priminimą pagal numerį iš list_reminders. "
        "Naudok tik kai vartotojas aiškiai prašo atšaukti priminimą."
    ),
    parameters={
        "type": "object",
        "properties": {
            "reminder_id": {"type": "integer", "description": "Atšaukiamo priminimo numeris"},
        },
        "required": ["reminder_id"],
    },
    risk="destructive",
    func=delete_reminder,
)


def make_reminder_tools(conn: sqlite3.Connection, user: User) -> list[Tool]:
    """Creates user-bound reminder tool instances for registration."""
    bound = {
        CREATE_REMINDER_TOOL.name: partial(
            create_reminder, conn=conn, user_id=user.telegram_id, timezone_str=user.timezone
        ),
        LIST_REMINDERS_TOOL.name: partial(
            list_reminders, conn=conn, user_id=user.telegram_id, timezone_str=user.timezone
        ),
        DELETE_REMINDER_TOOL.name: partial(delete_reminder, conn=conn, user_id=user.telegram_id),
    }
    return [
        Tool(
            name=tool.name,
            description=tool.description,
            parameters=tool.parameters,
            risk=tool.risk,
            func=bound[tool.name],
        )
        for tool in (CREATE_REMINDER_TOOL, LIST_REMINDERS_TOOL, DELETE_REMINDER_TOOL)
    ]
