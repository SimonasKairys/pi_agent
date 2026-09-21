"""Memory tools for pi_agent.

Lets the user see and remove the facts the assistant stores about them:
- list_facts: shows the user's stored facts with their numbers
- forget_fact: deletes one fact by number (always needs user approval)
"""

from __future__ import annotations

import sqlite3
from functools import partial

from agent.memory import delete_fact, get_user_facts
from agent.tools.registry import Tool


def list_facts(conn: sqlite3.Connection, user_id: int) -> str:
    """Returns the user's stored facts, one per line, with their numbers."""
    rows = get_user_facts(conn, user_id)
    if not rows:
        return "Apie vartotoją faktų neišsaugota."
    return "\n".join(f"{row['id']}. {row['fact']}" for row in rows)


def forget_fact(conn: sqlite3.Connection, user_id: int, fact_id: int) -> str:
    """Deletes one of the user's facts; another user's fact is never touched."""
    if delete_fact(conn, user_id, int(fact_id)):
        return f"Faktas {fact_id} pamirštas."
    return f"Fakto {fact_id} nerasta."


LIST_FACTS_TOOL = Tool(
    name="list_facts",
    description=(
        "grąžina faktus, kuriuos atsimeni apie vartotoją, su jų numeriais. "
        "Naudok, kai vartotojas klausia, ką apie jį žinai, arba prieš forget_fact."
    ),
    parameters={"type": "object", "properties": {}, "required": []},
    risk="read_only",
    func=list_facts,
)

FORGET_FACT_TOOL = Tool(
    name="forget_fact",
    description=(
        "ištrina vieną faktą apie vartotoją pagal numerį iš list_facts. "
        "Naudok tik kai vartotojas aiškiai prašo ką nors pamiršti."
    ),
    parameters={
        "type": "object",
        "properties": {
            "fact_id": {
                "type": "integer",
                "description": "Pamirštamo fakto numeris iš list_facts sąrašo",
            },
        },
        "required": ["fact_id"],
    },
    risk="destructive",
    func=forget_fact,
)


def make_list_facts_tool(conn: sqlite3.Connection, user_id: int) -> Tool:
    """Creates a user-bound list_facts tool instance for registration."""
    return Tool(
        name=LIST_FACTS_TOOL.name,
        description=LIST_FACTS_TOOL.description,
        parameters=LIST_FACTS_TOOL.parameters,
        risk=LIST_FACTS_TOOL.risk,
        func=partial(list_facts, conn=conn, user_id=user_id),
    )


def make_forget_fact_tool(conn: sqlite3.Connection, user_id: int) -> Tool:
    """Creates a user-bound forget_fact tool instance for registration."""
    return Tool(
        name=FORGET_FACT_TOOL.name,
        description=FORGET_FACT_TOOL.description,
        parameters=FORGET_FACT_TOOL.parameters,
        risk=FORGET_FACT_TOOL.risk,
        func=partial(forget_fact, conn=conn, user_id=user_id),
    )
