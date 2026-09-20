"""Tests for scripts/metrics.py.

Verifies calculation of task success rate, tool accuracy, and per-user cost
from JSONL journal files.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.metrics import compute_metrics


def test_metrics_empty_file(tmp_path: Path):
    log_file = tmp_path / "empty.jsonl"
    log_file.touch()

    metrics = compute_metrics(log_file)
    assert metrics["total_entries"] == 0
    assert metrics["task_success"]["total_runs"] == 0
    assert metrics["task_success"]["success_rate_percent"] == 0.0
    assert metrics["tool_usage"]["total_tool_calls"] == 0
    assert metrics["cost_summary"]["total_cost_usd"] == 0.0


def test_metrics_nonexistent_file(tmp_path: Path):
    log_file = tmp_path / "nonexistent.jsonl"
    metrics = compute_metrics(log_file)
    assert metrics["total_entries"] == 0


def test_metrics_calculation(tmp_path: Path):
    log_file = tmp_path / "sample.jsonl"

    entries = [
        # Run 1: User 111, search_web + model reply (success)
        {
            "timestamp": "2026-09-20T12:00:00+03:00",
            "run_id": "run_01",
            "user_id": 111,
            "tool_name": "search_web",
            "tokens": 0,
            "cost_usd": 0.0,
            "details": {"arguments": {"query": "Vilnius"}},
        },
        {
            "timestamp": "2026-09-20T12:00:02+03:00",
            "run_id": "run_01",
            "user_id": 111,
            "tool_name": None,
            "tokens": 150,
            "cost_usd": 0.00005,
        },
        # Run 2: User 222, create_event + model reply (success)
        {
            "timestamp": "2026-09-20T12:05:00+03:00",
            "run_id": "run_02",
            "user_id": 222,
            "tool_name": "create_event",
            "tokens": 0,
            "cost_usd": 0.0,
            "details": {"arguments": {"title": "Meeting"}},
        },
        {
            "timestamp": "2026-09-20T12:05:03+03:00",
            "run_id": "run_02",
            "user_id": 222,
            "tool_name": None,
            "tokens": 200,
            "cost_usd": 0.0001,
        },
        # Run 3: User 111, unknown tool with error (failed run)
        {
            "timestamp": "2026-09-20T12:10:00+03:00",
            "run_id": "run_03",
            "user_id": 111,
            "tool_name": "unknown_tool",
            "tokens": 0,
            "cost_usd": 0.0,
            "details": {"error": "Tool not found"},
        },
        # Operations: summarize for user 111
        {
            "timestamp": "2026-09-20T12:15:00+03:00",
            "run_id": "run_04",
            "user_id": 111,
            "tool_name": "summarize",
            "tokens": 50,
            "cost_usd": 0.00002,
        },
        # Nightly consolidate for user 222
        {
            "timestamp": "2026-09-21T03:00:00+03:00",
            "run_id": "run_05",
            "user_id": 222,
            "tool_name": "consolidate",
            "tokens": 100,
            "cost_usd": 0.00004,
            "details": {"facts_created": 2},
        },
    ]

    with open(log_file, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")

    metrics = compute_metrics(log_file)

    assert metrics["total_entries"] == 7

    # 5 runs in total: run_01 (ok), run_02 (ok), run_03 (failed), run_04 (summarize, ok), run_05 (consolidate, ok)
    ts = metrics["task_success"]
    assert ts["total_runs"] == 5
    assert ts["successful_runs"] == 4
    assert ts["failed_runs"] == 1
    assert ts["success_rate_percent"] == 80.0

    # Tool calls: search_web, create_event, unknown_tool (summarize & consolidate excluded from external tools)
    tu = metrics["tool_usage"]
    assert tu["total_tool_calls"] == 3
    assert tu["valid_tool_calls"] == 2
    assert tu["invalid_tool_calls"] == 1
    assert tu["accuracy_percent"] == round((2 / 3) * 100.0, 2)
    assert tu["tool_distribution"]["search_web"] == 1
    assert tu["tool_distribution"]["create_event"] == 1
    assert tu["tool_distribution"]["unknown_tool"] == 1
    assert tu["tool_distribution"]["summarize"] == 1
    assert tu["tool_distribution"]["consolidate"] == 1

    # Costs: sum from cost_usd directly
    cs = metrics["cost_summary"]
    expected_total_cost = 0.00005 + 0.0001 + 0.00002 + 0.00004
    assert abs(cs["total_cost_usd"] - expected_total_cost) < 1e-6
    assert cs["total_tokens"] == 150 + 200 + 50 + 100

    # Per-user breakdown
    assert 111 in cs["per_user"]
    assert 222 in cs["per_user"]
    assert abs(cs["per_user"][111]["cost_usd"] - (0.00005 + 0.00002)) < 1e-6
    assert abs(cs["per_user"][222]["cost_usd"] - (0.0001 + 0.00004)) < 1e-6


def test_metrics_cli_execution(tmp_path: Path):
    log_file = tmp_path / "cli.jsonl"
    entry = {
        "timestamp": "2026-09-20T12:00:00+03:00",
        "run_id": "r1",
        "user_id": 111,
        "tool_name": None,
        "tokens": 40,
        "cost_usd": 0.00001,
    }
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    cmd = [sys.executable, "scripts/metrics.py", str(log_file), "--json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    out = json.loads(proc.stdout)
    assert out["total_entries"] == 1
    assert out["task_success"]["total_runs"] == 1

    # Plain text report test
    cmd_text = [sys.executable, "scripts/metrics.py", str(log_file)]
    proc_text = subprocess.run(cmd_text, capture_output=True, text=True, check=True)
    assert "=== Pi Agent Žurnalo Metrikos ===" in proc_text.stdout
    assert "Užduočių sėkmė:" in proc_text.stdout
