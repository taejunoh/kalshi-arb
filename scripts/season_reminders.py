"""Hardcoded season-opening calendar.

When a covered-but-not-yet-scanned sport is about to start its regular
season, the daily Telegram report should include a T-7 heads-up so we can
decide whether to wire up Kalshi + Odds API support in time.

Kept as a simple Python list rather than a DB table — the dates move once
a year at most and are stable enough to commit in source. Update this file
each year when the leagues announce opening weekends.
"""
from datetime import date, timedelta
from typing import Optional

# Source: official league schedules (verify annually).
# open_date = Day 1 of regular season (first game). Reminder fires T-7 days.
SEASON_OPENINGS: list[dict] = [
    {
        "name": "WNBA",
        "open_date": date(2026, 5, 15),
        "action": "Add KXWNBAGAME series to sports_trader.sport_map "
                  "(Odds API sport_key: basketball_wnba). "
                  "Update clv_tracker regex to include WNBA.",
    },
    {
        "name": "NCAA Football",
        "open_date": date(2026, 8, 29),
        "action": "Add KXCFBGAME series (Odds API: americanfootball_ncaaf). "
                  "Verify Kalshi's CFB ticker format before committing to regex.",
    },
    {
        "name": "NFL",
        "open_date": date(2026, 9, 10),
        "action": "Add KXNFLGAME series (Odds API: americanfootball_nfl). "
                  "NFL has highest sharp-book volume — likely best Pinnacle edge.",
    },
    {
        "name": "NCAA Basketball",
        "open_date": date(2026, 11, 3),
        "action": "Add KXNCAABGAME series (Odds API: basketball_ncaab). "
                  "High game volume = fast CLV+ sample accumulation.",
    },
]

REMINDER_LEAD_DAYS = 7


def get_active_reminders(today: Optional[date] = None) -> list[str]:
    """Return formatted reminder messages for seasons whose opening is
    exactly REMINDER_LEAD_DAYS ahead of `today`. Typically fired once per
    year per season. Returns empty list most days."""
    if today is None:
        today = date.today()
    msgs: list[str] = []
    for entry in SEASON_OPENINGS:
        if entry["open_date"] - today == timedelta(days=REMINDER_LEAD_DAYS):
            msgs.append(
                f"⏰ <b>{entry['name']} season opens {entry['open_date'].isoformat()}</b> "
                f"(in {REMINDER_LEAD_DAYS} days).\n"
                f"Action: {entry['action']}"
            )
    return msgs
