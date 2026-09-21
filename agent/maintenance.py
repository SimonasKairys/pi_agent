"""Weekly database and log maintenance for pi_agent.

Runs non-interactively via systemd timer piagent-maintenance.timer (Sundays at 03:15
Europe/Vilnius, between the 03:00 consolidation and the 03:30 backup):
1. Deletes messages older than MESSAGE_RETENTION_DAYS that a summary already covers.
2. Deletes finished approvals older than APPROVAL_RETENTION_DAYS.
3. Drops journal lines older than JOURNAL_RETENTION_DAYS.
4. Checkpoints the WAL, refreshes query statistics, and compacts the file (VACUUM).
Does NOT import agent/telegram_ui.py.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agent.config import get_log_dir
from agent.db import get_connection
from agent.journal import DEFAULT_JOURNAL_FILENAME

logger = logging.getLogger(__name__)

# Retention limits
MESSAGE_RETENTION_DAYS = 120
APPROVAL_RETENTION_DAYS = 30
JOURNAL_RETENTION_DAYS = 90


def _cutoff(now: datetime, days: int) -> str:
    """Returns the UTC ISO timestamp `days` before `now`, in the format the tables use."""
    return (now.astimezone(timezone.utc) - timedelta(days=days)).isoformat()


def delete_old_messages(conn: sqlite3.Connection, now: datetime) -> int:
    """Deletes old messages, but only those already folded into the user's summary.

    Unsummarized messages are kept at any age, so nothing leaves the bot's memory
    without first being summarized.
    """
    cursor = conn.execute(
        """
        DELETE FROM messages
        WHERE created_at < ?
          AND id <= COALESCE(
              (SELECT MAX(s.covers_until_msg_id) FROM summaries s
               WHERE s.user_id = messages.user_id),
              0
          )
        """,
        (_cutoff(now, MESSAGE_RETENTION_DAYS),),
    )
    conn.commit()
    return cursor.rowcount


def delete_old_approvals(conn: sqlite3.Connection, now: datetime) -> int:
    """Deletes approved, rejected, and expired approvals; pending ones are never touched."""
    cursor = conn.execute(
        "DELETE FROM pending_approvals WHERE status != 'pending' AND expires_at < ?",
        (_cutoff(now, APPROVAL_RETENTION_DAYS),),
    )
    conn.commit()
    return cursor.rowcount


def trim_journal(journal_path: Path, now: datetime) -> int:
    """Removes journal lines older than the retention limit and returns how many.

    Lines without a readable timestamp are kept. The file is replaced atomically;
    a line the bot appends during the rewrite can be lost, which is why this runs
    at night.
    """
    if not journal_path.exists():
        return 0

    cutoff = now.astimezone(timezone.utc) - timedelta(days=JOURNAL_RETENTION_DAYS)
    kept: list[str] = []
    removed = 0
    with open(journal_path, encoding="utf-8") as f:
        for line in f:
            try:
                stamp = datetime.fromisoformat(json.loads(line)["timestamp"])
                if stamp.tzinfo is not None and stamp < cutoff:
                    removed += 1
                    continue
            except (ValueError, KeyError, TypeError):
                pass
            kept.append(line)

    if removed:
        tmp_path = journal_path.with_suffix(journal_path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.writelines(kept)
        os.replace(tmp_path, journal_path)
    return removed


def compact_database(conn: sqlite3.Connection) -> None:
    """Checkpoints the WAL, refreshes query statistics, and rebuilds the file."""
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    conn.execute("PRAGMA optimize;")
    conn.execute("VACUUM;")


def run_maintenance(
    conn: sqlite3.Connection,
    journal_path: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Runs every maintenance step and returns what each one removed."""
    now = now or datetime.now(timezone.utc)
    result = {
        "messages": delete_old_messages(conn, now),
        "approvals": delete_old_approvals(conn, now),
        "journal_lines": trim_journal(journal_path, now),
    }
    compact_database(conn)
    return result


def main() -> None:
    """CLI entry point for systemd timer piagent-maintenance.service."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Pradedama savaitinė duomenų priežiūra...")
    conn = get_connection()
    try:
        result = run_maintenance(conn, get_log_dir() / DEFAULT_JOURNAL_FILENAME)
    finally:
        conn.close()
    logger.info(
        "Duomenų priežiūra baigta. Ištrinta žinučių: %d, patvirtinimų: %d, žurnalo eilučių: %d",
        result["messages"],
        result["approvals"],
        result["journal_lines"],
    )


if __name__ == "__main__":
    main()
