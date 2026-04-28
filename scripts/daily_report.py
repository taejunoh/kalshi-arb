"""Daily strategy comparison report — sent to Telegram on cron.

Two sections:
  1. LIVE Today — daily PnL, trade count, win/loss for each LIVE_STRATEGIES
     entry (real-money positions only). Reset at 06:00 ET (matches dashboard
     today_cutoff: between west-coast late games and next-day pre-game).
  2. Strategy groups — paper-settled cumulative stats per group (V3 / V3.1
     / V3.2 / V3.3 / V4 active / archived). Lets us A/B variants at a glance.

Runs on EC2 via: `python3 scripts/daily_report.py`.
"""
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone, timedelta, date
from typing import Optional

# Make package imports work when launched as a standalone script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config
from core.database import Database
from monitoring.notifier import TelegramNotifier
from scripts.season_reminders import get_active_reminders

logger = logging.getLogger("daily_report")


def _today_et() -> date:
    """Current date in US Eastern (EDT during DST, EST otherwise). Overridable for tests."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York")).date()


def _today_cutoff_utc_iso() -> str:
    """Most recent 06:00 ET boundary as UTC ISO string. Same convention as
    monitoring.dashboard.api._today_cutoff_utc — keeps "today" consistent
    across dashboard and report."""
    from zoneinfo import ZoneInfo
    et = datetime.now(ZoneInfo("America/New_York"))
    if et.hour < 6:
        start = (et - timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
    else:
        start = et.replace(hour=6, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc).isoformat()


GROUPS = [
    ("V3 (us, retired baseline)", [
        "sports_longshot", "sports_midshot", "sports_underdog",
    ]),
    ("V3.1 (eu, sharp consensus)", [
        "sports_longshot_v31", "sports_midshot_v31", "sports_underdog_v31",
    ]),
    ("V3.2 (edge-scaled sizing)", [
        "sports_longshot_v32", "sports_midshot_v32", "sports_underdog_v32",
    ]),
    ("V3.3 (per-game cap)", [
        "sports_longshot_v33", "sports_midshot_v33", "sports_underdog_v33",
    ]),
    ("V4 (active edge filter)", [
        "sports_v4_edge",
    ]),
    ("Archived", [
        "sports_pinnacle_edge",
        "sports_liquidity_filtered", "sports_consolidated",
    ]),
]


async def _strategy_row(db: Database, name: str) -> str:
    trades = await db.get_trades(strategy=name)
    total = len(trades)
    settled = [t for t in trades if t.get("status") == "paper_settled"]
    pnl = sum(t.get("pnl", 0) or 0 for t in settled)
    wins = sum(1 for t in settled if (t.get("pnl", 0) or 0) > 0)
    losses = sum(1 for t in settled if (t.get("pnl", 0) or 0) < 0)
    settled_count = len(settled)
    wr = (wins / settled_count * 100) if settled_count > 0 else None
    try:
        clv = await db.get_clv_positive_rate(name, days=30)
    except Exception:
        clv = 0.0

    short = name.replace("sports_", "")
    pnl_str = f"{pnl:+.2f}" if settled_count > 0 else "—"
    wr_str = f"{wr:.0f}%" if wr is not None else "—"
    clv_str = f"{clv * 100:.0f}%" if clv > 0 else "—"
    return (f"  {short}: {total} trades | settled {settled_count} | "
            f"PnL {pnl_str} | WR {wr_str} | CLV+ {clv_str}")


async def _live_activity_row(db: Database, name: str, today_cutoff: str) -> tuple[str, dict]:
    """Activity (entries placed today) — counts and exposure regardless of
    settlement. For sports the entry-vs-settle gap is hours-to-days, so this
    is the only view that meaningfully tracks daily activity in real time.
    Returns (formatted_row, totals_dict)."""
    trades = await db.get_trades(strategy=name)
    today_entries = [
        t for t in trades
        if t.get("status") in ("filled", "settled")
        and t.get("timestamp", "") >= today_cutoff
    ]
    n_total = len(today_entries)
    n_open = sum(1 for t in today_entries if t.get("status") == "filled")
    n_settled = n_total - n_open
    exposure = sum(t.get("size", 0) or 0 for t in today_entries)
    short = name.replace("sports_", "")
    if n_total == 0:
        row = f"  {short}: —"
    else:
        row = (f"  {short}: {n_total} entered "
               f"(open {n_open}, settled {n_settled}) | exposure ${exposure:,.2f}")
    return row, {"n": n_total, "open": n_open, "settled": n_settled, "exposure": exposure}


async def _live_pnl_row(db: Database, name: str, today_cutoff: str) -> tuple[str, dict]:
    """Realized PnL today — trades whose Kalshi ticker SETTLED since cutoff,
    regardless of entry date. Joins ticker_settlements.settled_at; shows the
    realized-money view that complements the entry-based activity row."""
    settled_today = await db.get_trades_settled_since(today_cutoff, strategy=name)
    n = len(settled_today)
    pnl = sum(t.get("pnl", 0) or 0 for t in settled_today)
    wins = sum(1 for t in settled_today if (t.get("pnl", 0) or 0) > 0)
    losses = sum(1 for t in settled_today if (t.get("pnl", 0) or 0) < 0)
    wr = (wins / (wins + losses) * 100) if (wins + losses) > 0 else None
    short = name.replace("sports_", "")
    if n == 0:
        row = f"  {short}: —"
    else:
        wr_str = f"{wr:.0f}%" if wr is not None else "—"
        row = (f"  {short}: {n} settled | PnL {pnl:+.2f} | "
               f"{wins}W / {losses}L | WR {wr_str}")
    return row, {"n": n, "pnl": pnl, "wins": wins, "losses": losses}


async def _live_sections(
    db: Database, live_strategies: list[str], today_cutoff: str,
) -> Optional[str]:
    """Compose the two LIVE-Today sections (activity + realized PnL). Returns
    None if no live strategies are configured."""
    if not live_strategies:
        return None
    ordered = sorted(live_strategies, key=lambda s: (not s.startswith("sports_"), s))

    activity_rows = []
    activity_totals = {"n": 0, "open": 0, "settled": 0, "exposure": 0.0}
    pnl_rows = []
    pnl_totals = {"n": 0, "pnl": 0.0, "wins": 0, "losses": 0}
    for name in ordered:
        a_row, a_tot = await _live_activity_row(db, name, today_cutoff)
        activity_rows.append(a_row)
        for k, v in a_tot.items():
            activity_totals[k] += v
        p_row, p_tot = await _live_pnl_row(db, name, today_cutoff)
        pnl_rows.append(p_row)
        for k, v in p_tot.items():
            pnl_totals[k] += v

    activity_summary = (
        f"  <b>TOTAL: {activity_totals['n']} entered "
        f"(open {activity_totals['open']}, settled {activity_totals['settled']}) | "
        f"exposure ${activity_totals['exposure']:,.2f}</b>"
    )
    activity_block = (
        "<b>🔴 LIVE Activity Today (entered since 06:00 ET)</b>\n"
        + "\n".join(activity_rows + [activity_summary])
    )

    p_wr = None
    if (pnl_totals["wins"] + pnl_totals["losses"]) > 0:
        p_wr = pnl_totals["wins"] / (pnl_totals["wins"] + pnl_totals["losses"]) * 100
    pnl_summary = (
        f"  <b>TOTAL: {pnl_totals['n']} settled | "
        f"PnL {pnl_totals['pnl']:+.2f} | "
        f"{pnl_totals['wins']}W / {pnl_totals['losses']}L | "
        f"WR {f'{p_wr:.0f}%' if p_wr is not None else '—'}</b>"
    )
    pnl_block = (
        "<b>💰 LIVE Realized PnL Today (settled since 06:00 ET)</b>\n"
        + "\n".join(pnl_rows + [pnl_summary])
    )

    return activity_block + "\n\n" + pnl_block


async def build_report(db: Database, bankroll: float, live_strategies: Optional[list[str]] = None) -> str:
    """Compose a multi-line Telegram summary."""
    now = datetime.now(timezone.utc)
    et = now - timedelta(hours=4)  # EDT = UTC-4 during daylight saving
    header = (
        f"<b>📊 Strategy Daily Report</b>\n"
        f"{et.strftime('%Y-%m-%d %H:%M')} ET\n"
        f"Bankroll: ${bankroll:.2f}"
    )
    sections = []
    # Season-opening reminders fire exactly 7 days before a tracked sport's
    # regular season begins. Prepended so they show at the top of Telegram.
    for reminder in get_active_reminders(_today_et()):
        sections.append(reminder)
    sections.append(header)

    # LIVE Today (real-money) — split into Activity (entered today) and
    # Realized PnL (settled today, via ticker_settlements join). Either or
    # both may be empty depending on time-of-day; both are useful signals.
    if live_strategies:
        today_cutoff = _today_cutoff_utc_iso()
        live_block = await _live_sections(db, live_strategies, today_cutoff)
        if live_block:
            sections.append("\n" + live_block)

    # Paper cumulative — all groups
    for group_label, names in GROUPS:
        rows = [await _strategy_row(db, n) for n in names]
        sections.append(f"\n<b>{group_label}</b>\n" + "\n".join(rows))
    return "\n".join(sections)


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = Config()
    db = Database()
    await db.initialize()

    # Live bankroll comes from the bot's write-through to bot_state table.
    # Falls back to config.INITIAL_BANKROLL when the bot hasn't persisted yet.
    persisted = await db.get_bot_state("bankroll")
    bankroll = persisted if persisted is not None else config.INITIAL_BANKROLL

    live_strategies = sorted(config.LIVE_STRATEGIES)
    text = await build_report(db, bankroll=bankroll, live_strategies=live_strategies)
    logger.info("Report:\n%s", text)

    notifier = TelegramNotifier(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
    await notifier.send(text, msg_type=f"daily_report_{et_hour(datetime.now(timezone.utc))}")


def et_hour(now_utc: datetime) -> int:
    return (now_utc - timedelta(hours=4)).hour


if __name__ == "__main__":
    asyncio.run(main())
