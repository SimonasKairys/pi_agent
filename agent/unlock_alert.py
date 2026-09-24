"""Telegram alert when the encrypted bot data is still locked after a reboot.

Runs once at boot via piagent-unlock-alert.service, which starts only while
/home/piagent/data is not mounted. Reads nothing from the encrypted disk, so
users.toml is unavailable: recipients come from PIAGENT_UNLOCK_ALERT_CHAT_IDS
(comma-separated Telegram IDs) and the language from PIAGENT_UNLOCK_ALERT_LANGUAGE.
Needs only the standard library and agent/i18n.py. Does NOT import agent/telegram_ui.py.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from agent.i18n import t

logger = logging.getLogger(__name__)

DATA_MOUNT = Path("/home/piagent/data")
# Wi-Fi and DNS can lag behind network-online.target: keep trying for about 10 minutes.
MAX_ATTEMPTS = 20
RETRY_DELAY_SECONDS = 30
REQUEST_TIMEOUT_SECONDS = 15


class PermanentSendError(Exception):
    """Telegram rejected the message for a reason a retry does not fix."""


def parse_chat_ids(raw: str | None) -> list[int]:
    """Parses comma-separated Telegram IDs. Raises ValueError on an empty or invalid list."""
    parts = [part.strip() for part in (raw or "").split(",") if part.strip()]
    if not parts:
        raise ValueError("Trūksta aplinkos kintamojo PIAGENT_UNLOCK_ALERT_CHAT_IDS")
    try:
        return [int(part) for part in parts]
    except ValueError:
        raise ValueError(
            "PIAGENT_UNLOCK_ALERT_CHAT_IDS turi būti Telegram ID, atskirti kableliais"
        ) from None


def send_message(token: str, chat_id: int, text: str) -> None:
    """Sends a plain-text message through the Telegram Bot API.

    Error messages never include the request URL, because it contains the bot token.
    """
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    try:
        with urllib.request.urlopen(url, data=data, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            description = json.load(exc).get("description", "")
        except (ValueError, OSError):
            description = ""
        # 429 and 5xx are temporary; other 4xx (blocked bot, unknown chat) are not
        if exc.code == 429 or exc.code >= 500:
            raise OSError(f"HTTP {exc.code} {description}".strip()) from None
        raise PermanentSendError(f"HTTP {exc.code} {description}".strip()) from None
    if not payload.get("ok"):
        raise PermanentSendError(payload.get("description", "Telegram grąžino ok=false"))


def run(
    token: str,
    chat_ids: list[int],
    language: str | None,
    *,
    is_locked: Callable[[], bool] = lambda: not os.path.ismount(DATA_MOUNT),
    send: Callable[[str, int, str], None] = send_message,
    sleep: Callable[[float], None] | None = None,
    max_attempts: int | None = None,
) -> bool:
    """Sends the alert to every chat, retrying network errors.

    Stops early once the disk is unlocked. Returns False when any chat did not get the alert.
    """
    sleep = sleep or time.sleep
    max_attempts = max_attempts or MAX_ATTEMPTS
    text = t(language, "unlock_alert")
    pending = list(chat_ids)
    rejected = False
    for attempt in range(1, max_attempts + 1):
        if not is_locked():
            logger.info("Diskas jau atrakintas, įspėjimas nebereikalingas")
            return True
        for chat_id in list(pending):
            try:
                send(token, chat_id, text)
            except PermanentSendError as exc:
                logger.error("Telegram atmetė įspėjimą gavėjui %s: %s", chat_id, exc)
                pending.remove(chat_id)
                rejected = True
            except OSError as exc:
                logger.warning(
                    "Nepavyko išsiųsti įspėjimo gavėjui %s (bandymas %d/%d): %s",
                    chat_id, attempt, max_attempts, exc,
                )
            else:
                logger.info("Įspėjimas išsiųstas gavėjui %s", chat_id)
                pending.remove(chat_id)
        if not pending:
            return not rejected
        if attempt < max_attempts:
            sleep(RETRY_DELAY_SECONDS)
    return False


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("Trūksta aplinkos kintamojo TELEGRAM_BOT_TOKEN")
        return 1
    try:
        chat_ids = parse_chat_ids(os.environ.get("PIAGENT_UNLOCK_ALERT_CHAT_IDS"))
    except ValueError as exc:
        logger.error("%s", exc)
        return 1
    language = os.environ.get("PIAGENT_UNLOCK_ALERT_LANGUAGE")
    return 0 if run(token, chat_ids, language) else 1


if __name__ == "__main__":
    sys.exit(main())
