"""Tests for backup/authorize.py."""

import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

import backup.authorize as authorize_module
from backup.authorize import (
    HEADLESS_REDIRECT_URI,
    SCOPES,
    authorize,
    client_config,
    main,
    read_env_value,
    update_env_file,
)

CREDS = SimpleNamespace(client_id="id.apps.googleusercontent.com", client_secret="secret",
                        refresh_token="1//refresh")


class FakeFlow:
    calls = []
    refresh_token = "1//refresh"

    @classmethod
    def from_client_secrets_file(cls, path, scopes):
        cls.calls.append(("file", path, scopes))
        return cls()

    @classmethod
    def from_client_config(cls, config, scopes):
        cls.calls.append(("config", config, scopes))
        return cls()

    @property
    def credentials(self):
        return SimpleNamespace(**{**vars(CREDS), "refresh_token": self.refresh_token})

    def run_local_server(self, **kwargs):
        self.calls.append(("server", kwargs))
        return self.credentials

    def authorization_url(self, **kwargs):
        self.calls.append(("auth_url", self.redirect_uri, kwargs))
        return "https://accounts.google.com/o/oauth2/auth?x=1", "state"

    def fetch_token(self, authorization_response):
        self.calls.append(("fetch", authorization_response))


@pytest.fixture(autouse=True)
def reset_flow():
    FakeFlow.calls = []
    FakeFlow.refresh_token = "1//refresh"


def test_authorize_requests_drive_file_offline_access():
    values = authorize("client_secret.json", flow_class=FakeFlow)
    assert values == {
        "GOOGLE_CLIENT_ID": "id.apps.googleusercontent.com",
        "GOOGLE_CLIENT_SECRET": "secret",
        "GOOGLE_REFRESH_TOKEN": "1//refresh",
    }
    assert FakeFlow.calls[0] == ("file", "client_secret.json", SCOPES)
    assert SCOPES == ["https://www.googleapis.com/auth/drive.file"]
    kwargs = FakeFlow.calls[1][1]
    assert kwargs["access_type"] == "offline"
    assert kwargs["prompt"] == "consent"


def test_authorize_fails_without_refresh_token():
    FakeFlow.refresh_token = None
    with pytest.raises(RuntimeError, match="refresh token"):
        authorize("client_secret.json", flow_class=FakeFlow)


def test_authorize_with_client_id_and_secret():
    config = client_config("id.apps.googleusercontent.com", "secret")
    authorize(config=config, flow_class=FakeFlow)
    assert FakeFlow.calls[0] == ("config", config, SCOPES)
    assert config["installed"]["token_uri"] == "https://oauth2.googleapis.com/token"


def test_headless_uses_the_pasted_redirect_address():
    pasted = "http://localhost:8765/?state=s&code=4/abc&scope=x"
    values = authorize(config=client_config("id", "secret"), flow_class=FakeFlow,
                       headless=True, read=lambda prompt: f"  {pasted}\n")
    assert values["GOOGLE_REFRESH_TOKEN"] == "1//refresh"
    assert FakeFlow.calls[1] == ("auth_url", HEADLESS_REDIRECT_URI,
                                 {"access_type": "offline", "prompt": "consent"})
    assert FakeFlow.calls[2] == ("fetch", "https://localhost:8765/?state=s&code=4/abc&scope=x")


def test_headless_rejects_an_address_without_code():
    with pytest.raises(RuntimeError, match="code="):
        authorize(config=client_config("id", "secret"), flow_class=FakeFlow,
                  headless=True, read=lambda prompt: "http://localhost:8765/?error=access_denied")


def test_update_env_file_replaces_keys_and_keeps_other_lines(tmp_path: Path):
    env = tmp_path / "backup.env"
    env.write_text("GOOGLE_CLIENT_ID=id\nGOOGLE_CLIENT_SECRET=old\nGOOGLE_REFRESH_TOKEN=old\n"
                   "BACKUP_FOLDER_ID=folder\nPIAGENT_DB_PATH=/db\n")
    env.chmod(0o644)
    update_env_file(env, {"GOOGLE_CLIENT_ID": "id", "GOOGLE_CLIENT_SECRET": "new",
                          "GOOGLE_REFRESH_TOKEN": "1//new"})
    assert env.read_text() == ("GOOGLE_CLIENT_ID=id\nGOOGLE_CLIENT_SECRET=new\n"
                               "GOOGLE_REFRESH_TOKEN=1//new\nBACKUP_FOLDER_ID=folder\n"
                               "PIAGENT_DB_PATH=/db\n")
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert not (tmp_path / "backup.env.tmp").exists()


def test_update_env_file_appends_missing_keys(tmp_path: Path):
    env = tmp_path / "backup.env"
    env.write_text("BACKUP_FOLDER_ID=folder\n")
    update_env_file(env, {"GOOGLE_REFRESH_TOKEN": "1//new"})
    assert env.read_text() == "BACKUP_FOLDER_ID=folder\nGOOGLE_REFRESH_TOKEN=1//new\n"


def test_read_env_value(tmp_path: Path):
    env = tmp_path / "backup.env"
    env.write_text("# comment\nGOOGLE_CLIENT_ID=id.apps.googleusercontent.com\n")
    assert read_env_value(env, "GOOGLE_CLIENT_ID") == "id.apps.googleusercontent.com"
    assert read_env_value(env, "MISSING") == ""


def test_main_prompts_for_the_secret_without_echo(monkeypatch, capsys):
    captured = {}

    def fake_authorize(config, headless):
        captured.update(config=config, headless=headless)
        return {"GOOGLE_REFRESH_TOKEN": "1//refresh"}

    monkeypatch.setattr("builtins.input", lambda prompt: " id.apps.googleusercontent.com ")
    monkeypatch.setattr(authorize_module.getpass, "getpass", lambda prompt: "secret")
    monkeypatch.setattr(authorize_module, "authorize", fake_authorize)
    assert main(["authorize.py"]) == 0
    assert captured == {"config": client_config("id.apps.googleusercontent.com", "secret"),
                        "headless": False}
    assert "GOOGLE_REFRESH_TOKEN=1//refresh" in capsys.readouterr().out


def test_main_env_file_keeps_client_id_and_writes_keys(monkeypatch, tmp_path: Path, capsys):
    env = tmp_path / "backup.env"
    env.write_text("GOOGLE_CLIENT_ID=id\nGOOGLE_CLIENT_SECRET=old\nGOOGLE_REFRESH_TOKEN=old\n")
    captured = {}

    def fake_authorize(config, headless):
        captured.update(config=config, headless=headless)
        return {"GOOGLE_CLIENT_ID": "id", "GOOGLE_CLIENT_SECRET": "new",
                "GOOGLE_REFRESH_TOKEN": "1//new"}

    monkeypatch.setattr("builtins.input", lambda prompt: "")
    monkeypatch.setattr(authorize_module.getpass, "getpass", lambda prompt: "new")
    monkeypatch.setattr(authorize_module, "authorize", fake_authorize)
    assert main(["authorize.py", "--headless", "--env-file", str(env)]) == 0
    assert captured == {"config": client_config("id", "new"), "headless": True}
    assert "GOOGLE_REFRESH_TOKEN=1//new" in env.read_text()
    assert "1//new" not in capsys.readouterr().out


def test_main_rejects_missing_env_file(tmp_path: Path):
    assert main(["authorize.py", "--env-file", str(tmp_path / "missing.env")]) == 2


def test_main_rejects_empty_secret(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: "id")
    monkeypatch.setattr(authorize_module.getpass, "getpass", lambda prompt: "")
    assert main(["authorize.py"]) == 2
