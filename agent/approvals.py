"""Approval layer for pi_agent.

Manages confirmation workflow for sensitive actions:
- update_event, delete_event, and forget_fact always require confirmation
- write operations require confirmation if web search was performed (naudotas_internetas is True)
- pending approvals expire after 15 minutes
- atomic transitions and strict sender checking prevent unauthorized or duplicate executions
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from agent.reminders import local_time
from agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Approval settings
APPROVAL_EXPIRY_MINUTES = 15
# Deleting a fact, note, or reminder cannot be undone, so it is confirmed like an event change.
ALWAYS_APPROVE_TOOLS = (
    "update_event",
    "delete_event",
    "forget_fact",
    "delete_note",
    "delete_reminder",
)


def needs_approval(
    tool_name: str,
    naudotas_internetas: bool = False,
    risk: str | None = None,
    arguments: dict[str, Any] | None = None,
    caller_name: str | None = None,
) -> bool:
    """Determines whether a tool call requires user confirmation.

    Rules:
    1. update_event, delete_event, forget_fact, delete_note, and delete_reminder
       always require approval, and so does create_reminder for another user.
    2. Any write tool (risk='destructive' or create/update/delete) requires
       approval if web search was performed during the execution.
    3. create_event with users from users.toml and without search runs automatically.
    4. Read-only tools never require approval.
    """
    if tool_name in ALWAYS_APPROVE_TOOLS:
        return True

    if tool_name == "create_reminder" and arguments:
        for_name = str(arguments.get("for_name") or "").strip()
        if for_name and (caller_name is None or for_name.casefold() != caller_name.casefold()):
            return True

    is_write = (risk == "destructive") or (tool_name in ("create_event", "update_event", "delete_event"))

    if is_write and naudotas_internetas:
        return True

    return False


def create_pending_approval(
    conn: sqlite3.Connection,
    user_id: int,
    tool_name: str,
    arguments: dict[str, Any],
    chat_id: int | None = None,
    message_id: int | None = None,
    expiry_minutes: int = APPROVAL_EXPIRY_MINUTES,
) -> int:
    """Inserts a pending approval record into SQLite database."""
    now_utc = datetime.now(timezone.utc)
    expires_at = now_utc + timedelta(minutes=expiry_minutes)
    expires_at_str = expires_at.isoformat()
    args_json = json.dumps(arguments, ensure_ascii=False)

    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO pending_approvals (
            user_id, tool_name, arguments, chat_id, message_id, expires_at, status
        ) VALUES (?, ?, ?, ?, ?, ?, 'pending')
        """,
        (user_id, tool_name, args_json, chat_id, message_id, expires_at_str),
    )
    conn.commit()
    return int(cursor.lastrowid)


def _card_time(value: Any) -> str:
    """Formats an ISO datetime as 'YYYY-MM-DD HH:MM'; returns other values unchanged."""
    text = str(value)
    if "T" not in text:
        return text
    try:
        return datetime.fromisoformat(text).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return text


def _card_text(value: Any) -> str:
    """Removes Markdown control characters so stored text cannot break the card."""
    return re.sub(r"[*_`\[\]]", "", str(value))


