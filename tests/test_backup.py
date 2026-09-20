"""Tests for backup/backup.py."""

import sqlite3
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from backup.backup import (
    backup_database,
    restore_wal_ownership,
    encrypt_database,
    rotate_old_backups,
    run_backup,
    RETENTION_DAYS,
)


def test_backup_database(tmp_path: Path):
    src_db = tmp_path / "src.db"
    dest_db = tmp_path / "dest.db"

    # Populate source DB
    conn = sqlite3.connect(str(src_db))
    conn.execute("CREATE TABLE items (id INT, name TEXT);")
    conn.execute("INSERT INTO items VALUES (1, 'Vienas'), (2, 'Du');")
    conn.commit()
    conn.close()

    backup_database(src_db, dest_db)

    # Verify dest DB
    assert dest_db.exists()
    dest_conn = sqlite3.connect(str(dest_db))
    cursor = dest_conn.cursor()
    cursor.execute("SELECT id, name FROM items ORDER BY id ASC;")
    rows = cursor.fetchall()
    assert rows == [(1, "Vienas"), (2, "Du")]
    dest_conn.close()


def test_encrypt_database_with_stdin_passphrase(tmp_path: Path):
    plain_file = tmp_path / "data.txt"
    plain_content = b"Labai slapti duomenys 12345"
    plain_file.write_bytes(plain_content)

    gpg_file = tmp_path / "data.txt.gpg"
    passphrase = "ManoSlaptazodis2026"

    encrypt_database(plain_file, gpg_file, passphrase)

    assert gpg_file.exists()
    assert gpg_file.stat().st_size > 0
    assert gpg_file.read_bytes() != plain_content

    # Decrypt with gpg to verify correctness
    decrypted_proc = subprocess.run(
        [
            "gpg",
            "--batch",
            "--yes",
            "--decrypt",
            "--passphrase-fd", "0",
            str(gpg_file),
        ],
        input=passphrase.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    assert decrypted_proc.stdout == plain_content


def test_restore_wal_ownership(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "agent.db"
    db_file.touch()
    wal_file = tmp_path / "agent.db-wal"
    wal_file.touch()
    shm_file = tmp_path / "agent.db-shm"
    shm_file.touch()

    # Mock pwd and grp
    fake_pwd_entry = MagicMock(pw_uid=1004)
    fake_grp_entry = MagicMock(gr_gid=1004)
    monkeypatch.setattr("pwd.getpwnam", lambda name: fake_pwd_entry)
    monkeypatch.setattr("grp.getgrnam", lambda name: fake_grp_entry)

    chowned_files = []

    def fake_chown(path, uid, gid):
        chowned_files.append((path, uid, gid))

    restore_wal_ownership(db_file, user="piagent", group="piagent", chown_fn=fake_chown)

    assert len(chowned_files) == 2
    paths = {f[0] for f in chowned_files}
    assert str(wal_file) in paths
    assert str(shm_file) in paths
    for _, uid, gid in chowned_files:
        assert uid == 1004
        assert gid == 1004


def test_rotate_old_backups():
    folder_id = "test_folder_123"
    now = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)

    # 1. 20 days old (should be deleted)
    t_old = (now - timedelta(days=20)).isoformat()
    # 2. 5 days old (should be kept)
    t_recent = (now - timedelta(days=5)).isoformat()
    # 3. Non-backup file (should be ignored)
    t_other = (now - timedelta(days=25)).isoformat()

    mock_drive = MagicMock()
    mock_files = MagicMock()
    mock_drive.files.return_value = mock_files

    mock_files.list.return_value.execute.return_value = {
        "files": [
            {"id": "file_old", "name": "piagent-backup-20260831.db.gpg", "createdTime": t_old},
            {"id": "file_recent", "name": "piagent-backup-20260915.db.gpg", "createdTime": t_recent},
            {"id": "file_other", "name": "readme.txt", "createdTime": t_other},
        ]
    }
    mock_files.delete.return_value.execute.return_value = {}

    deleted = rotate_old_backups(mock_drive, folder_id=folder_id, retention_days=RETENTION_DAYS, now=now)

    assert deleted == ["file_old"]
    mock_files.delete.assert_called_once_with(fileId="file_old")


def test_run_backup_workflow(tmp_path: Path):
    db_file = tmp_path / "agent.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE t (x INT);")
    conn.execute("INSERT INTO t VALUES (42);")
    conn.commit()
    conn.close()

    mock_drive = MagicMock()
    mock_files = MagicMock()
    mock_drive.files.return_value = mock_files

    mock_create_exec = MagicMock(return_value={"id": "uploaded_id_789"})
    mock_files.create.return_value.execute = mock_create_exec

    mock_list_exec = MagicMock(return_value={"files": []})
    mock_files.list.return_value.execute = mock_list_exec

    result_id = run_backup(
        drive_service=mock_drive,
        db_path_str=str(db_file),
        folder_id="folder_abc",
        passphrase="backup_pass_xyz",
    )

    assert result_id == "uploaded_id_789"
    mock_files.create.assert_called_once()
    create_args = mock_files.create.call_args[1]
    assert create_args["body"]["parents"] == ["folder_abc"]
    assert create_args["body"]["name"].startswith("piagent-backup-")


def test_encrypt_database_never_puts_passphrase_in_argv(tmp_path: Path):
    """The passphrase must reach gpg over stdin: argv is visible in ps to every user."""
    plain_file = tmp_path / "data.txt"
    plain_file.write_bytes(b"Slapti duomenys")
    gpg_file = tmp_path / "data.txt.gpg"
    passphrase = "ManoSlaptazodis2026"

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["input"] = kwargs.get("input")
        result = MagicMock()
        result.returncode = 0
        return result

    with patch("backup.backup.subprocess.run", side_effect=fake_run):
        encrypt_database(plain_file, gpg_file, passphrase)

    cmd = captured["cmd"]

    # The passphrase travels over stdin, not as an argument.
    assert captured["input"] == passphrase.encode("utf-8")
    assert "--passphrase-fd" in cmd
    assert cmd[cmd.index("--passphrase-fd") + 1] == "0"
    assert "--batch" in cmd

    # No argument carries the passphrase, in any form.
    assert "--passphrase" not in cmd
    assert not any(passphrase in str(part) for part in cmd)
