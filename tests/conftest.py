"""Shared pytest fixtures."""

import pytest


@pytest.fixture(autouse=True)
def isolate_journal(tmp_path, monkeypatch):
    """Redirects the journal to a temporary directory for every test.

    run_loop() writes journal entries through PIAGENT_LOG_DIR. Without this,
    test runs append to the real development journal in dev/logs/.
    """
    monkeypatch.setenv("PIAGENT_LOG_DIR", str(tmp_path / "logs"))
