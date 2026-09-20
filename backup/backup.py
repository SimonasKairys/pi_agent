"""Šifruotas SQLite atsarginių kopijų kūrimo ir įkėlimo į Google Drive skriptas.

ŠALTINIS: iš šio katalogo niekada nepaleidžiamas tiesiogiai. Administratorius jį
nukopijuoja į /usr/local/lib/piagent-backup/ su atskira venv ir systemd laikmačiu.
Neturi jokių priklausomybių nuo agent/ kodo.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("piagent-backup")

DEFAULT_DB_PATH = "/home/piagent/data/agent.db"
RETENTION_DAYS = 14


def backup_database(src_db_path: Path, dest_db_path: Path) -> None:
    """Creates a consistent backup copy of SQLite database using sqlite3.Connection.backup()."""
    if not src_db_path.exists():
        raise FileNotFoundError(f"Šaltinio duomenų bazė nerasta: {src_db_path}")

    dest_db_path.parent.mkdir(parents=True, exist_ok=True)

    src_conn = sqlite3.connect(str(src_db_path))
    dest_conn = sqlite3.connect(str(dest_db_path))
    try:
        src_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        src_conn.close()


def restore_wal_ownership(
    db_path: Path,
    user: str = "piagent",
    group: str = "piagent",
    chown_fn: Any = os.chown,
) -> None:
    """Restores ownership of -wal and -shm files to piagent:piagent if created by root."""
    try:
        import pwd
        import grp
        uid = pwd.getpwnam(user).pw_uid
        gid = grp.getgrnam(group).gr_gid
    except (KeyError, ImportError):
        logger.warning("Vartotojas ar grupė '%s' nerasta sistemoje, praleidžiama chown", user)
        return

    for ext in ["-wal", "-shm"]:
        side_file = Path(f"{db_path}{ext}")
        if side_file.exists():
            try:
                chown_fn(str(side_file), uid, gid)
                logger.info("Atstatyta failo %s nuosavybė į %s:%s", side_file, user, group)
            except PermissionError:
                logger.warning("Nepavyko pakeisti failo %s nuosavybės (PermissionError)", side_file)


def encrypt_database(
    src_file: Path,
    dest_gpg_file: Path,
    passphrase: str,
) -> None:
    """Encrypts file symmetrically with gpg using --passphrase-fd 0 (avoids ps exposure)."""
    if not src_file.exists():
        raise FileNotFoundError(f"Šifruojamas failas nerastas: {src_file}")

    dest_gpg_file.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "gpg",
        "--batch",
        "--yes",
        "--symmetric",
        "--cipher-algo", "AES256",
        "--passphrase-fd", "0",
        "--output", str(dest_gpg_file),
        str(src_file),
    ]

    proc = subprocess.run(
        cmd,
        input=passphrase.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    if proc.returncode != 0:
        err_msg = proc.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"GPG šifravimo klaida (kodas {proc.returncode}): {err_msg}")


def get_drive_service(
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> Any:
    """Initializes Google Drive API v3 client using refresh token."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/drive.file"],
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def upload_backup(
    drive_service: Any,
    file_path: Path,
    folder_id: str,
    file_name: str | None = None,
    media: Any = None,
) -> str:
    """Uploads file to specified Google Drive folder."""
    upload_name = file_name or file_path.name
    if media is None:
        try:
            from googleapiclient.http import MediaFileUpload
            media = MediaFileUpload(str(file_path), mimetype="application/octet-stream", resumable=True)
        except ImportError:
            media = str(file_path)

    body = {
        "name": upload_name,
        "parents": [folder_id],
    }

    result = drive_service.files().create(body=body, media_body=media, fields="id").execute()
    file_id = result.get("id")
    logger.info("Kopija įkelta į Google Drive, failo ID: %s", file_id)
    return str(file_id)



def rotate_old_backups(
    drive_service: Any,
    folder_id: str,
    retention_days: int = RETENTION_DAYS,
    now: datetime | None = None,
) -> list[str]:
    """Deletes backups in Google Drive folder older than retention_days."""
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff_time = now - timedelta(days=retention_days)

    query = f"'{folder_id}' in parents and trashed = false"
    response = drive_service.files().list(
        q=query,
        fields="files(id, name, createdTime)",
    ).execute()

    deleted_ids: list[str] = []
    files = response.get("files", [])
    for f in files:
        file_id = f["id"]
        file_name = f.get("name", "")
        created_str = f.get("createdTime")

        if not file_name.startswith("piagent-backup-"):
            continue

        if created_str:
            created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            if created_dt < cutoff_time:
                logger.info("Trinama sena kopija %s (ID: %s, sukurta: %s)", file_name, file_id, created_str)
                drive_service.files().delete(fileId=file_id).execute()
                deleted_ids.append(file_id)

    return deleted_ids


def run_backup(
    drive_service: Any | None = None,
    db_path_str: str | None = None,
    folder_id: str | None = None,
    passphrase: str | None = None,
) -> str:
    """Coordinates full backup workflow: copy, restore WAL, encrypt, upload, rotate."""
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    refresh_token = os.environ.get("GOOGLE_REFRESH_TOKEN")
    folder_id = folder_id or os.environ.get("BACKUP_FOLDER_ID")
    passphrase = passphrase or os.environ.get("BACKUP_PASSPHRASE")

    if not passphrase:
        raise ValueError("Trūksta BACKUP_PASSPHRASE aplinkos kintamojo")
    if not folder_id:
        raise ValueError("Trūksta BACKUP_FOLDER_ID aplinkos kintamojo")

    db_path = Path(db_path_str or os.environ.get("PIAGENT_DB_PATH", DEFAULT_DB_PATH))
    if not db_path.exists():
        raise FileNotFoundError(f"Duomenų bazė nerasta: {db_path}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_filename = f"piagent-backup-{timestamp}.db.gpg"

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        raw_copy = tmp_path / "agent.db"
        encrypted_copy = tmp_path / backup_filename

        logger.info("Daroma SQLite kopija iš %s...", db_path)
        backup_database(db_path, raw_copy)

        # Ensure WAL / SHM permissions remain piagent:piagent
        restore_wal_ownership(db_path)

        logger.info("Šifruojama kopija per gpg...")
        encrypt_database(raw_copy, encrypted_copy, passphrase)

        if drive_service is None:
            if not client_id or not client_secret or not refresh_token:
                raise ValueError("Trūksta Google Drive OAuth kredencialų")
            drive_service = get_drive_service(client_id, client_secret, refresh_token)

        logger.info("Įkeliama kopija %s į Drive...", backup_filename)
        uploaded_id = upload_backup(drive_service, encrypted_copy, folder_id, backup_filename)

        logger.info("Valomos senos kopijos (senesnės nei %d d.)...", RETENTION_DAYS)
        rotate_old_backups(drive_service, folder_id, RETENTION_DAYS)

    logger.info("Atsarginė kopija sėkmingai atlikta.")
    return uploaded_id


if __name__ == "__main__":
    run_backup()
