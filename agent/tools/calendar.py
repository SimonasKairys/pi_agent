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

from agent.config import get_user_by_id, get_user_by_name
from agent.db import check_daily_event_limit, get_connection, record_usage
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


def create_event(
    title: str,
    start: str,
    end: str,
    attendees: list[str],
    description: str = "",
    conn: sqlite3.Connection | None = None,
    user_id: int | None = None,
    composio_client: Any = None,
    timezone_str: str = "Europe/Vilnius",
    connected_account_id: str | None = None,
) -> str:
    """Creates a calendar event and sends invitations to authorized attendees.

    Requires attendees to be recognized users in users.toml.
    Enforces per-user daily event creation limits.
    Sets send_updates='all', guests_can_invite_others=False,
    guests_can_see_other_guests=False, and create_meeting_room=False.
    Records event in events and event_attendees tables, and increments usage.
    """
    if user_id is None:
        raise ValueError("Trūksta vartotojo identifikatoriaus (user_id)")

    title = title.strip()
    if not title:
        raise ValueError("Įvykio pavadinimas negali būti tuščias")

    if not isinstance(attendees, list):
        if isinstance(attendees, str):
            attendees = [attendees]
        else:
            attendees = list(attendees)

    # Validate attendees: each must exist in users.toml
    attendee_emails: list[str] = []
    attendee_user_ids: list[int] = []
    for att_name in attendees:
        clean_name = str(att_name).strip()
        if not clean_name:
            continue
        try:
            u = get_user_by_name(clean_name)
        except Exception as e:
            raise ValueError(
                f"Dalyvis '{clean_name}' neleidžiamas: galima kviesti tik registruotus vartotojus"
            ) from e
        attendee_emails.append(u.email)
        attendee_user_ids.append(u.telegram_id)

    dt_start = parse_date_input(start, timezone_name=timezone_str, is_end_of_day=False)
    dt_end = parse_date_input(end, timezone_name=timezone_str, is_end_of_day=False)

    if dt_end <= dt_start:
        raise ValueError("Pabaigos laikas negali būti ankstesnis arba lygus pradžios laikui")

    if len(description) > MAX_DESCRIPTION_LENGTH:
        description = description[:MAX_DESCRIPTION_LENGTH]

    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    try:
        # Enforce daily event limit
        check_daily_event_limit(conn, user_id)

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

        arguments = {
            "calendar_id": "primary",
            "calendarId": "primary",
            "summary": title,
            "start_datetime": dt_start.isoformat(),
            "end_datetime": dt_end.isoformat(),
            "timezone": timezone_str,
            "attendees": attendee_emails,
            "description": description,
            "send_updates": "all",
            "sendUpdates": "all",
            "guests_can_invite_others": False,
            "guestsCanInviteOthers": False,
            "guests_can_see_other_guests": False,
            "guestsCanSeeOtherGuests": False,
            "create_meeting_room": False,
        }

        exec_kwargs: dict[str, Any] = {
            "slug": "GOOGLECALENDAR_CREATE_EVENT",
            "arguments": arguments,
            "dangerously_skip_version_check": True,
        }
        if conn_id:
            exec_kwargs["connected_account_id"] = conn_id
        if acc_user_id:
            exec_kwargs["user_id"] = acc_user_id

        resp = client.tools.execute(**exec_kwargs)

        if isinstance(resp, dict) and not resp.get("successful", True):
            err_msg = resp.get("error") or resp.get("message") or "Nežinoma klaida"
            raise RuntimeError(f"Klaida kuriant įvykį kalendoriuje: {err_msg}")

        # Extract google_event_id
        google_event_id = None
        if isinstance(resp, dict):
            data = resp.get("data", {})
            if isinstance(data, dict):
                google_event_id = data.get("id") or data.get("response_data", {}).get("id")
            if not google_event_id:
                google_event_id = resp.get("id")
        if not google_event_id:
            google_event_id = f"g_evt_{int(datetime.now().timestamp())}"

        created_at_str = datetime.now(zoneinfo.ZoneInfo(timezone_str)).isoformat()

        cur = conn.cursor()
        cur.execute(
            "INSERT INTO events (user_id, google_event_id, title, starts_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, str(google_event_id), title, dt_start.isoformat(), created_at_str),
        )
        local_event_id = cur.lastrowid

        for att_uid in attendee_user_ids:
            conn.execute(
                "INSERT OR IGNORE INTO event_attendees (event_id, user_id) VALUES (?, ?)",
                (local_event_id, att_uid),
            )

        record_usage(conn, user_id=user_id, cost_usd=0.0, events_created=1)
        conn.commit()

        time_str = _format_event_time(
            {"dateTime": dt_start.isoformat()},
            {"dateTime": dt_end.isoformat()},
            tz_name=timezone_str,
        )
        att_str = f" (dalyviai: {', '.join(attendees)})" if attendees else ""
        return f"Įvykis sukurtas [Nr. {local_event_id}]: {title}, laikas: {time_str}{att_str}"

    finally:
        if close_conn:
            conn.close()


