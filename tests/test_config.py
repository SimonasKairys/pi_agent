"""Tests for agent/config.py."""

import pytest
from pathlib import Path
from agent.config import ConfigError, User, load_users, get_users_file_path


VALID_TOML = """
[[user]]
telegram_id = 111111111
name = "Simonas"
email = "vardas@example.com"
timezone = "Europe/Vilnius"
role = "admin"

[[user]]
telegram_id = 222222222
name = "Rūta"
email = "ruta@example.com"
timezone = "Europe/Vilnius"
role = "member"
"""


def test_load_valid_users(tmp_path: Path):
    users_file = tmp_path / "users.toml"
    users_file.write_text(VALID_TOML, encoding="utf-8")

    users = load_users(users_file)
    assert len(users) == 2
    assert users[0] == User(
        telegram_id=111111111,
        name="Simonas",
        email="vardas@example.com",
        timezone="Europe/Vilnius",
        role="admin",
    )
    assert users[1] == User(
        telegram_id=222222222,
        name="Rūta",
        email="ruta@example.com",
        timezone="Europe/Vilnius",
        role="member",
    )


def test_duplicate_name(tmp_path: Path):
    content = """
[[user]]
telegram_id = 111111111
name = "Simonas"
email = "simonas1@example.com"
timezone = "Europe/Vilnius"

[[user]]
telegram_id = 222222222
name = "Simonas"
email = "simonas2@example.com"
timezone = "Europe/Vilnius"
"""
    users_file = tmp_path / "users.toml"
    users_file.write_text(content, encoding="utf-8")

    with pytest.raises(ConfigError, match="Duplicate user name"):
        load_users(users_file)


def test_duplicate_telegram_id(tmp_path: Path):
    content = """
[[user]]
telegram_id = 111111111
name = "Simonas"
email = "simonas@example.com"
timezone = "Europe/Vilnius"

[[user]]
telegram_id = 111111111
name = "Jonas"
email = "jonas@example.com"
timezone = "Europe/Vilnius"
"""
    users_file = tmp_path / "users.toml"
    users_file.write_text(content, encoding="utf-8")

    with pytest.raises(ConfigError, match="Duplicate telegram_id"):
        load_users(users_file)


def test_invalid_timezone(tmp_path: Path):
    content = """
[[user]]
telegram_id = 111111111
name = "Simonas"
email = "simonas@example.com"
timezone = "Invalid/Timezone_Not_Real"
"""
    users_file = tmp_path / "users.toml"
    users_file.write_text(content, encoding="utf-8")

    with pytest.raises(ConfigError, match="unrecognized timezone"):
        load_users(users_file)


def test_missing_file(tmp_path: Path):
    non_existent = tmp_path / "does_not_exist.toml"
    with pytest.raises(ConfigError, match="not found"):
        load_users(non_existent)


def test_invalid_email(tmp_path: Path):
    content = """
[[user]]
telegram_id = 111111111
name = "Simonas"
email = "invalid_email_without_at"
timezone = "Europe/Vilnius"
"""
    users_file = tmp_path / "users.toml"
    users_file.write_text(content, encoding="utf-8")

    with pytest.raises(ConfigError, match="invalid email"):
        load_users(users_file)


def test_env_var_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    custom_path = tmp_path / "custom_users.toml"
    custom_path.write_text(VALID_TOML, encoding="utf-8")
    monkeypatch.setenv("PIAGENT_USERS_FILE", str(custom_path))

    assert get_users_file_path() == custom_path
    users = load_users()
    assert len(users) == 2
