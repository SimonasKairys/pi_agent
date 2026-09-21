"""User-facing texts in every supported language.

Only text that reaches the user directly lives here. Text the model reads (the
system prompt, tool descriptions, tool results for the model) stays Lithuanian:
the model answers in the user's language as the system prompt tells it to.
Every language must define the same keys; tests/test_i18n.py checks this.
"""

from __future__ import annotations

from typing import Any

DEFAULT_LANGUAGE = "lt"

# Written into the system prompt: "Atsakinėk {name} kalba".
LANGUAGE_NAMES_LT = {
    "lt": "lietuvių",
    "en": "anglų (English)",
}

MESSAGES: dict[str, dict[str, str]] = {
    "lt": {
        # Commands and general messages
        "start": (
            "Sveiki! Aš esu jūsų asmeninis pagalbininkas. Kuo galiu padėti?\n"
            "Parašykite /pagalba, ir parodysiu, ką moku."
        ),
        "help": """Ką moku (rašykite laisvai, kaip žmogui):

📅 Kalendorius
• „Kas mano kalendoriuje šią savaitę?“
• „Sukurk susitikimą su Rūta rytoj 15 val.“
• „Perkelk susitikimą į 16 val.“, „Atšauk susitikimą“

⏰ Priminimai
• „Primink rytoj 9 val. paskambinti Jokūbui“
• „Primink Rūtai penktadienį 18 val. atnešti raktus“
• „Kokius turiu priminimus?“, „Atšauk priminimą 2“

📝 Užrašai
• „Užsirašyk: idėja straipsniui apie šifravimą“
• „Ką buvau užsirašęs apie straipsnius?“
• „Parodyk mano užrašus“, „Ištrink užrašą 3“

🧠 Atmintis
• „Prisimink, kad geriu kavą be cukraus“
• „Ką apie mane žinai?“, „Pamiršk, kad …“

🔎 Paieška internete
• „Kokia rytoj orų prognozė Vilniuje?“

Trynimas, keitimas ir priminimai kitiems patvirtinami mygtuku.

Komandos:
/pagalba – šis sąrašas
/islaidos – šiandienos išlaidos ir dienos ribos""",
        "cmd_help": "pagalba",
        "cmd_help_desc": "Ką moku ir kaip manęs paprašyti",
        "cmd_costs": "islaidos",
        "cmd_costs_desc": "Šiandienos išlaidos ir dienos ribos",
        "unauthorized": "Atsiprašome, neturite prieigos prie šio boto.",
        "error": "Atsiprašome, įvyko klaida apdorojant jūsų užklausą. Pabandykite vėliau.",
        "busy": "Jūsų ankstesnė užklausa dar vykdoma. Prašome palaukti.",
        "user_limit": "Viršyta jūsų dienos naudojimo riba ({limit} USD). Bandykite rytoj.",
        "system_limit": "Viršyta bendra sistemos dienos naudojimo riba ({limit} USD). Bandykite rytoj.",
        "event_limit": "Viršyta jūsų dienos įvykių kūrimo riba ({limit}). Bandykite rytoj.",
        # Approval cards
        "card_header": "⚠️ **Reikalingas patvirtinimas veiksmui atlikti**\n",
        "card_action": "Veiksmas",
        "card_title": "Pavadinimas",
        "card_event_no": "Įvykio numeris",
        "card_fact": "Faktas",
        "card_fact_no": "Fakto numeris",
        "card_to": "Kam",
        "card_when": "Kada",
        "card_text": "Tekstas",
        "card_note_no": "Užrašo numeris",
        "card_reminder_no": "Priminimo numeris",
        "card_time": "Laikas",
        "card_attendees": "Dalyvių skaičius",
        "card_attendees_none": "nėra",
        "card_attendee_count": "Dalyvių",
        "card_valid_for": "Patvirtinimas galioja {minutes} min.",
        "btn_approve": "Tvirtinti",
        "btn_reject": "Atmesti",
        "action_update_event": "Įvykio keitimas",
        "action_delete_event": "Įvykio atšaukimas",
        "action_create_event": "Naujo įvykio sukūrimas",
        "action_forget_fact": "Fakto pamiršimas",
        "action_create_reminder": "Priminimas kitam vartotojui",
        "action_delete_reminder": "Priminimo atšaukimas",
        "action_delete_note": "Užrašo trynimas",
        "approval_expired": "⌛ Patvirtinimo laikas baigėsi, veiksmas atmestas.",
        "approval_not_found": "Patvirtinimo užklausa nerasta.",
        "approval_not_allowed": "Neturite teisės tvirtinti šio veiksmo.",
        "approval_timed_out": "Patvirtinimo galiojimo laikas pasibaigęs.",
        "approval_already_approved": "Veiksmas jau patvirtintas.",
        "approval_already_rejected": "Veiksmas jau atmestas.",
        "approval_already_expired": "Veiksmas pasenęs.",
        "approval_already_processed": "Veiksmas jau buvo apdorotas.",
        "approval_rejected": "Veiksmas atmestas.",
        "approval_approved": "Veiksmas patvirtintas.",
        # /islaidos
        "costs_title": "Išlaidos šiandien ({day}):",
        "costs_yours": "• Jūsų: {spent} USD iš {limit} USD",
        "costs_events": "• Sukurta įvykių: {count} iš {limit}",
        "costs_system": "• Visos sistemos: {spent} USD iš {limit} USD",
        # Reminder delivery
        "reminder": "⏰ Priminimas: {text}",
        "reminder_from": "⏰ Priminimas nuo {sender}: {text}",
        "reminder_late": "(Vėluoja: turėjo būti {time})",
        "reminder_failed": (
            "Priminimo {name} išsiųsti nepavyko: šis vartotojas dar nėra pradėjęs "
            "pokalbio su botu (/start). Priminimas: {text}"
        ),
        # Tool results that can reach the user directly after an approval
        "fact_forgotten": "Faktas {id} pamirštas.",
        "fact_not_found": "Fakto {id} nerasta.",
        "facts_none": "Apie vartotoją faktų neišsaugota.",
        "note_saved": "Užrašas {id} išsaugotas.",
        "note_deleted": "Užrašas {id} ištrintas.",
        "note_not_found": "Užrašo {id} nerasta.",
        "note_empty": "Klaida: užrašas tuščias.",
        "note_too_long": "Klaida: užrašas per ilgas (daugiausia {limit} simbolių).",
        "note_limit": "Klaida: pasiekta užrašų riba ({limit}). Pirmiausia ištrink senus užrašus.",
        "notes_none": "Užrašų nėra.",
        "notes_no_match": "Tinkamų užrašų nerasta.",
        "reminder_created": "Priminimas {id} sukurtas: {time} – {text}",
        "reminder_created_for": "Priminimas {id} vartotojui {name} sukurtas: {time} – {text}",
        "reminder_cancelled": "Priminimas {id} atšauktas.",
        "reminder_not_found": "Priminimo {id} nerasta.",
        "reminder_empty": "Klaida: priminimo tekstas tuščias.",
        "reminder_too_long": "Klaida: priminimas per ilgas (daugiausia {limit} simbolių).",
        "reminder_bad_time": "Klaida: laiką nurodyk taip: 2026-09-22T09:00.",
        "reminder_past": "Klaida: šis laikas jau praėjo.",
        "reminder_too_far": "Klaida: priminti galima ne vėliau kaip po {days} dienų.",
        "reminder_unknown_user": (
            "Klaida: vartotojo „{name}“ nėra. Priminti galima tik boto vartotojams: {names}."
        ),
        "reminder_limit": "Klaida: pasiekta laukiančių priminimų riba ({limit}).",
        "reminders_none": "Laukiančių priminimų nėra.",
        "reminder_to_user": "(vartotojui {name})",
        "reminder_from_user": "(nuo {name})",
    },
    "en": {
        "start": (
            "Hello! I'm your personal assistant. How can I help?\n"
            "Send /help to see what I can do."
        ),
        "help": """What I can do (write naturally, as to a person):

📅 Calendar
• "What's in my calendar this week?"
• "Create a meeting with Rūta tomorrow at 3 pm"
• "Move the meeting to 4 pm", "Cancel the meeting"

⏰ Reminders
• "Remind me tomorrow at 9 am to call Jokūbas"
• "Remind Rūta on Friday at 6 pm to bring the keys"
• "What reminders do I have?", "Cancel reminder 2"

📝 Notes
• "Note: idea for an article about encryption"
• "What did I note about articles?"
• "Show my notes", "Delete note 3"

🧠 Memory
• "Remember that I take my coffee without sugar"
• "What do you know about me?", "Forget that …"

🔎 Web search
• "What's the weather forecast for Vilnius tomorrow?"

Deleting, changing, and reminders for others are confirmed with a button.

Commands:
/help – this list
/costs – today's spending and daily limits""",
        "cmd_help": "help",
        "cmd_help_desc": "What I can do and how to ask",
        "cmd_costs": "costs",
        "cmd_costs_desc": "Today's spending and daily limits",
        "unauthorized": "Sorry, you don't have access to this bot.",
        "error": "Sorry, something went wrong while handling your request. Please try again later.",
        "busy": "Your previous request is still running. Please wait.",
        "user_limit": "You have reached your daily usage limit ({limit} USD). Please try again tomorrow.",
        "system_limit": "The system's daily usage limit ({limit} USD) has been reached. Please try again tomorrow.",
        "event_limit": "You have reached your daily event limit ({limit}). Please try again tomorrow.",
        "card_header": "⚠️ **Confirmation required**\n",
        "card_action": "Action",
        "card_title": "Title",
        "card_event_no": "Event number",
        "card_fact": "Fact",
        "card_fact_no": "Fact number",
        "card_to": "To",
        "card_when": "When",
        "card_text": "Text",
        "card_note_no": "Note number",
        "card_reminder_no": "Reminder number",
        "card_time": "Time",
        "card_attendees": "Attendees",
        "card_attendees_none": "none",
        "card_attendee_count": "Attendees",
        "card_valid_for": "This confirmation is valid for {minutes} min.",
        "btn_approve": "Confirm",
        "btn_reject": "Reject",
        "action_update_event": "Change event",
        "action_delete_event": "Cancel event",
        "action_create_event": "Create event",
        "action_forget_fact": "Forget fact",
        "action_create_reminder": "Reminder for another user",
        "action_delete_reminder": "Cancel reminder",
        "action_delete_note": "Delete note",
        "approval_expired": "⌛ The confirmation expired, so the action was rejected.",
        "approval_not_found": "Confirmation request not found.",
        "approval_not_allowed": "You are not allowed to confirm this action.",
        "approval_timed_out": "This confirmation has expired.",
        "approval_already_approved": "The action is already confirmed.",
        "approval_already_rejected": "The action is already rejected.",
        "approval_already_expired": "The action has expired.",
        "approval_already_processed": "The action was already handled.",
        "approval_rejected": "Action rejected.",
        "approval_approved": "Action confirmed.",
        "costs_title": "Spending today ({day}):",
        "costs_yours": "• You: {spent} USD of {limit} USD",
        "costs_events": "• Events created: {count} of {limit}",
        "costs_system": "• Whole system: {spent} USD of {limit} USD",
        "reminder": "⏰ Reminder: {text}",
        "reminder_from": "⏰ Reminder from {sender}: {text}",
        "reminder_late": "(Late: it was due at {time})",
        "reminder_failed": (
            "The reminder for {name} could not be sent: they haven't started a chat "
            "with the bot yet (/start). Reminder: {text}"
        ),
        "fact_forgotten": "Fact {id} forgotten.",
        "fact_not_found": "Fact {id} not found.",
        "facts_none": "No facts about the user are stored.",
        "note_saved": "Note {id} saved.",
        "note_deleted": "Note {id} deleted.",
        "note_not_found": "Note {id} not found.",
        "note_empty": "Error: the note is empty.",
        "note_too_long": "Error: the note is too long (at most {limit} characters).",
        "note_limit": "Error: the note limit ({limit}) is reached. Delete old notes first.",
        "notes_none": "No notes.",
        "notes_no_match": "No matching notes found.",
        "reminder_created": "Reminder {id} created: {time} – {text}",
        "reminder_created_for": "Reminder {id} for {name} created: {time} – {text}",
        "reminder_cancelled": "Reminder {id} cancelled.",
        "reminder_not_found": "Reminder {id} not found.",
        "reminder_empty": "Error: the reminder text is empty.",
        "reminder_too_long": "Error: the reminder is too long (at most {limit} characters).",
        "reminder_bad_time": "Error: give the time like this: 2026-09-22T09:00.",
        "reminder_past": "Error: this time has already passed.",
        "reminder_too_far": "Error: a reminder can be at most {days} days ahead.",
        "reminder_unknown_user": (
            "Error: there is no user \"{name}\". Reminders can go only to bot users: {names}."
        ),
        "reminder_limit": "Error: the pending reminder limit ({limit}) is reached.",
        "reminders_none": "No pending reminders.",
        "reminder_to_user": "(for {name})",
        "reminder_from_user": "(from {name})",
    },
}


def normalize_language(language: str | None) -> str:
    """Returns a supported language code, falling back to the default."""
    code = (language or "").strip().lower()
    return code if code in MESSAGES else DEFAULT_LANGUAGE


def t(language: str | None, key: str, **values: Any) -> str:
    """Returns the text for `key` in `language`, with {placeholders} filled in."""
    text = MESSAGES[normalize_language(language)][key]
    return text.format(**values) if values else text


def language_from_telegram(language_code: str | None) -> str:
    """Picks a language for people not in users.toml, from their Telegram app language."""
    if isinstance(language_code, str) and language_code.lower().startswith("en"):
        return "en"
    return DEFAULT_LANGUAGE


def format_usd(amount: float, language: str | None = DEFAULT_LANGUAGE) -> str:
    """Formats a USD amount: decimal comma in Lithuanian, decimal point in English."""
    text = f"{amount:.2f}"
    return text.replace(".", ",") if normalize_language(language) == "lt" else text


def format_usd_precise(amount: float, language: str | None = DEFAULT_LANGUAGE) -> str:
    """Formats a small USD amount with four decimals."""
    text = f"{amount:.4f}"
    return text.replace(".", ",") if normalize_language(language) == "lt" else text