def format_approval_card(
    approval_id: int,
    tool_name: str,
    arguments: dict[str, Any],
) -> tuple[str, InlineKeyboardMarkup]:
    """Builds confirmation message text and inline keyboard."""
    action_names = {
        "update_event": "Įvykio keitimas",
        "delete_event": "Įvykio atšaukimas",
        "create_event": "Naujo įvykio sukūrimas",
        "forget_fact": "Fakto pamiršimas",
        "create_reminder": "Priminimas kitam vartotojui",
        "delete_reminder": "Priminimo atšaukimas",
        "delete_note": "Užrašo trynimas",
    }
    action_label = action_names.get(tool_name, tool_name)

    lines = [
        "⚠️ **Reikalingas patvirtinimas veiksmui atlikti**\n",
        f"• **Veiksmas**: {action_label}",
    ]

    if "title" in arguments:
        lines.append(f"• **Pavadinimas**: {arguments['title']}")
    elif "event_id" in arguments:
        lines.append(f"• **Įvykio numeris**: {arguments['event_id']}")

    if "fact" in arguments:
        lines.append(f"• **Faktas**: {_card_text(arguments['fact'])}")
    elif "fact_id" in arguments:
        lines.append(f"• **Fakto numeris**: {arguments['fact_id']}")

    if "for_name" in arguments:
        lines.append(f"• **Kam**: {_card_text(arguments['for_name'])}")
    if "when" in arguments:
        lines.append(f"• **Kada**: {_card_time(arguments['when'])}")
    if "text" in arguments:
        lines.append(f"• **Tekstas**: {_card_text(arguments['text'])}")
    elif "note_id" in arguments:
        lines.append(f"• **Užrašo numeris**: {arguments['note_id']}")
    elif "reminder_id" in arguments:
        lines.append(f"• **Priminimo numeris**: {arguments['reminder_id']}")

    if "start" in arguments:
        end_str = f" - {_card_time(arguments['end'])}" if "end" in arguments else ""
        lines.append(f"• **Laikas**: {_card_time(arguments['start'])}{end_str}")

    if "attendees" in arguments and isinstance(arguments["attendees"], list):
        count = len(arguments["attendees"])
        names = ", ".join(arguments["attendees"]) if count > 0 else "nėra"
        lines.append(f"• **Dalyvių skaičius**: {count} ({names})")

    if "attendee_count" in arguments and not any("Dalyvi" in ln for ln in lines):
        lines.append(f"• Dalyvių: {arguments['attendee_count']}")
    lines.append(f"\n_Patvirtinimas galioja {APPROVAL_EXPIRY_MINUTES} min._")
    text = "\n".join(lines)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Tvirtinti", callback_data=f"approve:{approval_id}"),
            InlineKeyboardButton("Atmesti", callback_data=f"reject:{approval_id}"),
        ]
    ])

    return text, keyboard


