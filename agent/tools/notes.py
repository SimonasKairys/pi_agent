"""Note tools for pi_agent.

Notes hold the user's ideas and thoughts. Unlike facts, they are never added to
the system prompt; the model finds them on demand, so there can be many.
- add_note: saves a note
- search_notes: finds notes by words (FTS5, prefix match for Lithuanian endings)
- list_notes: shows the most recent notes
- delete_note: deletes one note by number (always needs user approval)
"""

from __future__ import annotations

import re
import sqlite3
from functools import partial

from agent.i18n import t
from agent.memory import get_current_timestamp
from agent.tools.registry import Tool

MAX_NOTE_LENGTH = 2000
MAX_USER_NOTES = 1000
LIST_NOTES_LIMIT = 20
SEARCH_NOTES_LIMIT = 10


def _format_rows(rows: list[sqlite3.Row]) -> str:
    return "\n".join(f"{row['id']}. [{row['created_at'][:10]}] {row['text']}" for row in rows)


def add_note(conn: sqlite3.Connection, user_id: int, text: str, language: str = "lt") -> str:
    """Saves a note for the user."""
    clean = str(text).strip()
    if not clean:
        return t(language, "note_empty")
    if len(clean) > MAX_NOTE_LENGTH:
        return t(language, "note_too_long", limit=MAX_NOTE_LENGTH)
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM notes WHERE user_id = ?", (user_id,)
    ).fetchone()["c"]
    if count >= MAX_USER_NOTES:
        return t(language, "note_limit", limit=MAX_USER_NOTES)
    cursor = conn.execute(
        "INSERT INTO notes (user_id, text, created_at) VALUES (?, ?, ?)",
        (user_id, clean, get_current_timestamp()),
    )
    conn.commit()
    return t(language, "note_saved", id=cursor.lastrowid)


def list_notes(conn: sqlite3.Connection, user_id: int, language: str = "lt") -> str:
    """Returns the user's most recent notes with their numbers."""
    rows = conn.execute(
        "SELECT id, text, created_at FROM notes WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, LIST_NOTES_LIMIT),
    ).fetchall()
    if not rows:
        return t(language, "notes_none")
    return _format_rows(rows)


def search_notes(conn: sqlite3.Connection, user_id: int, query: str, language: str = "lt") -> str:
    """Finds the user's notes that contain words starting like the query words."""
    # Words under 3 letters ("ir", "ką") would match almost every note.
    words = [w for w in re.findall(r"\w+", str(query)) if len(w) >= 3]
    if not words:
        return list_notes(conn, user_id, language)
    # Quoting each word keeps FTS5 syntax out of the model's query; the prefix
    # match lets "straipsnis" find "straipsnį" and "straipsniui".
    match = " OR ".join(f'"{word[:max(3, len(word) - 2)]}"*' for word in words)
    rows = conn.execute(
        """
        SELECT n.id, n.text, n.created_at
        FROM notes n
        JOIN notes_fts ON n.id = notes_fts.rowid
        WHERE n.user_id = ? AND notes_fts MATCH ?
        ORDER BY n.id DESC
        LIMIT ?
        """,
        (user_id, match, SEARCH_NOTES_LIMIT),
    ).fetchall()
    if not rows:
        return t(language, "notes_no_match")
    return _format_rows(rows)


def delete_note(conn: sqlite3.Connection, user_id: int, note_id: int, language: str = "lt") -> str:
    """Deletes one of the user's notes; another user's note is never touched."""
    cursor = conn.execute(
        "DELETE FROM notes WHERE id = ? AND user_id = ?", (int(note_id), user_id)
    )
    conn.commit()
    if cursor.rowcount:
        return t(language, "note_deleted", id=note_id)
    return t(language, "note_not_found", id=note_id)


ADD_NOTE_TOOL = Tool(
    name="add_note",
    description=(
        "išsaugo vartotojo užrašą: idėją, mintį ar pastabą. Naudok, kai vartotojas prašo "
        "užsirašyti ar užrašyti. Nenaudok faktams apie vartotoją ir priminimams."
    ),
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Užrašo tekstas"},
        },
        "required": ["text"],
    },
    risk="destructive",
    func=add_note,
)

SEARCH_NOTES_TOOL = Tool(
    name="search_notes",
    description="ieško vartotojo užrašų pagal žodžius. Naudok, kai vartotojas klausia, ką buvo užsirašęs.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Paieškos žodžiai"},
        },
        "required": ["query"],
    },
    risk="read_only",
    func=search_notes,
)

LIST_NOTES_TOOL = Tool(
    name="list_notes",
    description="grąžina naujausius vartotojo užrašus su jų numeriais.",
    parameters={"type": "object", "properties": {}, "required": []},
    risk="read_only",
    func=list_notes,
)

DELETE_NOTE_TOOL = Tool(
    name="delete_note",
    description=(
        "ištrina vieną užrašą pagal numerį iš list_notes arba search_notes. "
        "Naudok tik kai vartotojas aiškiai prašo ištrinti užrašą."
    ),
    parameters={
        "type": "object",
        "properties": {
            "note_id": {"type": "integer", "description": "Trinamo užrašo numeris"},
        },
        "required": ["note_id"],
    },
    risk="destructive",
    func=delete_note,
)


def make_note_tools(conn: sqlite3.Connection, user_id: int, language: str = "lt") -> list[Tool]:
    """Creates user-bound note tool instances for registration."""
    return [
        Tool(
            name=tool.name,
            description=tool.description,
            parameters=tool.parameters,
            risk=tool.risk,
            func=partial(tool.func, conn=conn, user_id=user_id, language=language),
        )
        for tool in (ADD_NOTE_TOOL, SEARCH_NOTES_TOOL, LIST_NOTES_TOOL, DELETE_NOTE_TOOL)
    ]
