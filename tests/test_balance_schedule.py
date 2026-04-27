"""Time computation + message formatting for the 14:00 / 22:00 ET
balance-check Telegram alerts. zoneinfo handles DST shifts so the same
schedule expression yields the right UTC instant year-round."""
from datetime import datetime, timezone

from monitoring.balance_schedule import (
    ET, format_check_message, next_check_time,
)


def _utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def test_next_check_morning_returns_14_et_today():
    """It's 09:00 ET (13:00 UTC) on a summer day → next is 14:00 ET = 18:00 UTC."""
    now = _utc(2026, 4, 27, 13, 0)  # 09:00 EDT (UTC-4)
    nxt = next_check_time(now)
    assert nxt == _utc(2026, 4, 27, 18, 0)
    assert nxt.astimezone(ET).hour == 14


def test_next_check_after_14_returns_22_et_today():
    """At 15:00 ET → next is 22:00 ET same day."""
    now = _utc(2026, 4, 27, 19, 0)  # 15:00 EDT
    nxt = next_check_time(now)
    assert nxt == _utc(2026, 4, 28, 2, 0)  # 22:00 EDT = 02:00 UTC next day
    assert nxt.astimezone(ET).hour == 22


def test_next_check_after_22_returns_14_next_day():
    """At 23:00 ET → next is 14:00 ET tomorrow."""
    now = _utc(2026, 4, 28, 3, 0)  # 23:00 EDT 4/27
    nxt = next_check_time(now)
    assert nxt == _utc(2026, 4, 28, 18, 0)  # 14:00 EDT 4/28 = 18:00 UTC
    assert nxt.astimezone(ET).hour == 14


def test_next_check_dst_winter_offset():
    """In winter (EST = UTC-5), 14:00 ET = 19:00 UTC (not 18:00)."""
    now = _utc(2026, 1, 15, 13, 0)  # 08:00 EST
    nxt = next_check_time(now)
    assert nxt == _utc(2026, 1, 15, 19, 0)
    assert nxt.astimezone(ET).hour == 14


def test_next_check_exactly_at_target_returns_next_slot():
    """Boundary: now == 14:00:00 ET should advance to 22:00 ET (strict >)."""
    now = _utc(2026, 4, 27, 18, 0)  # 14:00 EDT exactly
    nxt = next_check_time(now)
    assert nxt.astimezone(ET).hour == 22


def test_next_check_requires_tz_aware():
    import pytest
    with pytest.raises(ValueError):
        next_check_time(datetime(2026, 4, 27, 13, 0))


def test_format_message_below_threshold_recommends_deposit():
    msg = format_check_message(
        hour_et=14, balance=149.98,
        total_kalshi=2137.16, open_positions=87,
    )
    assert "14:00 ET" in msg
    assert "$149.98" in msg
    assert "DEPOSIT" in msg
    assert "$500" in msg  # threshold
    assert "🔴" in msg


def test_format_message_above_threshold_marks_ok():
    msg = format_check_message(
        hour_et=22, balance=350.0,
        total_kalshi=1500.0, open_positions=50,
    )
    assert "22:00 ET" in msg
    assert "$350.00" in msg
    assert "OK" in msg
    assert "🟢" in msg


def test_threshold_differs_by_hour():
    """14:00 = $500, 22:00 = $200 per the agreed schedule."""
    msg14 = format_check_message(
        hour_et=14, balance=250.0, total_kalshi=1000.0, open_positions=10,
    )
    msg22 = format_check_message(
        hour_et=22, balance=250.0, total_kalshi=1000.0, open_positions=10,
    )
    # Same balance ($250) — under $500 threshold but over $200 threshold
    assert "DEPOSIT" in msg14
    assert "OK" in msg22
