"""Tests for agent/config.py name and email mapping functions."""

from pathlib import Path
import pytest

from agent.config import (
    ConfigError,
    UnknownUserError,
    User,
    email_to_name,
    email_to_vardas,
    get_user_by_email,
    get_user_by_id,
    get_user_by_name,
    name_to_email,
    vardas_to_email,
)

SAMPLE_USERS = [
    User(
        telegram_id=101,
        name="Simonas",
        email="simonas@example.com",
        timezone="Europe/Vilnius",
        role="admin",
    ),
    User(
        telegram_id=102,
        name="Ruta",
        email="ruta@example.com",
        timezone="Europe/Vilnius",
        role="member",
    ),
]


def test_name_to_email_success():
    assert name_to_email("Simonas", users=SAMPLE_USERS) == "simonas@example.com"
    assert name_to_email("Ruta", users=SAMPLE_USERS) == "ruta@example.com"
    # Case insensitive fallback
    assert name_to_email("simonas", users=SAMPLE_USERS) == "simonas@example.com"
    # Alias
    assert vardas_to_email("Simonas", users=SAMPLE_USERS) == "simonas@example.com"


def test_email_to_name_success():
    assert email_to_name("simonas@example.com", users=SAMPLE_USERS) == "Simonas"
    assert email_to_name("ruta@example.com", users=SAMPLE_USERS) == "Ruta"
    # Case insensitive
    assert email_to_name("SIMONAS@EXAMPLE.COM", users=SAMPLE_USERS) == "Simonas"
    # Alias
    assert email_to_vardas("simonas@example.com", users=SAMPLE_USERS) == "Simonas"


def test_unknown_name_raises_error():
    with pytest.raises((UnknownUserError, ConfigError, KeyError)) as exc_info:
        name_to_email("NezinomasVardas", users=SAMPLE_USERS)
    assert "NezinomasVardas" in str(exc_info.value)


def test_unknown_email_raises_error():
    with pytest.raises((UnknownUserError, ConfigError, KeyError)) as exc_info:
        email_to_name("svetimas@example.com", users=SAMPLE_USERS)
    assert "svetimas@example.com" in str(exc_info.value)


def test_get_user_helpers():
    user = get_user_by_name("Simonas", users=SAMPLE_USERS)
    assert user.telegram_id == 101

    user_by_mail = get_user_by_email("ruta@example.com", users=SAMPLE_USERS)
    assert user_by_mail.name == "Ruta"

    user_by_id = get_user_by_id(101, users=SAMPLE_USERS)
    assert user_by_id.name == "Simonas"

    with pytest.raises(UnknownUserError):
        get_user_by_name("Anonimas", users=SAMPLE_USERS)

    with pytest.raises(UnknownUserError):
        get_user_by_id(999, users=SAMPLE_USERS)


def test_mapping_with_toml_file(tmp_path: Path):
    toml_content = """
    [[user]]
    telegram_id = 201
    name = "Tomas"
    email = "tomas@example.com"
    timezone = "Europe/Vilnius"

    [[user]]
    telegram_id = 202
    name = "Elena"
    email = "elena@example.com"
    timezone = "Europe/Vilnius"
    """
    toml_file = tmp_path / "test_users.toml"
    toml_file.write_text(toml_content, encoding="utf-8")

    assert name_to_email("Tomas", file_path=toml_file) == "tomas@example.com"
    assert email_to_name("elena@example.com", file_path=toml_file) == "Elena"

    with pytest.raises(UnknownUserError):
        name_to_email("Gediminas", file_path=toml_file)
