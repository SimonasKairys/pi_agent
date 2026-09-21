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


def test_system_prompt_contains_today_date_in_user_timezone():
    import zoneinfo
    from datetime import datetime

    for tz_name in ["Europe/Vilnius", "America/New_York"]:
        now = datetime.now(zoneinfo.ZoneInfo(tz_name))
        today_date = now.strftime("%Y-%m-%d")
        prompt = build_system_prompt(name="Simonas", timezone_name=tz_name)

        assert today_date in prompt
        assert tz_name in prompt
        assert "Simonas" in prompt



def test_system_prompt_includes_weekday(monkeypatch):
    import agent.prompts as prompts
    from datetime import datetime as real_datetime

    class FixedDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 9, 21, 21, 19, tzinfo=tz)

    monkeypatch.setattr(prompts, "datetime", FixedDatetime)
    prompt = build_system_prompt(name="Simonas", timezone_name="Europe/Vilnius")

    assert "2026-09-21 21:19, pirmadienis" in prompt
