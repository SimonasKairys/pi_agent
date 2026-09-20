"""Tests for agent/prompts.py."""

from agent.prompts import (
    START_MESSAGE,
    UNAUTHORIZED_MESSAGE,
    ERROR_MESSAGE,
    USER_LIMIT_EXCEEDED_MESSAGE,
    SYSTEM_LIMIT_EXCEEDED_MESSAGE,
    BUSY_MESSAGE,
    build_system_prompt,
    SYSTEM_PROMPT_TEMPLATE,
)


def test_system_prompt_placeholders():
    # Template must contain the required placeholders
    assert "{data_laikas}" in SYSTEM_PROMPT_TEMPLATE
    assert "{laiko_juosta}" in SYSTEM_PROMPT_TEMPLATE
    assert "{vardas}" in SYSTEM_PROMPT_TEMPLATE
    assert "{vardu_sarasas}" in SYSTEM_PROMPT_TEMPLATE


def test_build_system_prompt():
    prompt = build_system_prompt(
        name="Simonas",
        timezone_name="Europe/Vilnius",
        allowed_names=["Rūta", "Tomas"],
        current_time_str="2026-09-20 15:30",
    )

    assert "Simonas" in prompt
    assert "Europe/Vilnius" in prompt
    assert "2026-09-20 15:30" in prompt
    assert "Rūta, Tomas" in prompt
    assert "Tu esi asmeninis pagalbininkas" in prompt


def test_messages_are_lithuanian():
    messages = [
        START_MESSAGE,
        UNAUTHORIZED_MESSAGE,
        ERROR_MESSAGE,
        USER_LIMIT_EXCEEDED_MESSAGE,
        SYSTEM_LIMIT_EXCEEDED_MESSAGE,
        BUSY_MESSAGE,
    ]

    english_markers = ["Sorry", "Hello", "Unauthorized", "Error", "Welcome", "Please wait"]
    for msg in messages:
        assert isinstance(msg, str)
        assert len(msg.strip()) > 0
        for marker in english_markers:
            assert marker.lower() not in msg.lower(), f"Found English word '{marker}' in '{msg}'"

    # Verify Lithuanian indicators
    assert "Sveiki" in START_MESSAGE
    assert "Atsiprašome" in UNAUTHORIZED_MESSAGE
    assert "Atsiprašome" in ERROR_MESSAGE
    assert "riba" in USER_LIMIT_EXCEEDED_MESSAGE
    assert "riba" in SYSTEM_LIMIT_EXCEEDED_MESSAGE