CREATE_EVENT_TOOL = Tool(
    name="create_event",
    description=(
        "sukuria įvykį ir išsiunčia kvietimus nurodytiems žmonėms. "
        "Naudok tik turėdamas pavadinimą, pradžią ir pabaigą. "
        "Nenaudok esamam įvykiui keisti."
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Įvykio pavadinimas",
            },
            "start": {
                "type": "string",
                "description": "Pradžios laikas vietiniu laiku be zonos (pvz. 2026-09-20T15:00)",
            },
            "end": {
                "type": "string",
                "description": "Pabaigos laikas vietiniu laiku be zonos (pvz. 2026-09-20T16:00)",
            },
            "attendees": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Dalyvių vardai (iš leistinų vartotojų sąrašo, ne el. pašto adresai)",
            },
            "description": {
                "type": "string",
                "description": "Papildomas įvykio aprašymas (neprivalomas)",
            },
        },
        "required": ["title", "start", "end", "attendees"],
    },
    risk="destructive",
    func=create_event,
)


def make_create_event_tool(
    conn: sqlite3.Connection,
    user_id: int,
    timezone_str: str = "Europe/Vilnius",
    composio_client: Any = None,
    connected_account_id: str | None = None,
) -> Tool:
    """Creates a user-bound create_event tool instance for registration."""
    return Tool(
        name=CREATE_EVENT_TOOL.name,
        description=CREATE_EVENT_TOOL.description,
        parameters=CREATE_EVENT_TOOL.parameters,
        risk=CREATE_EVENT_TOOL.risk,
        func=partial(
            create_event,
            conn=conn,
            user_id=user_id,
            timezone_str=timezone_str,
            composio_client=composio_client,
            connected_account_id=connected_account_id,
        ),
    )


