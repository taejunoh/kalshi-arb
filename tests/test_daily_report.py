"""Unit tests for daily V3/V3.1/V4 comparison report."""
import pytest
import pytest_asyncio

from core.database import Database
from scripts.daily_report import build_report


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.initialize()
    yield database


@pytest.mark.asyncio
async def test_report_has_three_sections(db):
    """Report always includes V3, V3.1, V4 group headers."""
    text = await build_report(db, bankroll=2000.0)
    assert "V3" in text
    assert "V3.1" in text
    assert "V4" in text


@pytest.mark.asyncio
async def test_report_lists_all_expected_strategies(db):
    """Full strategy names are shortened by stripping the `sports_` prefix."""
    text = await build_report(db, bankroll=2000.0)
    for short_name in ("longshot:", "midshot:", "underdog:",
                       "longshot_v31:", "midshot_v31:", "underdog_v31:",
                       "pinnacle_edge:", "v4_edge:",
                       "liquidity_filtered:", "consolidated:"):
        assert short_name in text, f"{short_name} missing from report"


@pytest.mark.asyncio
async def test_report_counts_settled_and_pending_separately(db):
    # Seed: longshot has 2 settled (+$5 net) + 1 pending
    await db.record_trade(strategy="sports_longshot", market_id="m1", direction="yes",
                          price=0.30, size=3.0, fee=0.07, status="paper_settled", pnl=5.0)
    await db.record_trade(strategy="sports_longshot", market_id="m2", direction="no",
                          price=0.40, size=3.0, fee=0.07, status="paper_settled", pnl=-3.0)
    await db.record_trade(strategy="sports_longshot", market_id="m3", direction="yes",
                          price=0.20, size=3.0, fee=0.07, status="paper_filled")
    text = await build_report(db, bankroll=2000.0)
    # The row should report 3 total, 2 settled, +2.00 pnl
    longshot_line = next(line for line in text.split("\n")
                         if "longshot:" in line and "v31" not in line)
    assert "3 trades" in longshot_line
    assert "settled 2" in longshot_line
    assert "+2.00" in longshot_line


@pytest.mark.asyncio
async def test_report_includes_bankroll_and_timestamp(db):
    text = await build_report(db, bankroll=1987.50)
    assert "$1987.50" in text or "$1,987.50" in text
    # YYYY-MM-DD format should appear
    import re
    assert re.search(r"\d{4}-\d{2}-\d{2}", text)


@pytest.mark.asyncio
async def test_report_prepends_season_reminder_when_due(db, monkeypatch):
    """On T-7 day of a tracked season, report should lead with the heads-up."""
    from datetime import date
    monkeypatch.setattr(
        "scripts.daily_report._today_et",
        lambda: date(2026, 5, 8),  # 7 days before WNBA opener 2026-05-15
    )
    text = await build_report(db, bankroll=2000.0)
    # Reminder should appear above the main report body
    assert "WNBA" in text
    assert text.index("WNBA") < text.index("V3 ")


@pytest.mark.asyncio
async def test_report_has_no_reminder_on_ordinary_day(db, monkeypatch):
    from datetime import date
    monkeypatch.setattr(
        "scripts.daily_report._today_et",
        lambda: date(2026, 6, 10),
    )
    text = await build_report(db, bankroll=2000.0)
    assert "season opens" not in text


@pytest.mark.asyncio
async def test_report_handles_zero_trades_gracefully(db):
    text = await build_report(db, bankroll=2000.0)
    # Empty strategies should show placeholder (no division by zero)
    assert "—" in text or "0" in text  # should contain some kind of empty indicator
