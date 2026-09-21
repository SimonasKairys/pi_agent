"""Nightly memory consolidation entry point for pi_agent.

Processes the last 24 hours of conversation history and existing facts
for each user independently, generating 2-3 synthesizing insights.
Runs non-interactively via systemd timer piagent-consolidate.timer at 03:00
Europe/Vilnius (30 minutes before the 03:30 backup).
Does NOT import agent/telegram_ui.py.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
import zoneinfo
from datetime import datetime, timedelta
from typing import Any

from agent.config import User, load_users
from agent.db import RESET_TIMEZONE, get_connection, record_usage
from agent.journal import record_journal_entry
from agent.llm import LLMClient
from agent.memory import _parse_extracted_facts, get_user_facts, save_fact

logger = logging.getLogger(__name__)

CONSOLIDATION_SYSTEM_PROMPT = """Tu esi asistento atminties konsolidatorius.
Tavo užduotis: išanalizuoti vartotojo paskutinės paros pokalbius bei esamus faktus ir sukurti 2–3 apibendrinančias ilgalaikes įžvalgas apie vartotoją.

Taisyklės:
1. Ieškok pasikartojančių įpročių, prioritetų, taisyklių, pageidavimų ar apibendrinimų, kurie padės asistentui geriau pažinti vartotoją ateityje.
2. Nekartok jau žinomų faktų pažodžiui.
3. Kiekvienai įžvalgai priskirk svarbą nuo 7 iki 10 (7 - naudingas pastebėjimas, 10 - esminė taisyklė ar įžvalga).
4. Atsakymą pateik griežtai kaip JSON masyvą:
   [{"fact": "įžvalgos tekstas", "importance": 8}]
   Jei naujų prasmingų įžvalgų suformuluoti negalima, grąžink tuščią masyvą: []
"""


def get_recent_messages(
    conn: Any,
    user_id: int,
    since_timestamp: str,
) -> list[dict[str, Any]]:
    """Retrieves messages for a given user created at or after since_timestamp."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, role, content, created_at
        FROM messages
        WHERE user_id = ? AND created_at >= ?
        ORDER BY created_at ASC;
        """,
        (user_id, since_timestamp),
    )
    rows = cursor.fetchall()
    return [{"id": r["id"], "role": r["role"], "content": r["content"], "created_at": r["created_at"]} for r in rows]


async def consolidate_user(
    conn: Any,
    user: User,
    llm_client: LLMClient,
    since_timestamp: str | None = None,
    run_id: str | None = None,
) -> list[int]:
    """Consolidates memory for a single user.

    Strictly isolated: only accesses and updates data for user.telegram_id.
    Empty history causes no error and returns an empty list.
    """
    user_id = user.telegram_id
    if run_id is None:
        run_id = f"cons_{uuid.uuid4().hex[:12]}"

    if since_timestamp is None:
        try:
            tz = zoneinfo.ZoneInfo(user.timezone or RESET_TIMEZONE)
        except Exception:
            tz = zoneinfo.ZoneInfo(RESET_TIMEZONE)
        since_timestamp = (datetime.now(tz) - timedelta(hours=24)).isoformat()

    recent_messages = get_recent_messages(conn, user_id, since_timestamp)
    existing_facts = get_user_facts(conn, user_id)

    # If there is nothing in recent history, nothing to consolidate
    if not recent_messages:
        logger.info("Vartotojas %s (%d): paskutinės paros žinučių nėra, konsolidacija praleidžiama.", user.name, user_id)
        return []

    # Build prompt for consolidation
    msg_lines = [f"{m['role']}: {m['content']}" for m in recent_messages]
    msg_block = "\n".join(msg_lines)

    fact_lines = [f"- {f['fact']} (svarba: {f['importance']})" for f in existing_facts]
    fact_block = "\n".join(fact_lines) if fact_lines else "Nėra anksčiau įrašytų faktų."

    user_prompt = (
        f"Vartotojo vardas: {user.name}\n\n"
        f"Esami žinomi faktai:\n{fact_block}\n\n"
        f"Paskutinės paros pokalbiai:\n{msg_block}\n\n"
        "Suformuluok 2–3 apibendrinančias įžvalgas pagal pateiktas taisykles."
    )

    messages = [
        {"role": "system", "content": CONSOLIDATION_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    try:
        response = await llm_client.generate(messages=messages)
    except Exception:
        logger.exception("Klaida kviečiant modelį konsolidacijai vartotojui %d", user_id)
        return []

    # Record token usage (no daily cost limit check: runs at night, non-blocking)
    record_usage(conn, user_id=user_id, cost_usd=response.cost_usd)
    try:
        record_journal_entry(
            run_id=run_id,
            user_id=user_id,
            tool_name="consolidate",
            tokens=response.total_tokens,
            cost_usd=response.cost_usd,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
        )
    except Exception as log_err:
        logger.warning("Klaida rašant konsolidaciją į žurnalą vartotojui %d: %s", user_id, log_err)

    parsed_insights = _parse_extracted_facts(response.content)
    saved_ids: list[int] = []

    for item in parsed_insights:
        fact_id = save_fact(
            conn=conn,
            user_id=user_id,
            fact=item["fact"],
            importance=item["importance"],
        )
        if fact_id is not None:
            saved_ids.append(fact_id)

    logger.info(
        "Konsolidacija baigta vartotojui %s (%d): sukurta %d naujų įžvalgų",
        user.name,
        user_id,
        len(saved_ids),
    )
    return saved_ids


async def run_consolidation(
    conn: Any | None = None,
    llm_client: LLMClient | None = None,
    users: list[User] | None = None,
    since_timestamp: str | None = None,
) -> dict[int, list[int]]:
    """Runs memory consolidation sequentially for all authorized users."""
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    if llm_client is None:
        llm_client = LLMClient()

    if users is None:
        try:
            users = load_users()
        except Exception:
            logger.exception("Nepavyko nuskaityti vartotojų sąrašo konsolidacijai")
            users = []

    results: dict[int, list[int]] = {}

    try:
        for user in users:
            try:
                created_ids = await consolidate_user(
                    conn=conn,
                    user=user,
                    llm_client=llm_client,
                    since_timestamp=since_timestamp,
                )
                results[user.telegram_id] = created_ids
            except Exception:
                logger.exception("Klaida apdorojant vartotoją %d konsolidacijos metu", user.telegram_id)
                results[user.telegram_id] = []
    finally:
        if close_conn:
            conn.close()

    return results


def main() -> None:
    """CLI entry point for systemd timer piagent-consolidate.service."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Pradedama naktinė atminties konsolidacija...")
    results = asyncio.run(run_consolidation())
    total_created = sum(len(ids) for ids in results.values())
    logger.info(
        "Naktinė konsolidacija baigta. Apdorota vartotojų: %d, sukurta įžvalgų: %d",
        len(results),
        total_created,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
