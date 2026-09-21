"""Memory module for pi_agent.

Handles persistent user facts, importance evaluation, deduplication,
capacity limits, and profile retrieval.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import zoneinfo
from datetime import datetime
from typing import Any

from agent.db import RESET_TIMEZONE, record_usage

logger = logging.getLogger(__name__)

# Memory limits
IMPORTANCE_THRESHOLD = 7  # 7 out of 10
MAX_USER_FACTS = 200

FACT_EXTRACTION_SYSTEM_PROMPT = """Tu esi asistento atminties modulis.
Tavo užduotis: išanalizuoti vartotojo ir asistento pokalbį ir išskirti ilgalaikius faktus apie vartotoją.

Taisyklės:
1. Išskirk tik faktus apie vartotoją (pvz. vardas, pomėgiai, įpročiai, kontaktai, taisyklės, nuostatos, gyvenamoji vieta).
2. Faktus imk tik iš to, ką vartotojas pasakė savo žinutėje. Asistento atsakymas skirtas tik kontekstui:
   neišskirk faktų, kuriuos asistentas išvardijo ar pakartojo iš atminties.
3. Neišskirk vienkartinių užklausų ar laikinų detalių (pvz., "vartotojas paklausė koks šiandien oras").
4. Kiekvienam faktui priskirk svarbą sveikais skaičiais nuo 1 iki 10:
   - 1-6: laikina ar menkavertė detalė
   - 7-8: naudingas ilgalaikis faktas (pvz. gyvena Vilniuje, geria kavą be cukraus)
   - 9-10: esminis asmeninis faktas ar svarbi taisyklė (pvz. alergiškas riešutams, dirba programuotoju)
5. Jei vartotojas aiškiai prašo ką nors prisiminti bet kuria kalba (pvz. "prisimink", "įsimink",
   "atsimink", "remember"), išskirk tą faktą su svarba 9.
6. Neišskirk prašymų ką nors pamiršti ir faktų apie pačią atmintį (pvz. "vartotojas paprašė pamiršti").
   Neišskirk ir priminimų bei užrašų (pvz. "primink rytoj...", "užsirašyk idėją...",
   "remind me...", "note...").
7. Faktą rašyk ta kalba, kuria rašo vartotojas.
8. Atsakymą pateik griežtai kaip JSON masyvą:
   [{"fact": "fakto tekstas", "importance": 8}]
   Jei tinkamų faktų nėra, grąžink tuščią masyvą: []
"""


def _normalize_fact(text: str) -> str:
    """Returns fact text without case, punctuation, or extra spaces, for duplicate checks."""
    without_punctuation = re.sub(r"[^\w\s]|_", " ", text.casefold())
    return " ".join(without_punctuation.split())


def get_current_timestamp(tz_name: str = RESET_TIMEZONE) -> str:
    """Returns the current ISO-formatted timestamp in the specified timezone."""
    tz = zoneinfo.ZoneInfo(tz_name)
    return datetime.now(tz).isoformat()


def save_fact(
    conn: sqlite3.Connection,
    user_id: int,
    fact: str,
    importance: int,
    source_msg_id: int | None = None,
    created_at: str | None = None,
    max_facts: int = MAX_USER_FACTS,
) -> int | None:
    """Saves a fact for a user if it meets importance and deduplication criteria.

    - Discards facts with importance < IMPORTANCE_THRESHOLD (7).
    - Checks for duplicates for the same user, ignoring case, punctuation, and spacing.
    - If user fact capacity reaches max_facts (200), evicts the least important and oldest fact.
    - Strictly isolates facts by user_id.

    Returns the new fact ID, or None if the fact was skipped or duplicate.
    """
    clean_fact = fact.strip()
    if not clean_fact:
        return None

    if importance < IMPORTANCE_THRESHOLD:
        logger.debug(
            "Faktas atmestas dėl per mažos svarbos (%d < %d): %s",
            importance,
            IMPORTANCE_THRESHOLD,
            clean_fact,
        )
        return None

    # Duplicate check for this user; \w keeps Lithuanian letters such as Ė and Ą
    normalized = _normalize_fact(clean_fact)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, fact FROM facts WHERE user_id = ?;",
        (user_id,),
    )
    for existing_row in cursor.fetchall():
        if _normalize_fact(existing_row["fact"]) == normalized:
            logger.debug("Dublikatas praleistas vartotojui %d: %s", user_id, clean_fact)
            return None

    # Check capacity limit for this user
    cursor.execute(
        "SELECT COUNT(*) AS cnt FROM facts WHERE user_id = ?;",
        (user_id,),
    )
    count_row = cursor.fetchone()
    current_count = int(count_row["cnt"]) if count_row else 0

    if current_count >= max_facts:
        # Evict least important, oldest fact for this user
        cursor.execute(
            """
            SELECT id FROM facts
            WHERE user_id = ?
            ORDER BY importance ASC, created_at ASC, id ASC
            LIMIT 1;
            """,
            (user_id,),
        )
        oldest_row = cursor.fetchone()
        if oldest_row:
            evict_id = oldest_row["id"]
            cursor.execute(
                "DELETE FROM facts WHERE id = ? AND user_id = ?;",
                (evict_id, user_id),
            )
            logger.info(
                "Viršyta faktų riba (%d). Ištrintas mažiausiai svarbus faktas id=%d vartotojui %d",
                max_facts,
                evict_id,
                user_id,
            )

    now = created_at or get_current_timestamp()
    cursor.execute(
        """
        INSERT INTO facts (user_id, fact, importance, source_msg_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        (user_id, clean_fact, int(importance), source_msg_id, now, now),
    )
    conn.commit()
    return cursor.lastrowid


