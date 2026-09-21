"""Tests for agent/journal.py."""

from pathlib import Path
import pytest

from agent.journal import (
    calculate_cost,
    get_journal_path,
    read_journal_entries,
    record_journal_entry,
)


def test_record_journal_entry_basic(tmp_path: Path):
    log_file = tmp_path / "test_journal.jsonl"
    entry = record_journal_entry(
        run_id="run_test_123",
        user_id=111,
        tool_name="search_web",
        tokens=150,
        cost_usd=0.00012,
        file_path=log_file,
    )

    assert entry["run_id"] == "run_test_123"
    assert entry["user_id"] == 111
    assert entry["tool_name"] == "search_web"
    assert entry["tokens"] == 150
    assert entry["cost_usd"] == 0.00012
    assert "timestamp" in entry

    # Verify file content
    assert log_file.exists()
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1


def test_journal_entry_scrubs_email_addresses(tmp_path: Path):
    """Recorded line must not contain '@' in email contexts."""
    log_file = tmp_path / "email_test.jsonl"
    raw_query = "Surask informaciją apie vartotoją simonas@example.com arba test.user+tag@domain.co.uk"

    record_journal_entry(
        run_id="run_email_1",
        user_id=111,
        tool_name="search_web",
        tokens=100,
        cost_usd=0.00005,
        details={"query": raw_query, "contact": "admin@piagent.local"},
        file_path=log_file,
    )

    raw_content = log_file.read_text(encoding="utf-8")

    # The character '@' must NOT appear in the written file
    assert "@" not in raw_content
    assert "simonas@example.com" not in raw_content
    assert "admin@piagent.local" not in raw_content
    assert "[el. paštas pašalintas]" in raw_content


def test_journal_entry_scrubs_api_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    log_file = tmp_path / "keys_test.jsonl"
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-secret-test-key-abcdef123456")

    record_journal_entry(
        run_id="run_keys_1",
        user_id=111,
        tool_name="llm_call",
        tokens=50,
        cost_usd=0.00001,
        details={
            "api_key": "sensitive-value-here",
            "message": "Calling with key sk-or-v1-secret-test-key-abcdef123456",
        },
        file_path=log_file,
    )

    raw_content = log_file.read_text(encoding="utf-8")
    assert "sk-or-v1-secret-test-key-abcdef123456" not in raw_content
    assert "sensitive-value-here" not in raw_content
    assert "[raktas_pašalintas]" in raw_content or "[paslėpta]" in raw_content


def test_cost_calculated_from_usage(tmp_path: Path):
    log_file = tmp_path / "calc_test.jsonl"
    # Rates: 0.15 USD / 1M prompt, 0.60 USD / 1M completion
    # 1000 prompt tokens = 0.00015
    # 500 completion tokens = 0.00030
    # Total cost = 0.00045 USD
    expected_cost = calculate_cost(prompt_tokens=1000, completion_tokens=500)
    assert abs(expected_cost - 0.00045) < 1e-9

    entry = record_journal_entry(
        run_id="run_calc_1",
        user_id=222,
        tool_name=None,
        prompt_tokens=1000,
        completion_tokens=500,
        file_path=log_file,
    )

    assert entry["tokens"] == 1500
    assert abs(entry["cost_usd"] - 0.00045) < 1e-9


def test_read_journal_entries(tmp_path: Path):
    log_file = tmp_path / "multi_test.jsonl"
    for i in range(3):
        record_journal_entry(
            run_id=f"run_{i}",
            user_id=100 + i,
            tool_name=f"tool_{i}",
            tokens=10 * (i + 1),
            cost_usd=0.0001 * (i + 1),
            file_path=log_file,
        )

    entries = read_journal_entries(file_path=log_file)
    assert len(entries) == 3
    assert entries[0]["run_id"] == "run_0"
    assert entries[1]["run_id"] == "run_1"
    assert entries[2]["run_id"] == "run_2"
    assert entries[2]["tokens"] == 30


def test_journal_respects_piagent_log_dir_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    custom_log_dir = tmp_path / "custom_logs"
    monkeypatch.setenv("PIAGENT_LOG_DIR", str(custom_log_dir))

    entry = record_journal_entry(
        run_id="run_env_test",
        user_id=333,
        tool_name="search_web",
        tokens=42,
        cost_usd=0.00002,
    )

    expected_file = custom_log_dir / "journal.jsonl"
    assert expected_file.exists()
    assert get_journal_path() == expected_file
    assert entry["user_id"] == 333


def test_journal_line_survives_at_sign_in_argument(tmp_path: Path):
    """A '@' inside a tool argument must not corrupt the JSON line."""
    log_file = tmp_path / "journal.jsonl"

    for query in ["@nasa naujienos", "adresas baigiasi @", "rasyk jonas@example.com"]:
        record_journal_entry(
            run_id="run_at",
            user_id=111,
            tool_name="search_web",
            details={"arguments": {"query": query}},
            file_path=log_file,
        )

    entries = read_journal_entries(file_path=log_file)

    # Every written line must parse back.
    assert len(entries) == 3
    assert sum(1 for _ in open(log_file, encoding="utf-8")) == 3

    # The '@' that starts a value survives; the real address does not.
    assert entries[0]["details"]["arguments"]["query"] == "@nasa naujienos"
    assert "jonas@example.com" not in entries[2]["details"]["arguments"]["query"]
