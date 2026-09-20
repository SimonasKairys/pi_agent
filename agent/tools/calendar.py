"""Calendar tools module for pi_agent using Composio.

Provides calendar interaction tools:
- list_events: retrieves and filters user events for a time window (Step 3.3)
"""

from __future__ import annotations

import logging
import os
import sqlite3
import zoneinfo
from datetime import datetime
from functools import partial
from typing import Any

from agent.config import get_user_by_id
from agent.db import get_connection
from agent.tools.registry import Tool

logger = logging.getLogger(__name__)

# Constants from TASK.md "Sprendimai ir skaičiai" (Kalendorius)
MAX_LIST_EVENTS = 50
MAX_INTERVAL_DAYS = 90
MAX_DESCRIPTION_LENGTH = 2000
MAX_USER_DAILY_EVENTS = 20


def parse_date_input(
    val: str,
    timezone_name: str = "Europe/Vilnius",
    is_end_of_day: bool = False,
) -> datetime:
    """Parses date or datetime string and attaches the specified timezone."""
    tz = zoneinfo.ZoneInfo(timezone_name)
    val = val.strip()

    # Plain date YYYY-MM-DD
    if len(val) == 10 and val.count("-") == 2 and "T" not in val:
        try:
            d = datetime.strptime(val, "%Y-%m-%d").date()
        except ValueError as e:
            raise ValueError(f"Neteisingas datos formatas '{val}': {e}") from e
        if is_end_of_day:
            return datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=tz)
        return datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=tz)

    try:
        dt = datetime.fromisoformat(val)
    except ValueError as e:
        raise ValueError(f"Neteisingas datos formatas '{val}': {e}") from e

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    else:
        dt = dt.astimezone(tz)
    return dt


def _format_event_time(
    start_info: dict[str, Any] | None,
    end_info: dict[str, Any] | None,
    tz_name: str = "Europe/Vilnius",
    default_start: str = "",
) -> str:
    """Formats start and end time into human-readable local time string."""
    tz = zoneinfo.ZoneInfo(tz_name)
    start_info = start_info or {}
    end_info = end_info or {}

    start_dt_str = start_info.get("dateTime")
    start_date_str = start_info.get("date")
    end_dt_str = end_info.get("dateTime")
    end_date_str = end_info.get("date")

    if start_dt_str:
        try:
            dt_start = datetime.fromisoformat(start_dt_str).astimezone(tz)
            start_fmt = dt_start.strftime("%Y-%m-%d %H:%M")
        except Exception:
            start_fmt = start_dt_str

        if end_dt_str:
            try:
                dt_end = datetime.fromisoformat(end_dt_str).astimezone(tz)
                if dt_start.date() == dt_end.date():
                    end_fmt = dt_end.strftime("%H:%M")
                else:
                    end_fmt = dt_end.strftime("%Y-%m-%d %H:%M")
                return f"{start_fmt} - {end_fmt}"
            except Exception:
                return f"{start_fmt} - {end_dt_str}"
        return start_fmt

    if start_date_str:
        if end_date_str and end_date_str != start_date_str:
            return f"{start_date_str} - {end_date_str}"
        return start_date_str

    return default_start


def _extract_items(resp: Any) -> list[dict[str, Any]]:
    """Extracts calendar items list from Composio tool response."""
    if isinstance(resp, dict):
        if "items" in resp and isinstance(resp["items"], list):
            return resp["items"]
        data = resp.get("data")
        if isinstance(data, dict):
            if "items" in data and isinstance(data["items"], list):
                return data["items"]
            resp_data = data.get("response_data")
            if isinstance(resp_data, dict) and "items" in resp_data and isinstance(resp_data["items"], list):
                return resp_data["items"]
        elif isinstance(data, list):
            return data
    return []


