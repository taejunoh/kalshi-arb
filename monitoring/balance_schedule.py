"""Scheduled balance-check Telegram alerts at 14:00 + 22:00 ET.

Two daily windows bracketing the primary trading peak (16:00-22:00 ET ≈ 58%
of fills). The 14:00 alert fires before the peak so the user can top up
cash; the 22:00 alert fires after the peak so the user can confirm the
overnight cushion (also catches the residual 02:00-08:00 ET signals
implicated in the 2026-04-27 missed-entries incident).

Time computation is local-Eastern-aware via zoneinfo so the schedule
shifts automatically across DST transitions — what the user requested
is "14:00 ET" in conversational terms, which is wall-clock 14:00 in
America/New_York regardless of EST vs EDT.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
DEFAULT_CHECK_HOURS: tuple[int, ...] = (14, 22)
THRESHOLDS = {14: 500, 22: 200}


def next_check_time(
    now_utc: datetime,
    *,
    hours: Iterable[int] = DEFAULT_CHECK_HOURS,
) -> datetime:
    """Return the next wall-clock {hour}:00 ET strictly after `now_utc`.

    Both inputs and outputs are timezone-aware UTC datetimes. The hours
    list is evaluated in America/New_York local time and the next match
    converted back to UTC for the asyncio.sleep computation.
    """
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")
    now_et = now_utc.astimezone(ET)
    candidates = []
    for h in hours:
        target = now_et.replace(hour=h, minute=0, second=0, microsecond=0)
        if target <= now_et:
            target = target + timedelta(days=1)
        candidates.append(target)
    return min(candidates).astimezone(now_utc.tzinfo)


def format_check_message(
    *,
    hour_et: int,
    balance: float,
    total_kalshi: float,
    open_positions: int,
) -> str:
    threshold = THRESHOLDS.get(hour_et, 300)
    if hour_et == 14:
        label = "pre-peak (primary peak starts 16:00 ET)"
    elif hour_et == 22:
        label = "post-peak (catches residual overnight signals)"
    else:
        label = "balance check"
    if balance < threshold:
        verdict = f"🔴 DEPOSIT — under ${threshold} threshold"
    else:
        verdict = "🟢 OK"
    return (
        f"⏰ <b>{hour_et}:00 ET balance check — {label}</b>\n"
        f"Free cash: ${balance:,.2f}\n"
        f"Open positions: {open_positions} (${total_kalshi - balance:,.2f})\n"
        f"Total Kalshi: ${total_kalshi:,.2f}\n"
        f"Threshold: < ${threshold}\n"
        f"Verdict: {verdict}"
    )
