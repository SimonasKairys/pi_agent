#!/usr/bin/env python3
"""Calculates performance and cost metrics from pi_agent JSONL journal logs.

Metrics computed:
- Task success rate (užduočių sėkmė)
- Tool usage accuracy and distribution (įrankių naudojimo tikslumas)
- Cost and token usage per user (kaina vienam vartotojui)

Usage:
    python scripts/metrics.py [LOG_FILE_PATH]
    python scripts/metrics.py --file /path/to/journal.jsonl
    python scripts/metrics.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# Standard recognized tools in pi_agent
VALID_TOOLS = {
    "search_web",
    "list_events",
    "create_event",
    "update_event",
    "delete_event",
}

INTERNAL_OPERATIONS = {
    "summarize",
    "consolidate",
}


def parse_journal_entries(journal_path: Path) -> list[dict[str, Any]]:
    """Reads and parses JSON lines from the journal file."""
    if not journal_path.exists():
        return []

    entries: list[dict[str, Any]] = []
    with open(journal_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if isinstance(data, dict):
                    entries.append(data)
            except Exception:
                # Ignore corrupted lines
                continue
    return entries


def compute_metrics(journal_path: str | Path) -> dict[str, Any]:
    """Computes success rate, tool accuracy, and per-user cost from a journal file.

    Cost is taken directly from the cost_usd field in the journal without recalculation.
    """
    path = Path(journal_path)
    entries = parse_journal_entries(path)

    # 1. Group entries by run_id
    runs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in entries:
        run_id = str(e.get("run_id") or "unknown_run")
        runs[run_id].append(e)

    total_runs = len(runs)
    successful_runs = 0
    failed_runs = 0

    for run_id, run_entries in runs.items():
        # Check if the run has an error
        has_error = False
        has_completion = False

        for entry in run_entries:
            details = entry.get("details")
            if isinstance(details, dict):
                if details.get("error") or details.get("status") == "error":
                    has_error = True
                    break

            tool = entry.get("tool_name")
            # A run has completed if it has a final model completion or internal operation (summarize/consolidate)
            if tool is None or tool in INTERNAL_OPERATIONS:
                has_completion = True

        if not has_error and has_completion:
            successful_runs += 1
        else:
            failed_runs += 1

    task_success_rate = (
        round((successful_runs / total_runs) * 100.0, 2) if total_runs > 0 else 0.0
    )

    # 2. Tool accuracy and distribution
    tool_counts: dict[str, int] = defaultdict(int)
    total_tool_calls = 0
    valid_tool_calls = 0
    invalid_tool_calls = 0

    for e in entries:
        tool = e.get("tool_name")
        if tool is None:
            continue

        tool_counts[tool] += 1

        # Only evaluate external tools for tool call accuracy
        if tool in INTERNAL_OPERATIONS:
            continue

        total_tool_calls += 1
        details = e.get("details")
        has_error = isinstance(details, dict) and bool(details.get("error"))

        if tool in VALID_TOOLS and not has_error:
            valid_tool_calls += 1
        else:
            invalid_tool_calls += 1

    tool_accuracy = (
        round((valid_tool_calls / total_tool_calls) * 100.0, 2)
        if total_tool_calls > 0
        else 100.0
    )

    # 3. Cost and token usage per user
    user_costs: dict[int, float] = defaultdict(float)
    user_tokens: dict[int, int] = defaultdict(int)
    user_runs: dict[int, set[str]] = defaultdict(set)
    total_cost_usd = 0.0
    total_tokens = 0

    for e in entries:
        user_id = int(e.get("user_id") or 0)
        cost = float(e.get("cost_usd") or 0.0)
        tokens = int(e.get("tokens") or 0)
        run_id = str(e.get("run_id") or "")

        user_costs[user_id] += cost
        user_tokens[user_id] += tokens
        if run_id:
            user_runs[user_id].add(run_id)

        total_cost_usd += cost
        total_tokens += tokens

    per_user_summary = {}
    all_users = sorted(user_costs.keys())
    for uid in all_users:
        per_user_summary[uid] = {
            "cost_usd": round(user_costs[uid], 6),
            "tokens": user_tokens[uid],
            "runs_count": len(user_runs[uid]),
        }

    return {
        "journal_file": str(path),
        "total_entries": len(entries),
        "task_success": {
            "total_runs": total_runs,
            "successful_runs": successful_runs,
            "failed_runs": failed_runs,
            "success_rate_percent": task_success_rate,
        },
        "tool_usage": {
            "total_tool_calls": total_tool_calls,
            "valid_tool_calls": valid_tool_calls,
            "invalid_tool_calls": invalid_tool_calls,
            "accuracy_percent": tool_accuracy,
            "tool_distribution": dict(sorted(tool_counts.items())),
        },
        "cost_summary": {
            "total_cost_usd": round(total_cost_usd, 6),
            "total_tokens": total_tokens,
            "per_user": per_user_summary,
        },
    }


def format_report(metrics: dict[str, Any]) -> str:
    """Formats metrics dictionary into a human-readable Lithuanian report."""
    lines = []
    lines.append("=== Pi Agent Žurnalo Metrikos ===")
    lines.append(f"Žurnalo failas: {metrics['journal_file']}")
    lines.append(f"Viso įrašų: {metrics['total_entries']}")
    lines.append("")

    ts = metrics["task_success"]
    lines.append("Užduočių sėkmė:")
    lines.append(f"  Viso užduočių: {ts['total_runs']}")
    lines.append(f"  Sėkmingos: {ts['successful_runs']}")
    lines.append(f"  Nesėkmingos: {ts['failed_runs']}")
    lines.append(f"  Sėkmės rodiklis: {ts['success_rate_percent']} %")
    lines.append("")

    tu = metrics["tool_usage"]
    lines.append("Įrankių naudojimo tikslumas:")
    lines.append(f"  Viso įrankių kvietimų: {tu['total_tool_calls']}")
    lines.append(f"  Teisingi kvietimai: {tu['valid_tool_calls']}")
    lines.append(f"  Neteisingi kvietimai: {tu['invalid_tool_calls']}")
    lines.append(f"  Tikslumas: {tu['accuracy_percent']} %")
    if tu["tool_distribution"]:
        lines.append("  Kvietimų pasiskirstymas:")
        for tool_name, count in tu["tool_distribution"].items():
            lines.append(f"    - {tool_name}: {count}")
    lines.append("")

    cs = metrics["cost_summary"]
    lines.append("Kaina ir sąnaudos:")
    lines.append(f"  Bendra kaina: {cs['total_cost_usd']:.6f} USD")
    lines.append(f"  Viso žetonų: {cs['total_tokens']}")
    lines.append("  Pagal vartotojus:")
    for uid, udata in cs["per_user"].items():
        lines.append(
            f"    Vartotojas {uid}: {udata['cost_usd']:.6f} USD, "
            f"{udata['tokens']} žetonų, {udata['runs_count']} užduotys"
        )

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Apskaičiuoja pi_agent metrikas iš JSONL žurnalo.")
    parser.add_argument(
        "log_path",
        nargs="?",
        default=None,
        help="Kelias iki journal.jsonl failo.",
    )
    parser.add_argument(
        "-f",
        "--file",
        dest="file_opt",
        default=None,
        help="Kelias iki journal.jsonl failo (alternatyva).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Išvesti metrikas JSON formatu.",
    )

    args = parser.parse_args()
    target_path = args.file_opt or args.log_path

    if not target_path:
        # Try resolving default journal path
        try:
            from agent.journal import get_journal_path
            default_path = get_journal_path()
            target_path = str(default_path)
        except Exception:
            target_path = "dev/logs/journal.jsonl"

    metrics = compute_metrics(target_path)

    if args.json:
        print(json.dumps(metrics, indent=2, ensure_ascii=False))
    else:
        print(format_report(metrics))

    return 0


if __name__ == "__main__":
    sys.exit(main())