def list_events(
    date_from: str,
    date_to: str,
    conn: sqlite3.Connection | None = None,
    user_id: int | None = None,
    composio_client: Any = None,
    timezone_str: str = "Europe/Vilnius",
    connected_account_id: str | None = None,
) -> str:
    """Lists calendar events for the user in the specified date range.

    Filters results so users see only events they created or are invited to.
    Creator sees attendee names; invited attendees see only title and time.
    Returns local event IDs, never google_event_id.
    """
    if user_id is None:
        raise ValueError("Trūksta vartotojo identifikatoriaus (user_id)")

    dt_from = parse_date_input(date_from, timezone_name=timezone_str, is_end_of_day=False)
    dt_to = parse_date_input(date_to, timezone_name=timezone_str, is_end_of_day=True)

    if dt_to < dt_from:
        raise ValueError("Pabaigos data negali būti ankstesnė už pradžios datą")

    delta = dt_to - dt_from
    if delta.total_seconds() > MAX_INTERVAL_DAYS * 86400:
        raise ValueError(f"Laiko intervalas negali būti ilgesnis nei {MAX_INTERVAL_DAYS} dienų")

    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    try:
        client = composio_client
        if client is None:
            api_key = os.environ.get("COMPOSIO_API_KEY")
            if not api_key:
                raise ValueError("Trūksta COMPOSIO_API_KEY aplinkos kintamojo")
            from composio import Composio
            client = Composio(api_key=api_key)

        conn_id = connected_account_id or os.environ.get("COMPOSIO_CONNECTED_ACCOUNT_ID")

        acc_user_id = None
        if hasattr(client, "connected_accounts") and conn_id:
            try:
                acc = client.connected_accounts.get(conn_id)
                acc_user_id = getattr(acc, "user_id", None)
            except Exception as e:
                logger.warning("Nepavyko gauti Composio acc.user_id: %s", e)

        time_min_str = dt_from.isoformat()
        time_max_str = dt_to.isoformat()

        exec_kwargs: dict[str, Any] = {
            "slug": "GOOGLECALENDAR_EVENTS_LIST",
            "arguments": {
                "calendarId": "primary",
                "timeMin": time_min_str,
                "timeMax": time_max_str,
                "maxResults": MAX_LIST_EVENTS,
                "singleEvents": True,
                "orderBy": "startTime",
            },
            "dangerously_skip_version_check": True,
        }
        if conn_id:
            exec_kwargs["connected_account_id"] = conn_id
        if acc_user_id:
            exec_kwargs["user_id"] = acc_user_id

        resp = client.tools.execute(**exec_kwargs)

        if isinstance(resp, dict) and not resp.get("successful", True):
            err_msg = resp.get("error") or resp.get("message") or "Nežinoma klaida"
            raise RuntimeError(f"Klaida kreipiantis į kalendorių: {err_msg}")

        items = _extract_items(resp)
        if not items:
            return "Nurodytu laikotarpiu įvykių nerasta."

        matching_events: list[str] = []

        for item in items:
            g_id = item.get("id")
            if not g_id:
                continue

            row = conn.execute(
                "SELECT id, user_id, title, starts_at, deleted_at FROM events WHERE google_event_id = ? AND deleted_at IS NULL",
                (g_id,),
            ).fetchone()

            if row is None:
                continue

            local_event_id = row["id"]
            creator_id = row["user_id"]

            is_creator = (creator_id == user_id)
            is_attendee = False

            if not is_creator:
                att_row = conn.execute(
                    "SELECT 1 FROM event_attendees WHERE event_id = ? AND user_id = ?",
                    (local_event_id, user_id),
                ).fetchone()
                if att_row is not None:
                    is_attendee = True

            if not is_creator and not is_attendee:
                continue

            event_title = row["title"]
            time_display = _format_event_time(
                item.get("start"),
                item.get("end"),
                tz_name=timezone_str,
                default_start=row["starts_at"],
            )

            if is_creator:
                att_rows = conn.execute(
                    "SELECT user_id FROM event_attendees WHERE event_id = ? ORDER BY user_id",
                    (local_event_id,),
                ).fetchall()
                attendee_names: list[str] = []
                for ar in att_rows:
                    att_uid = ar["user_id"]
                    try:
                        u = get_user_by_id(att_uid)
                        attendee_names.append(u.name)
                    except Exception:
                        pass
                if attendee_names:
                    names_str = ", ".join(attendee_names)
                    matching_events.append(f"[{local_event_id}] {event_title}: {time_display} (dalyviai: {names_str})")
                else:
                    matching_events.append(f"[{local_event_id}] {event_title}: {time_display}")
            else:
                matching_events.append(f"[{local_event_id}] {event_title}: {time_display}")

            if len(matching_events) >= MAX_LIST_EVENTS:
                break

        if not matching_events:
            return "Nurodytu laikotarpiu įvykių nerasta."

        return "\n".join(matching_events)

    finally:
        if close_conn:
            conn.close()


LIST_EVENTS_TOOL = Tool(
    name="list_events",
    description=(
        "grąžina vartotojo įvykius nurodytu laikotarpiu su jų numeriais. "
        "Naudok prieš keisdamas ar trindamas įvykį, kad gautum numerį. "
        "Nenaudok, kai vartotojas prašo tik sukurti naują įvykį."
    ),
    parameters={
        "type": "object",
        "properties": {
            "date_from": {
                "type": "string",
                "description": "Laikotarpio pradžia (pvz. 2026-09-20 arba 2026-09-20T00:00)",
            },
            "date_to": {
                "type": "string",
                "description": "Laikotarpio pabaiga (pvz. 2026-09-27 arba 2026-09-27T23:59)",
            },
        },
        "required": ["date_from", "date_to"],
    },
    risk="read_only",
    func=list_events,
)


def make_list_events_tool(
    conn: sqlite3.Connection,
    user_id: int,
    timezone_str: str = "Europe/Vilnius",
    composio_client: Any = None,
    connected_account_id: str | None = None,
) -> Tool:
    """Creates a user-bound list_events tool instance for registration."""
    return Tool(
        name=LIST_EVENTS_TOOL.name,
        description=LIST_EVENTS_TOOL.description,
        parameters=LIST_EVENTS_TOOL.parameters,
        risk=LIST_EVENTS_TOOL.risk,
        func=partial(
            list_events,
            conn=conn,
            user_id=user_id,
            timezone_str=timezone_str,
            composio_client=composio_client,
            connected_account_id=connected_account_id,
        ),
    )
