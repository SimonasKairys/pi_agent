"""Tests for usage tracking and cost limits in agent/db.py."""

from pathlib import Path
import pytest
from agent.db import (
    get_connection,
    calculate_cost,
    record_usage,
    get_user_daily_cost,
    get_total_daily_cost,
    check_daily_cost_limit,
    LimitExceededError,
    MAX_USER_DAILY_COST_USD,
    MAX_TOTAL_DAILY_COST_USD,
)


def test_calculate_cost():
    # 1M prompt @ 0.15 + 1M completion @ 0.60 = 0.75 USD
    cost = calculate_cost(1_000_000, 1_000_000)
    assert pytest.approx(cost, rel=1e-5) == 0.75

    # 1000 prompt (0.00015) + 500 completion (0.00030) = 0.00045 USD
    cost_small = calculate_cost(1000, 500)
    assert pytest.approx(cost_small, rel=1e-5) == 0.00045


def test_usage_under_limit(tmp_path: Path):
    db_file = tmp_path / "test.db"
    conn = get_connection(db_file)
    user_id = 111
    day = "2026-09-20"

    record_usage(conn, user_id=user_id, cost_usd=0.25, day=day)
    assert pytest.approx(get_user_daily_cost(conn, user_id, day=day)) == 0.25
    assert pytest.approx(get_total_daily_cost(conn, day=day)) == 0.25

    # Should not raise
    check_daily_cost_limit(conn, user_id, day=day)


def test_user_limit_exceeded(tmp_path: Path):
    db_file = tmp_path / "test.db"
    conn = get_connection(db_file)
    user_id = 222
    day = "2026-09-20"

    # Exceed 1.00 USD user limit
    record_usage(conn, user_id=user_id, cost_usd=MAX_USER_DAILY_COST_USD + 0.01, day=day)

    with pytest.raises(LimitExceededError, match="Viršyta jūsų dienos naudojimo riba"):
        check_daily_cost_limit(conn, user_id, day=day)


def test_total_system_limit_exceeded(tmp_path: Path):
    db_file = tmp_path / "test.db"
    conn = get_connection(db_file)
    day = "2026-09-20"

    # 4 users each spend 0.90 USD (below 1.00 USD each, but total = 3.60 USD)
    for uid in [1, 2, 3, 4]:
        record_usage(conn, user_id=uid, cost_usd=0.90, day=day)

    # User 5 spends 0.50 USD -> total becomes 4.10 USD (exceeds 4.00 USD total)
    record_usage(conn, user_id=5, cost_usd=0.50, day=day)

    assert get_total_daily_cost(conn, day=day) > MAX_TOTAL_DAILY_COST_USD

    # Any user (even a new one who spent 0.00) should now be blocked by system limit
    with pytest.raises(LimitExceededError, match="Viršyta bendra sistemos dienos naudojimo riba"):
        check_daily_cost_limit(conn, user_id=6, day=day)


def test_daily_reset(tmp_path: Path):
    db_file = tmp_path / "test.db"
    conn = get_connection(db_file)
    user_id = 333

    day1 = "2026-09-20"
    day2 = "2026-09-21"

    # Exceed limit on day 1
    record_usage(conn, user_id=user_id, cost_usd=1.50, day=day1)
    with pytest.raises(LimitExceededError):
        check_daily_cost_limit(conn, user_id, day=day1)

    # On day 2, cost should be 0 and check should succeed
    assert get_user_daily_cost(conn, user_id, day=day2) == 0.0
    assert get_total_daily_cost(conn, day=day2) == 0.0
    check_daily_cost_limit(conn, user_id, day=day2)