def update_event(
    event_id: int,
    title: str | None = None,
    start: str | None = None,
    end: str | None = None,
    attendees: list[str] | None = None,
    description: str | None = None,
    conn: sqlite3.Connection | None = None,
    user_id: int | None = None,
    composio_client: Any = None,
    timezone_str: str = "Europe/Vilnius",
    connected_account_id: str | None = None,
) -> str:
    """Updates an existing calendar event.

    Allowed only for the event creator (events.user_id == user_id).
    Preserves all existing attendees from event_attendees if attendees parameter
    is not provided, because GOOGLECALENDAR_PATCH_EVENT overwrites the full attendees list.
    Passes send_updates='all'.
    """
    if user_id is None:
        raise ValueError("Trūksta vartotojo identifikatoriaus (user_id)")

    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    try:
        row = conn.execute(
            "SELECT id, user_id, google_event_id, title, starts_at, deleted_at FROM events WHERE id = ?",
            (event_id,),
        ).fetchone()

        if row is None or row["deleted_at"] is not None:
            raise ValueError(f"Įvykis Nr. {event_id} nerastas arba jau ištrintas")

        if row["user_id"] != user_id:
            raise ValueError("Galima keisti tik savo sukurtą įvykį")

        google_event_id = row["google_event_id"]
        if not google_event_id:
            raise ValueError("Įvykis neturi susieto Google kalendoriaus identifikatoriaus")

        update_db_attendees = False
        new_attendee_ids: list[int] = []
        attendee_emails: list[str] = []

        if attendees is None:
            att_rows = conn.execute(
                "SELECT user_id FROM event_attendees WHERE event_id = ? ORDER BY user_id",
                (event_id,),
            ).fetchall()
            for ar in att_rows:
                att_uid = ar["user_id"]
                try:
                    u = get_user_by_id(att_uid)
                    attendee_emails.append(u.email)
                except Exception:
                    pass
        else:
            update_db_attendees = True
            if not isinstance(attendees, list):
                attendees = [attendees] if isinstance(attendees, str) else list(attendees)
            for att_name in attendees:
                clean_name = str(att_name).strip()
                if not clean_name:
                    continue
                try:
                    u = get_user_by_name(clean_name)
                except Exception as e:
                    raise ValueError(
                        f"Dalyvis '{clean_name}' neleidžiamas: galima kviesti tik registruotus vartotojus"
                    ) from e
                attendee_emails.append(u.email)
                new_attendee_ids.append(u.telegram_id)

        start_iso = None
        if start is not None and start.strip():
            dt_start = parse_date_input(start, timezone_name=timezone_str, is_end_of_day=False)
            start_iso = dt_start.isoformat()

        end_iso = None
        if end is not None and end.strip():
            dt_end = parse_date_input(end, timezone_name=timezone_str, is_end_of_day=False)
            end_iso = dt_end.isoformat()

        if start_iso and end_iso:
            if parse_date_input(end, timezone_name=timezone_str) <= parse_date_input(start, timezone_name=timezone_str):
                raise ValueError("Pabaigos laikas negali būti ankstesnis arba lygus pradžios laikui")

        new_title = title.strip() if title is not None and title.strip() else row["title"]

        if description is not None and len(description) > MAX_DESCRIPTION_LENGTH:
            description = description[:MAX_DESCRIPTION_LENGTH]

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

        arguments: dict[str, Any] = {
            "calendar_id": "primary",
            "calendarId": "primary",
            "event_id": google_event_id,
            "eventId": google_event_id,
            "send_updates": "all",
            "sendUpdates": "all",
            "attendees": attendee_emails,
        }
        if title is not None:
            arguments["summary"] = new_title
        if start_iso is not None:
            arguments["start_datetime"] = start_iso
            arguments["start"] = {"dateTime": start_iso}
        if end_iso is not None:
            arguments["end_datetime"] = end_iso
            arguments["end"] = {"dateTime": end_iso}
        if description is not None:
            arguments["description"] = description

        exec_kwargs: dict[str, Any] = {
            "slug": "GOOGLECALENDAR_PATCH_EVENT",
            "arguments": arguments,
            "dangerously_skip_version_check": True,
        }
        if conn_id:
            exec_kwargs["connected_account_id"] = conn_id
        if acc_user_id:
            exec_kwargs["user_id"] = acc_user_id

        resp = client.tools.execute(**exec_kwargs)

        if isinstance(resp, dict) and not resp.get("successful", True):
            err_msg = resp.get("error") or resp.get("message") or "Nežinoma klaida"
            raise RuntimeError(f"Klaida atnaujinant įvykį kalendoriuje: {err_msg}")

        conn.execute(
            "UPDATE events SET title = ?, starts_at = COALESCE(?, starts_at) WHERE id = ?",
            (new_title, start_iso, event_id),
        )

        if update_db_attendees:
            conn.execute("DELETE FROM event_attendees WHERE event_id = ?", (event_id,))
            for att_uid in new_attendee_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO event_attendees (event_id, user_id) VALUES (?, ?)",
                    (event_id, att_uid),
                )

        conn.commit()

        return f"Įvykis [Nr. {event_id}] sėkmingai atnaujintas: {new_title}."

    finally:
        if close_conn:
            conn.close()


