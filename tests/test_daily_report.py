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


@pytest.mark.asyncio
async def test_report_includes_v32_v33_groups(db):
    """All sports variant generations show up as their own group."""
    text = await build_report(db, bankroll=2000.0)
    assert "V3.2" in text
    assert "V3.3" in text
    for short_name in ("longshot_v32:", "midshot_v32:", "underdog_v32:",
                       "longshot_v33:", "midshot_v33:", "underdog_v33:"):
        assert short_name in text, f"{short_name} missing"


async def _seed_settlement(db, ticker: str, settled_at: str, result: str = "yes"):
    """Seed a ticker_settlements row so the realized-PnL join finds it."""
    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        await conn.execute(
            """INSERT OR REPLACE INTO ticker_settlements
               (kalshi_ticker, result, close_yes_ask, close_no_ask,
                settled_at, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ticker, result, 1.0, 0.01, settled_at, settled_at),
        )
        await conn.commit()


@pytest.mark.asyncio
async def test_live_today_section_shown_when_strategies_passed(db):
    """LIVE Activity + PnL sections appear when live strategies are configured."""
    no_live = await build_report(db, bankroll=2000.0)
    assert "LIVE Activity Today" not in no_live
    assert "LIVE Realized PnL Today" not in no_live

    with_live = await build_report(
        db, bankroll=2000.0,
        live_strategies=["sports_longshot_v31", "sports_v4_edge"],
    )
    assert "LIVE Activity Today" in with_live
    assert "LIVE Realized PnL Today" in with_live


@pytest.mark.asyncio
async def test_live_activity_counts_filled_and_settled_excludes_paper(db, monkeypatch):
    """Activity row counts entries placed today (filled + settled, by entry
    timestamp). Paper rows must be excluded since they're not real-money."""
    monkeypatch.setattr("scripts.daily_report._today_cutoff_utc_iso",
                        lambda: "2000-01-01T00:00:00+00:00")
    # 2 live settled + 1 live filled (open) + 1 paper twin all today.
    await db.record_trade(strategy="sports_longshot_v31", market_id="m_w",
                          direction="yes", price=0.30, size=4.0, fee=0.07,
                          status="settled", pnl=8.0)
    await db.record_trade(strategy="sports_longshot_v31", market_id="m_l",
                          direction="yes", price=0.30, size=4.0, fee=0.07,
                          status="settled", pnl=-4.0)
    await db.record_trade(strategy="sports_longshot_v31", market_id="m_open",
                          direction="yes", price=0.30, size=4.0, fee=0.07,
                          status="filled")
    await db.record_trade(strategy="sports_longshot_v31", market_id="m_paper",
                          direction="yes", price=0.30, size=4.0, fee=0.07,
                          status="paper_settled", pnl=99.0)
    text = await build_report(
        db, bankroll=2000.0, live_strategies=["sports_longshot_v31"],
    )
    # Find the Activity row for longshot_v31
    activity_line = next(
        l for l in text.split("\n")
        if "longshot_v31:" in l and "entered" in l and "settled" in l
    )
    assert "3 entered" in activity_line  # 2 settled + 1 filled, paper excluded
    assert "open 1" in activity_line
    assert "settled 2" in activity_line


@pytest.mark.asyncio
async def test_live_pnl_uses_settlement_timestamp_not_entry(db, monkeypatch):
    """Realized PnL section joins ticker_settlements.settled_at, so a trade
    entered LAST WEEK but settled TODAY should appear in today's report."""
    monkeypatch.setattr("scripts.daily_report._today_cutoff_utc_iso",
                        lambda: "2026-04-27T10:00:00+00:00")
    # Entry was a week ago — but settled today.
    await db.record_trade(strategy="sports_longshot_v31", market_id="t_old_entry",
                          direction="yes", price=0.30, size=4.0, fee=0.07,
                          status="settled", pnl=8.0,
                          )
    # Manually set entry timestamp to last week to prove the entry filter
    # would have excluded this row under the old logic.
    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        await conn.execute(
            "UPDATE trades SET timestamp = '2026-04-20T12:00:00+00:00' WHERE market_id = ?",
            ("t_old_entry",),
        )
        await conn.commit()
    await _seed_settlement(db, "t_old_entry", "2026-04-27T15:00:00+00:00")

    text = await build_report(
        db, bankroll=2000.0, live_strategies=["sports_longshot_v31"],
    )
    # PnL row should show this settled trade
    pnl_line = next(
        l for l in text.split("\n")
        if "longshot_v31:" in l and "PnL +8.00" in l
    )
    assert "1 settled" in pnl_line
    assert "1W / 0L" in pnl_line


@pytest.mark.asyncio
async def test_live_pnl_excludes_pre_cutoff_settlements(db, monkeypatch):
    """A trade settled BEFORE the cutoff is excluded from the realized-PnL
    section even if its entry was today."""
    monkeypatch.setattr("scripts.daily_report._today_cutoff_utc_iso",
                        lambda: "2026-04-27T10:00:00+00:00")
    await db.record_trade(strategy="sports_longshot_v31", market_id="t_prev_settle",
                          direction="yes", price=0.30, size=4.0, fee=0.07,
                          status="settled", pnl=8.0)
    # Settled yesterday — before today's cutoff
    await _seed_settlement(db, "t_prev_settle", "2026-04-26T15:00:00+00:00")

    text = await build_report(
        db, bankroll=2000.0, live_strategies=["sports_longshot_v31"],
    )
    # PnL row should be the placeholder (—) — no settlement counted
    pnl_section_idx = text.find("LIVE Realized PnL Today")
    pnl_block = text[pnl_section_idx:]
    pnl_line = next(
        l for l in pnl_block.split("\n")
        if "longshot_v31:" in l
    )
    assert "—" in pnl_line
    assert "settled" not in pnl_line.lower() or "0 settled" in pnl_line
