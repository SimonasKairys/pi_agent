"""Configuration module for pi_agent.

Loads users from TOML file and configuration from environment variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
import zoneinfo
import tomllib
from dotenv import load_dotenv

load_dotenv()

DEFAULT_USERS_FILE = "/etc/piagent/users.toml"
DEFAULT_DB_PATH = "/home/piagent/data/agent.db"
DEFAULT_LOG_DIR = "/home/piagent/logs"


class ConfigError(Exception):
    """Raised when configuration or user validation fails."""
    pass


@dataclass(frozen=True)
class User:
    telegram_id: int
    name: str
    email: str
    timezone: str
    role: str = "member"


@dataclass(frozen=True)
class Guest:
    """An external person that events may invite. Guests have no bot access."""

    name: str
    email: str


def get_users_file_path() -> Path:
    """Returns the path to users.toml configuration file."""
    return Path(os.environ.get("PIAGENT_USERS_FILE", DEFAULT_USERS_FILE))


def get_db_path() -> Path:
    """Returns the path to SQLite database."""
    return Path(os.environ.get("PIAGENT_DB_PATH", DEFAULT_DB_PATH))


def get_log_dir() -> Path:
    """Returns the directory for JSONL logs."""
    return Path(os.environ.get("PIAGENT_LOG_DIR", DEFAULT_LOG_DIR))


def load_users(file_path: str | Path | None = None) -> list[User]:
    """Loads and validates users from TOML file.

    Raises ConfigError if the file is missing, malformed, or fails validation:
    - telegram_id and name must be unique
    - email must contain '@'
    - timezone must be recognized by zoneinfo
    """
    path = Path(file_path) if file_path is not None else get_users_file_path()

    if not path.exists():
        raise ConfigError(f"Users configuration file not found: {path}")

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except Exception as e:
        raise ConfigError(f"Failed to parse TOML configuration from {path}: {e}") from e

    user_entries = data.get("user")
    if user_entries is None:
        user_entries = data.get("users", [])

    if not isinstance(user_entries, list):
        raise ConfigError(f"Expected [[user]] table array in {path}, got {type(user_entries).__name__}")

    users: list[User] = []
    seen_ids: set[int] = set()
    seen_names: set[str] = set()

    for idx, entry in enumerate(user_entries):
        if not isinstance(entry, dict):
            raise ConfigError(f"User entry #{idx + 1} is not a valid table")

        if "telegram_id" not in entry:
            raise ConfigError(f"User entry #{idx + 1} is missing required field 'telegram_id'")
        try:
            telegram_id = int(entry["telegram_id"])
        except (ValueError, TypeError):
            raise ConfigError(f"User entry #{idx + 1} 'telegram_id' must be an integer: {entry.get('telegram_id')}")

        if telegram_id in seen_ids:
            raise ConfigError(f"Duplicate telegram_id {telegram_id} found in user entry #{idx + 1}")

        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"User entry #{idx + 1} must have a non-empty string 'name'")
        name = name.strip()
        if name in seen_names:
            raise ConfigError(f"Duplicate user name '{name}' found in user entry #{idx + 1}")

        email = entry.get("email")
        if not isinstance(email, str) or "@" not in email:
            raise ConfigError(f"User entry '{name}' has invalid email: {email!r} (must contain '@')")

        tz_str = entry.get("timezone")
        if not isinstance(tz_str, str) or not tz_str.strip():
            raise ConfigError(f"User entry '{name}' has missing or empty timezone")
        tz_str = tz_str.strip()
        try:
            zoneinfo.ZoneInfo(tz_str)
        except Exception as e:
            raise ConfigError(f"User entry '{name}' has unrecognized timezone '{tz_str}': {e}") from e

        role = entry.get("role", "member")
        if not isinstance(role, str):
            role = "member"

        users.append(User(
            telegram_id=telegram_id,
            name=name,
            email=email.strip(),
            timezone=tz_str,
            role=role.strip()
        ))
        seen_ids.add(telegram_id)
        seen_names.add(name)

    return users


def load_guests(file_path: str | Path | None = None) -> list[Guest]:
    """Loads and validates the [[guest]] entries from users.toml.

    Guests can only be invited to events; they are not users of the bot.
    Raises ConfigError if the file is malformed, an entry lacks a name or an
    email with '@', or a name repeats or clashes with a [[user]] name (the model
    refers to people by name, so names must be unambiguous).
    """
    path = Path(file_path) if file_path is not None else get_users_file_path()
    if not path.exists():
        raise ConfigError(f"Users configuration file not found: {path}")

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except Exception as e:
        raise ConfigError(f"Failed to parse TOML configuration from {path}: {e}") from e

    entries = data.get("guest")
    if entries is None:
        entries = data.get("guests", [])
    if not isinstance(entries, list):
        raise ConfigError(f"Expected [[guest]] table array in {path}, got {type(entries).__name__}")

    taken = {u.name.lower() for u in load_users(file_path=path)}
    guests: list[Guest] = []
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ConfigError(f"Guest entry #{idx + 1} is not a valid table")

        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"Guest entry #{idx + 1} must have a non-empty string 'name'")
        name = name.strip()
        if name.lower() in taken:
            raise ConfigError(f"Guest name '{name}' repeats another user or guest name")

        email = entry.get("email")
        if not isinstance(email, str) or "@" not in email:
            raise ConfigError(f"Guest entry '{name}' has invalid email: {email!r} (must contain '@')")

        guests.append(Guest(name=name, email=email.strip()))
        taken.add(name.lower())

    return guests


class UnknownUserError(ConfigError, KeyError):
    """Raised when looking up an unknown user name or email."""
    pass


def name_to_email(
    name: str,
    users: list[User] | None = None,
    file_path: str | Path | None = None,
) -> str:
    """Maps user name to email address from users.toml.

    Raises UnknownUserError if the name is not found.
    """
    if users is None:
        users = load_users(file_path=file_path)

    name_clean = name.strip()
    for u in users:
        if u.name == name_clean:
            return u.email

    name_lower = name_clean.lower()
    for u in users:
        if u.name.lower() == name_lower:
            return u.email

    raise UnknownUserError(f"Vartotojas su vardu '{name}' nerastas")


def email_to_name(
    email: str,
    users: list[User] | None = None,
    file_path: str | Path | None = None,
) -> str:
    """Maps email address to user name from users.toml.

    Raises UnknownUserError if the email is not found.
    """
    if users is None:
        users = load_users(file_path=file_path)

    email_clean = email.strip().lower()
    for u in users:
        if u.email.lower() == email_clean:
            return u.name

    raise UnknownUserError("Vartotojas su nurodytu el. pašto adresu nerastas")


def get_user_by_name(
    name: str,
    users: list[User] | None = None,
    file_path: str | Path | None = None,
) -> User:
    """Returns User object for given name or raises UnknownUserError."""
    if users is None:
        users = load_users(file_path=file_path)

    name_clean = name.strip()
    for u in users:
        if u.name == name_clean:
            return u
    for u in users:
        if u.name.lower() == name_clean.lower():
            return u

    raise UnknownUserError(f"Vartotojas su vardu '{name}' nerastas")


def get_guest_by_name(
    name: str,
    guests: list[Guest] | None = None,
    file_path: str | Path | None = None,
) -> Guest:
    """Returns Guest object for given name (case-insensitive) or raises UnknownUserError."""
    if guests is None:
        guests = load_guests(file_path=file_path)

    name_lower = name.strip().lower()
    for g in guests:
        if g.name.lower() == name_lower:
            return g

    raise UnknownUserError(f"Svečias su vardu '{name}' nerastas")


def get_user_by_email(
    email: str,
    users: list[User] | None = None,
    file_path: str | Path | None = None,
) -> User:
    """Returns User object for given email or raises UnknownUserError."""
    if users is None:
        users = load_users(file_path=file_path)

    email_clean = email.strip().lower()
    for u in users:
        if u.email.lower() == email_clean:
            return u

    raise UnknownUserError("Vartotojas su nurodytu el. pašto adresu nerastas")


def get_user_by_id(
    telegram_id: int,
    users: list[User] | None = None,
    file_path: str | Path | None = None,
) -> User:
    """Returns User object for given telegram_id or raises UnknownUserError."""
    if users is None:
        users = load_users(file_path=file_path)

    for u in users:
        if u.telegram_id == telegram_id:
            return u

    raise UnknownUserError(f"Vartotojas su telegram_id '{telegram_id}' nerastas")


# Lithuanian aliases
vardas_to_email = name_to_email
email_to_vardas = email_to_name