def get_user_facts(
    conn: sqlite3.Connection,
    user_id: int,
    limit: int | None = None,
) -> list[sqlite3.Row]:
    """Retrieves all facts for a user, ordered by importance DESC and created_at DESC.

    Strictly filters by user_id to ensure user isolation.
    """
    cursor = conn.cursor()
    query = """
        SELECT id, user_id, fact, importance, source_msg_id, created_at, updated_at
        FROM facts
        WHERE user_id = ?
        ORDER BY importance DESC, created_at DESC
    """
    if limit is not None and limit > 0:
        query += f" LIMIT {int(limit)}"

    cursor.execute(query, (user_id,))
    return cursor.fetchall()


def search_user_facts(
    conn: sqlite3.Connection,
    user_id: int,
    query: str,
    limit: int = 10,
) -> list[sqlite3.Row]:
    """Searches user facts using FTS5 virtual table.

    Strictly ensures user isolation: only facts belonging to user_id are returned.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT f.id, f.user_id, f.fact, f.importance, f.source_msg_id, f.created_at, f.updated_at
        FROM facts f
        JOIN facts_fts fts ON f.id = fts.rowid
        WHERE f.user_id = ? AND facts_fts MATCH ?
        ORDER BY f.importance DESC, f.created_at DESC
        LIMIT ?;
        """,
        (user_id, query, limit),
    )
    return cursor.fetchall()


def delete_fact(conn: sqlite3.Connection, user_id: int, fact_id: int) -> bool:
    """Deletes a fact for a specific user.

    Returns True if deleted, False otherwise.
    """
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM facts WHERE id = ? AND user_id = ?;",
        (fact_id, user_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def _parse_extracted_facts(raw_text: str) -> list[dict[str, Any]]:
    """Extracts and validates a list of fact dicts from LLM output."""
    text = raw_text.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
    except Exception:
        # Try to find JSON array in the text
        match = re.search(r"\[\s*\{.*\}\s*\]", text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
            except Exception:
                return []
        else:
            return []

    if not isinstance(data, list):
        return []

    valid_facts: list[dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict) and "fact" in item and "importance" in item:
            fact_str = str(item["fact"]).strip()
            try:
                importance_val = int(item["importance"])
            except (ValueError, TypeError):
                continue
            if fact_str:
                valid_facts.append({"fact": fact_str, "importance": importance_val})
    return valid_facts


async def extract_facts(
    llm_client: Any,
    user_message: str,
    assistant_message: str,
) -> list[dict[str, Any]]:
    """Prompts LLM to extract facts and importance ratings from a conversation turn."""
    facts, _ = await _request_facts(llm_client, user_message, assistant_message)
    return facts


async def _request_facts(
    llm_client: Any,
    user_message: str,
    assistant_message: str,
) -> tuple[list[dict[str, Any]], float]:
    """Returns the extracted facts and the cost of the model call."""
    prompt_messages = [
        {"role": "system", "content": FACT_EXTRACTION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Vartotojas: {user_message}\nAsistentas: {assistant_message}",
        },
    ]
    try:
        response = await llm_client.generate(messages=prompt_messages)
        content = response.content if hasattr(response, "content") else str(response)
        cost = getattr(response, "cost_usd", 0.0)
        cost = float(cost) if isinstance(cost, (int, float)) else 0.0
        return _parse_extracted_facts(content), cost
    except Exception:
        logger.exception("Klaida nuskaitant faktus iš modelio atsako")
        return [], 0.0


async def extract_and_save_facts(
    conn: sqlite3.Connection,
    user_id: int,
    user_message: str,
    assistant_message: str,
    llm_client: Any,
    source_msg_id: int | None = None,
) -> list[int]:
    """Extracts facts from a conversation turn using LLM and saves qualifying ones.

    Returns a list of created fact IDs.
    """
    raw_facts, cost = await _request_facts(llm_client, user_message, assistant_message)
    # The extraction call is paid for too, so it counts toward the daily limit.
    if cost > 0:
        record_usage(conn, user_id=user_id, cost_usd=cost)
    saved_ids: list[int] = []
    for item in raw_facts:
        fact_id = save_fact(
            conn=conn,
            user_id=user_id,
            fact=item["fact"],
            importance=item["importance"],
            source_msg_id=source_msg_id,
        )
        if fact_id is not None:
            saved_ids.append(fact_id)
    return saved_ids
