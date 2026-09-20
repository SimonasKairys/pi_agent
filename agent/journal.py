"""JSONL logging journal for pi_agent.

Records run_id, user_id, tool_name, tokens, and cost_usd into a JSONL log file
in the directory configured by PIAGENT_LOG_DIR.
Scrubs API keys and email addresses (preventing '@' from appearing in email locations).
Calculates cost from model usage per TASK.md rates.
"""

from __future__ import annotations

import json
import os
import re
import uuid
import zoneinfo
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.config import get_log_dir
from agent.db import calculate_cost

DEFAULT_JOURNAL_FILENAME = "journal.jsonl"
LOG_TIMEZONE = "Europe/Vilnius"

# Regex patterns for sanitization
EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)
GENERIC_AT_PATTERN = re.compile(r"\S+@\S+")

API_KEY_PATTERNS = [
    re.compile(r"\b(?:sk|tvly|comp)-[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{10,}\b", re.IGNORECASE),
    re.compile(r"\b[0-9]{8,10}:[A-Za-z0-9_-]{30,}\b"),  # Telegram bot token
]

TOKEN_COUNT_FIELDS = {
    "tokens",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
}

SENSITIVE_FIELD_NAMES = {
    "api_key",
    "token",
    "secret",
    "password",
    "passphrase",
    "authorization",
    "auth",
    "bot_token",
    "access_token",
    "refresh_token",
    "api_token",
}


def is_sensitive_key(key_name: str) -> bool:
    """Checks whether a dictionary key represents an authentication secret."""
    lower = key_name.lower()
    if lower in TOKEN_COUNT_FIELDS:
        return False
    if lower in SENSITIVE_FIELD_NAMES:
        return True
    if lower.endswith("_key") or lower.endswith("_secret") or lower.endswith("_token"):
        return True
    return False


def sanitize_text(text: str) -> str:
    """Removes email addresses and known API key patterns from text.

    Replaces email addresses ensuring no '@' remains in email contexts.
    """
    if not isinstance(text, str):
        return text

    cleaned = text

    # Mask known environment secret values if set and sufficiently long
    for env_var in (
        "OPENROUTER_API_KEY",
        "TAVILY_API_KEY",
        "COMPOSIO_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "BACKUP_PASSPHRASE",
        "GOOGLE_CLIENT_SECRET",
        "GOOGLE_REFRESH_TOKEN",
    ):
        secret_val = os.environ.get(env_var)
        if secret_val and len(secret_val) > 5 and secret_val in cleaned:
            cleaned = cleaned.replace(secret_val, "[raktas_pašalintas]")

    # Mask regex-matched API keys
    for pattern in API_KEY_PATTERNS:
        cleaned = pattern.sub("[raktas_pašalintas]", cleaned)

    # Mask emails
    cleaned = EMAIL_PATTERN.sub("[el. paštas pašalintas]", cleaned)
    cleaned = GENERIC_AT_PATTERN.sub("[el. paštas pašalintas]", cleaned)

    return cleaned


def sanitize_data(data: Any) -> Any:
    """Recursively sanitizes nested dictionaries, lists, and primitives."""
    if isinstance(data, str):
        return sanitize_text(data)
    if isinstance(data, dict):
        sanitized_dict: dict[str, Any] = {}
        for k, v in data.items():
            k_str = str(k)
            if is_sensitive_key(k_str):
                sanitized_dict[k_str] = "[paslėpta]"
            else:
                sanitized_dict[k_str] = sanitize_data(v)
        return sanitized_dict
    if isinstance(data, list):
        return [sanitize_data(item) for item in data]
    return data


@dataclass
class JournalEntry:
    """Structure of an audit journal entry."""

    run_id: str
    user_id: int
    tool_name: str | None
    tokens: int
    cost_usd: float
    timestamp: str | None = None
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Converts entry to dictionary, omitting None values for details if empty."""
        tz = zoneinfo.ZoneInfo(LOG_TIMEZONE)
        ts = self.timestamp or datetime.now(tz).isoformat()
        res: dict[str, Any] = {
            "timestamp": ts,
            "run_id": str(self.run_id),
            "user_id": int(self.user_id),
            "tool_name": self.tool_name,
            "tokens": int(self.tokens),
            "cost_usd": round(float(self.cost_usd), 6),
        }
        if self.details:
            res["details"] = self.details
        return res


def get_journal_path(
    log_dir: str | Path | None = None,
    filename: str = DEFAULT_JOURNAL_FILENAME,
) -> Path:
    """Returns absolute path to the journal file and ensures parent directory exists."""
    directory = Path(log_dir) if log_dir is not None else get_log_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory / filename


def record_journal_entry(
    run_id: str | None = None,
    user_id: int = 0,
    tool_name: str | None = None,
    tokens: int = 0,
    cost_usd: float = 0.0,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    details: dict[str, Any] | None = None,
    log_dir: str | Path | None = None,
    file_path: str | Path | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Records an entry to the JSONL journal.

    - Automatically calculates cost_usd if tokens/usage are provided and cost is 0.0.
    - Scrubs any sensitive keys and email addresses.
    - Appends entry as a single JSON line to target file.
    - Returns the sanitized entry dictionary.
    """
    if run_id is None:
        run_id = f"run_{uuid.uuid4().hex[:12]}"

    if cost_usd == 0.0 and (prompt_tokens > 0 or completion_tokens > 0):
        cost_usd = calculate_cost(prompt_tokens, completion_tokens)

    if tokens == 0 and (prompt_tokens > 0 or completion_tokens > 0):
        tokens = prompt_tokens + completion_tokens

    entry = JournalEntry(
        run_id=run_id,
        user_id=user_id,
        tool_name=tool_name,
        tokens=tokens,
        cost_usd=cost_usd,
        timestamp=timestamp,
        details=details,
    )

    entry_dict = entry.to_dict()
    sanitized = sanitize_data(entry_dict)

    target_path = Path(file_path) if file_path is not None else get_journal_path(log_dir=log_dir)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    # sanitize_data() already scrubbed every string value. Re-running sanitize_text()
    # over the serialized line would let \S+@\S+ eat the quotes around any value
    # starting with '@', writing a corrupt line that never parses back.
    json_line = json.dumps(sanitized, ensure_ascii=False)

    with open(target_path, "a", encoding="utf-8") as f:
        f.write(json_line + "\n")

    return json.loads(json_line)


def read_journal_entries(
    file_path: str | Path | None = None,
    log_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Reads and parses all JSON lines from the journal file.

    Returns an empty list if file does not exist.
    """
    target_path = Path(file_path) if file_path is not None else get_journal_path(log_dir=log_dir)
    if not target_path.exists():
        return []

    entries: list[dict[str, Any]] = []
    with open(target_path, "r", encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if not line_str:
                continue
            try:
                entries.append(json.loads(line_str))
            except json.JSONDecodeError:
                continue
    return entries
