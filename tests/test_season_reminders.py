"""Season opening reminders — fire in the daily report 7 days before each
covered-but-not-yet-scanned sport starts its regular season."""
from datetime import date

from scripts.season_reminders import SEASON_OPENINGS, get_active_reminders


def test_no_reminder_when_no_season_starts_today():
    """Ordinary day → empty list."""
    assert get_active_reminders(date(2026, 6, 10)) == []


def test_reminder_fires_exactly_7_days_before_season():
    """NCAA Football opens 2026-08-29 → reminder on 2026-08-22."""
    msgs = get_active_reminders(date(2026, 8, 22))
    assert len(msgs) == 1
    assert "NCAA Football" in msgs[0]


def test_reminder_does_not_fire_6_days_before():
    """Only T-7 exactly, not a week-long window."""
    assert get_active_reminders(date(2026, 8, 23)) == []
    assert get_active_reminders(date(2026, 8, 28)) == []  # day before
    assert get_active_reminders(date(2026, 8, 29)) == []  # day of


def test_multiple_reminders_same_day_possible():
    """If two seasons open within a week of each other, both should fire
    on their own T-7 day. Just checking the API shape allows a list."""
    # Not actual overlapping seasons, just validating the data structure
    assert isinstance(get_active_reminders(date(2026, 1, 1)), list)


def test_each_entry_has_required_fields():
    """Data integrity check — every season entry needs open_date + name + action."""
    for entry in SEASON_OPENINGS:
        assert "open_date" in entry and isinstance(entry["open_date"], date)
        assert "name" in entry and entry["name"]
        assert "action" in entry and entry["action"]


def test_covers_remaining_pre_wired_seasons():
    """Verify the remaining un-wired seasons are tracked. WNBA was wired
    2026-05-09 and removed from the list."""
    names = {e["name"] for e in SEASON_OPENINGS}
    assert "WNBA" not in names  # wired
    assert "NFL" in names
    assert "NCAA Football" in names
    assert "NCAA Basketball" in names


def test_openings_are_future_dates_from_today():
    """Sanity: all hardcoded dates should be in the future as of plan time
    (2026-04-14), otherwise they'll never fire."""
    plan_date = date(2026, 4, 14)
    for entry in SEASON_OPENINGS:
        assert entry["open_date"] > plan_date, f"{entry['name']} date is past plan date"
