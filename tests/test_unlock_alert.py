"""Tests for agent/unlock_alert.py."""

import io
import json
import logging
import urllib.error
import urllib.parse

import pytest

import agent.unlock_alert as unlock_alert
from agent.i18n import t
from agent.unlock_alert import PermanentSendError, main, parse_chat_ids, run, send_message

TOKEN = "123456:SECRET-TOKEN"


class FakeSend:
    """Records sends and fails according to a per-chat list of outcomes."""

    def __init__(self, outcomes=None):
        self.outcomes = outcomes or {}
        self.calls = []

    def __call__(self, token, chat_id, text):
        self.calls.append((token, chat_id, text))
        queue = self.outcomes.get(chat_id)
        if queue:
            error = queue.pop(0)
            if error is not None:
                raise error


def _run(send, *, locked=True, max_attempts=3, language="lt"):
    sleeps = []
    ok = run(
        TOKEN, [1001, 2002], language,
        is_locked=(locked if callable(locked) else lambda: locked),
        send=send, sleep=sleeps.append, max_attempts=max_attempts,
    )
    return ok, sleeps


def test_parse_chat_ids():
    assert parse_chat_ids(" 1001, 2002 ,") == [1001, 2002]


@pytest.mark.parametrize("raw", [None, "", " , ", "1001,abc"])
def test_parse_chat_ids_rejects_bad_input(raw):
    with pytest.raises(ValueError):
        parse_chat_ids(raw)


def test_sends_to_every_chat_in_the_chosen_language():
    send = FakeSend()
    ok, sleeps = _run(send, language="en")
    assert ok
    assert sleeps == []
    assert [call[1] for call in send.calls] == [1001, 2002]
    assert all(call[2] == t("en", "unlock_alert") for call in send.calls)


def test_retries_only_chats_with_network_errors():
    send = FakeSend({2002: [OSError("no route"), None]})
    ok, sleeps = _run(send)
    assert ok
    assert [call[1] for call in send.calls] == [1001, 2002, 2002]
    assert sleeps == [unlock_alert.RETRY_DELAY_SECONDS]


def test_gives_up_after_max_attempts():
    send = FakeSend({1001: [OSError("down")] * 3, 2002: [OSError("down")] * 3})
    ok, sleeps = _run(send, max_attempts=3)
    assert not ok
    assert len(send.calls) == 6
    assert len(sleeps) == 2


def test_permanent_error_is_not_retried_and_fails_the_run():
    send = FakeSend({1001: [PermanentSendError("HTTP 403 Forbidden: bot was blocked by the user")]})
    ok, sleeps = _run(send)
    assert not ok
    assert [call[1] for call in send.calls] == [1001, 2002]
    assert sleeps == []


def test_nothing_is_sent_when_the_disk_is_unlocked():
    send = FakeSend()
    ok, _ = _run(send, locked=False)
    assert ok
    assert send.calls == []


def test_stops_retrying_once_the_disk_is_unlocked():
    states = iter([True, False])
    send = FakeSend({1001: [OSError("down")], 2002: [OSError("down")]})
    ok, sleeps = _run(send, locked=lambda: next(states))
    assert ok
    assert len(send.calls) == 2
    assert len(sleeps) == 1


def _http_error(code, description):
    body = io.BytesIO(json.dumps({"ok": False, "description": description}).encode())
    return urllib.error.HTTPError("https://api.telegram.org/bot" + TOKEN, code, "x", {}, body)


def test_send_message_posts_plain_text(monkeypatch):
    requests = []

    def fake_urlopen(url, data, timeout):
        requests.append((url, urllib.parse.parse_qs(data.decode())))
        return io.BytesIO(b'{"ok": true}')

    monkeypatch.setattr(unlock_alert.urllib.request, "urlopen", fake_urlopen)
    send_message(TOKEN, 1001, "Sveiki")
    url, fields = requests[0]
    assert url == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert fields == {"chat_id": ["1001"], "text": ["Sveiki"]}


@pytest.mark.parametrize(
    ("code", "expected"),
    [(403, PermanentSendError), (400, PermanentSendError), (429, OSError), (502, OSError)],
)
def test_send_message_classifies_http_errors_without_leaking_the_token(monkeypatch, code, expected):
    def fake_urlopen(url, data, timeout):
        raise _http_error(code, "Some description")

    monkeypatch.setattr(unlock_alert.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(expected) as info:
        send_message(TOKEN, 1001, "Sveiki")
    assert info.type is expected
    assert "Some description" in str(info.value)
    assert TOKEN not in str(info.value)
    assert info.value.__cause__ is None


def test_main_requires_chat_ids(monkeypatch, caplog):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.delenv("PIAGENT_UNLOCK_ALERT_CHAT_IDS", raising=False)
    with caplog.at_level(logging.ERROR):
        assert main() == 1
    assert "PIAGENT_UNLOCK_ALERT_CHAT_IDS" in caplog.text


def test_main_does_not_log_the_token(monkeypatch, caplog):
    def fake_urlopen(url, data, timeout):
        raise urllib.error.URLError("Temporary failure in name resolution")

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("PIAGENT_UNLOCK_ALERT_CHAT_IDS", "1001")
    monkeypatch.setattr(unlock_alert.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(unlock_alert, "MAX_ATTEMPTS", 2)
    monkeypatch.setattr(unlock_alert.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(unlock_alert.os.path, "ismount", lambda path: False)
    with caplog.at_level(logging.INFO):
        assert main() == 1
    assert "name resolution" in caplog.text
    assert TOKEN not in caplog.text