def delete_event(
    event_id: int,
    conn: sqlite3.Connection | None = None,
    user_id: int | None = None,
    composio_client: Any = None,
    timezone_str: str = "Europe/Vilnius",
    connected_account_id: str | None = None,
) -> str:
    """Cancels a calendar event and notifies attendees.

    Allowed only for the event creator (events.user_id == user_id).
    Passes send_updates='all'.
    Soft-deletes the event in SQLite (sets deleted_at timestamp).
    """
    if user_id is None:
        raise ValueError("Trūksta vartotojo identifikatoriaus (user_id)")

    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    try:
        row = conn.execute(
            "SELECT id, user_id, google_event_id, title, starts_at, deleted_at FROM events WHERE id = ?",
            (event_id,),
        ).fetchone()

        if row is None or row["deleted_at"] is not None:
            raise ValueError(f"Įvykis Nr. {event_id} nerastas arba jau ištrintas")

        if row["user_id"] != user_id:
            raise ValueError("Galima trinti tik savo sukurtą įvykį")

        google_event_id = row["google_event_id"]
        if not google_event_id:
            raise ValueError("Įvykis neturi susieto Google kalendoriaus identifikatoriaus")

        title = row["title"]

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

        arguments = {
            "calendar_id": "primary",
            "calendarId": "primary",
            "event_id": google_event_id,
            "eventId": google_event_id,
            "send_updates": "all",
            "sendUpdates": "all",
        }

        exec_kwargs: dict[str, Any] = {
            "slug": "GOOGLECALENDAR_DELETE_EVENT",
            "arguments": arguments,
            "dangerously_skip_version_check": True,
        }
        if conn_id:
            exec_kwargs["connected_account_id"] = conn_id
        if acc_user_id:
            exec_kwargs["user_id"] = acc_user_id

        resp = client.tools.execute(**exec_kwargs)

        if isinstance(resp, dict) and not resp.get("successful", True):
            err_msg = resp.get("error") or resp.get("message") or "Nežinoma klaida"
            raise RuntimeError(f"Klaida trinant įvykį kalendoriuje: {err_msg}")

        deleted_at_str = datetime.now(zoneinfo.ZoneInfo(timezone_str)).isoformat()
        conn.execute(
            "UPDATE events SET deleted_at = ? WHERE id = ?",
            (deleted_at_str, event_id),
        )
        conn.commit()

        return f"Įvykis [Nr. {event_id}] '{title}' sėkmingai atšauktas. Dalyviams išsiųsti pranešimai."

    finally:
        if close_conn:
            conn.close()


UPDATE_EVENT_TOOL = Tool(
    name="update_event",
    description=(
        "keičia esamo įvykio laukus pagal numerį. Nenaudok naujam įvykiui kurti."
    ),
    parameters={
        "type": "object",
        "properties": {
            "event_id": {
                "type": "integer",
                "description": "Įvykio numeris iš list_events sąrašo",
            },
            "title": {
                "type": "string",
                "description": "Naujas įvykio pavadinimas (neprivalomas)",
            },
            "start": {
                "type": "string",
                "description": "Naujas pradžios laikas vietiniu laiku be zonos (neprivalomas)",
            },
            "end": {
                "type": "string",
                "description": "Naujas pabaigos laikas vietiniu laiku be zonos (neprivalomas)",
            },
            "attendees": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Naujas dalyvių vardų sąrašas (neprivalomas, nenurodžius paliekami esami)",
            },
            "description": {
                "type": "string",
                "description": "Naujas aprašymas (neprivalomas)",
            },
        },
        "required": ["event_id"],
    },
    risk="destructive",
    func=update_event,
)


DELETE_EVENT_TOOL = Tool(
    name="delete_event",
    description=(
        "atšaukia įvykį pagal numerį ir praneša dalyviams. Naudok tik kai vartotojas aiškiai prašo atšaukti."
    ),
    parameters={
        "type": "object",
        "properties": {
            "event_id": {
                "type": "integer",
                "description": "Atšaukiamo įvykio numeris iš list_events sąrašo",
            },
        },
        "required": ["event_id"],
    },
    risk="destructive",
    func=delete_event,
)


def make_update_event_tool(
    conn: sqlite3.Connection,
    user_id: int,
    timezone_str: str = "Europe/Vilnius",
    composio_client: Any = None,
    connected_account_id: str | None = None,
) -> Tool:
    """Creates a user-bound update_event tool instance for registration."""
    return Tool(
        name=UPDATE_EVENT_TOOL.name,
        description=UPDATE_EVENT_TOOL.description,
        parameters=UPDATE_EVENT_TOOL.parameters,
        risk=UPDATE_EVENT_TOOL.risk,
        func=partial(
            update_event,
            conn=conn,
            user_id=user_id,
            timezone_str=timezone_str,
            composio_client=composio_client,
            connected_account_id=connected_account_id,
        ),
    )


def make_delete_event_tool(
    conn: sqlite3.Connection,
    user_id: int,
    timezone_str: str = "Europe/Vilnius",
    composio_client: Any = None,
    connected_account_id: str | None = None,
) -> Tool:
    """Creates a user-bound delete_event tool instance for registration."""
    return Tool(
        name=DELETE_EVENT_TOOL.name,
        description=DELETE_EVENT_TOOL.description,
        parameters=DELETE_EVENT_TOOL.parameters,
        risk=DELETE_EVENT_TOOL.risk,
        func=partial(
            delete_event,
            conn=conn,
            user_id=user_id,
            timezone_str=timezone_str,
            composio_client=composio_client,
            connected_account_id=connected_account_id,
        ),
    )


