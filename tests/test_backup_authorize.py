"""Tests for backup/authorize.py."""

from types import SimpleNamespace

import pytest

import backup.authorize as authorize_module
from backup.authorize import SCOPES, authorize, client_config, main


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

    def run_local_server(self, **kwargs):
        self.calls.append(("server", kwargs))
        return SimpleNamespace(client_id="id.apps.googleusercontent.com",
                               client_secret="secret", refresh_token=self.refresh_token)


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
    assert config["installed"]["client_secret"] == "secret"
    assert config["installed"]["token_uri"] == "https://oauth2.googleapis.com/token"


def test_main_prompts_for_the_secret_without_echo(monkeypatch, capsys):
    captured = {}

    def fake_authorize(config):
        captured["config"] = config
        return {"GOOGLE_REFRESH_TOKEN": "1//refresh"}

    monkeypatch.setattr("builtins.input", lambda prompt: " id.apps.googleusercontent.com ")
    monkeypatch.setattr(authorize_module.getpass, "getpass", lambda prompt: "secret")
    monkeypatch.setattr(authorize_module, "authorize", fake_authorize)
    assert main(["authorize.py"]) == 0
    assert captured["config"] == client_config("id.apps.googleusercontent.com", "secret")
    assert "GOOGLE_REFRESH_TOKEN=1//refresh" in capsys.readouterr().out


def test_main_rejects_empty_secret(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: "id")
    monkeypatch.setattr(authorize_module.getpass, "getpass", lambda prompt: "")
    assert main(["authorize.py"]) == 2


def test_main_rejects_extra_arguments(capsys):
    assert main(["authorize.py", "a.json", "b"]) == 2
    assert "Naudojimas" in capsys.readouterr().err