def process_approval_action(
    conn: sqlite3.Connection,
    approval_id: int,
    action: str,
    clicking_user_id: int,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Processes approval button click atomically.

    Verifies user identity, checks expiration, and transitions status.
    Returns (success, message, approval_info_dict).
    """
    if action not in ("approve", "reject"):
        raise ValueError(f"Nežinomas veiksmas: {action}")

    cursor = conn.cursor()
    cursor.execute("SELECT * FROM pending_approvals WHERE id = ?", (approval_id,))
    row = cursor.fetchone()

    if row is None:
        return False, "Patvirtinimo užklausa nerasta.", None

    # Strict sender verification
    if row["user_id"] != clicking_user_id:
        logger.warning(
            "Vartotojas %d bandė patvirtinti vartotojo %d užklausą #%d",
            clicking_user_id,
            row["user_id"],
            approval_id,
        )
        return False, "Neturite teisės tvirtinti šio veiksmo.", None

    # Check expiration
    exp_str = row["expires_at"]
    try:
        exp_dt = datetime.fromisoformat(exp_str)
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
    except Exception:
        exp_dt = datetime.now(timezone.utc) - timedelta(seconds=1)

    now_utc = datetime.now(timezone.utc)
    if now_utc > exp_dt:
        if row["status"] == "pending":
            conn.execute(
                "UPDATE pending_approvals SET status = 'expired' WHERE id = ? AND status = 'pending'",
                (approval_id,),
            )
            conn.commit()
        return False, "Patvirtinimo galiojimo laikas pasibaigęs.", None

    # Check status
    if row["status"] != "pending":
        status_labels = {
            "approved": "jau patvirtintas",
            "rejected": "jau atmestas",
            "expired": "pasenęs",
        }
        lbl = status_labels.get(row["status"], row["status"])
        return False, f"Veiksmas {lbl}.", None

    new_status = "approved" if action == "approve" else "rejected"

    # Atomic status change: exactly one concurrent click can succeed
    cur = conn.execute(
        "UPDATE pending_approvals SET status = ? WHERE id = ? AND status = 'pending'",
        (new_status, approval_id),
    )
    conn.commit()

    if cur.rowcount != 1:
        return False, "Veiksmas jau buvo apdorotas.", None

    if new_status == "rejected":
        return True, "Veiksmas atmestas.", None

    try:
        arguments = json.loads(row["arguments"])
    except Exception:
        arguments = {}

    approval_info = {
        "id": row["id"],
        "user_id": row["user_id"],
        "tool_name": row["tool_name"],
        "arguments": arguments,
        "chat_id": row["chat_id"],
        "message_id": row["message_id"],
    }
    return True, "Veiksmas patvirtintas.", approval_info


async def execute_approved_action(
    approval_info: dict[str, Any],
    tool_registry: ToolRegistry,
    get_lock_fn: Callable[[int], asyncio.Lock],
) -> str:
    """Executes confirmed tool holding the user lock.

    get_lock_fn is required: an approved action runs under the same
    per-user lock as an ordinary request, so running without one must be
    impossible rather than merely discouraged.
    """
    user_id = approval_info["user_id"]
    tool_name = approval_info["tool_name"]
    arguments = approval_info["arguments"]

    lock = get_lock_fn(user_id)
    async with lock:
        # The result goes to the user, not the model: no untrusted-data envelope.
        return await tool_registry.execute(tool_name, arguments, wrap=False)


def enrich_arguments(
    conn: sqlite3.Connection,
    tool_name: str,
    arguments: dict[str, Any],
    user_id: int | None = None,
    timezone_name: str = "Europe/Vilnius",
) -> dict[str, Any]:
    """Fills in time and attendee count for the approval card.

    update_event and delete_event arguments carry only event_id, so the card
    must read the stored event instead of trusting what the model passed.
    forget_fact gets the fact text, read only from the requesting user's facts;
    delete_note and delete_reminder likewise read only the user's own rows.
    """
    if tool_name == "delete_note":
        note_id = arguments.get("note_id")
        if note_id is None or user_id is None:
            return arguments
        row = conn.execute(
            "SELECT text FROM notes WHERE id = ? AND user_id = ?", (note_id, user_id)
        ).fetchone()
        return {**arguments, "text": row["text"]} if row is not None else arguments

    if tool_name == "delete_reminder":
        reminder_id = arguments.get("reminder_id")
        if reminder_id is None or user_id is None:
            return arguments
        row = conn.execute(
            "SELECT text, due_at FROM reminders WHERE id = ? AND sent_at IS NULL "
            "AND (created_by = ? OR user_id = ?)",
            (reminder_id, user_id, user_id),
        ).fetchone()
        if row is None:
            return arguments
        return {**arguments, "text": row["text"], "when": local_time(row["due_at"], timezone_name)}
    if tool_name == "forget_fact":
        fact_id = arguments.get("fact_id")
        if fact_id is None or user_id is None:
            return arguments
        row = conn.execute(
            "SELECT fact FROM facts WHERE id = ? AND user_id = ?",
            (fact_id, user_id),
        ).fetchone()
        return {**arguments, "fact": row["fact"]} if row is not None else arguments

    if tool_name not in ("update_event", "delete_event"):
        return arguments

    event_id = arguments.get("event_id")
    if event_id is None:
        return arguments

    enriched = dict(arguments)
    row = conn.execute(
        "SELECT title, starts_at FROM events WHERE id = ? AND deleted_at IS NULL",
        (event_id,),
    ).fetchone()
    if row is not None:
        enriched.setdefault("title", row["title"])
        enriched.setdefault("start", row["starts_at"])

    # Users and guests are both emailed by Google, so both belong in the count.
    count = conn.execute(
        "SELECT (SELECT COUNT(*) FROM event_attendees WHERE event_id = ?) "
        "+ (SELECT COUNT(*) FROM event_guests WHERE event_id = ?) AS c",
        (event_id, event_id),
    ).fetchone()
    enriched["attendee_count"] = int(count["c"]) if count else 0
    return enriched


def expire_stale_approvals(
    conn: sqlite3.Connection,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Marks timed-out approvals as expired and returns them.

    Without this, a card nobody clicks stays 'pending' forever: the lazy check
    inside process_approval_action only runs when somebody presses a button.
    """
    now_utc = now or datetime.now(timezone.utc)
    rows = conn.execute(
        "SELECT id, user_id, chat_id, message_id, tool_name, expires_at "
        "FROM pending_approvals WHERE status = 'pending'"
    ).fetchall()

    expired: list[dict[str, Any]] = []
    for row in rows:
        try:
            exp = datetime.fromisoformat(row["expires_at"])
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            exp = now_utc - timedelta(seconds=1)
        if now_utc > exp:
            cur = conn.execute(
                "UPDATE pending_approvals SET status = 'expired' "
                "WHERE id = ? AND status = 'pending'",
                (row["id"],),
            )
            if cur.rowcount == 1:
                expired.append(dict(row))
    if expired:
        conn.commit()
    return expired
