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


async def _live_daily_row(db: Database, name: str, today_cutoff: str) -> str:
    """Live (real-money) row: today-only PnL / trade count / win-loss / WR.
    Filters status='settled' (closed live positions) and entry timestamp
    since the most recent 06:00 ET boundary."""
    trades = await db.get_trades(strategy=name)
    today_settled = [
        t for t in trades
        if t.get("status") == "settled"
        and t.get("timestamp", "") >= today_cutoff
    ]
    n = len(today_settled)
    pnl = sum(t.get("pnl", 0) or 0 for t in today_settled)
    wins = sum(1 for t in today_settled if (t.get("pnl", 0) or 0) > 0)
    losses = sum(1 for t in today_settled if (t.get("pnl", 0) or 0) < 0)
    wr = (wins / (wins + losses) * 100) if (wins + losses) > 0 else None

    short = name.replace("sports_", "")
    pnl_str = f"{pnl:+.2f}" if n > 0 else "—"
    wr_str = f"{wr:.0f}%" if wr is not None else "—"
    return (f"  {short}: {n} trades | PnL {pnl_str} | "
            f"{wins}W / {losses}L | WR {wr_str}")


async def _live_section(db: Database, live_strategies: list[str], today_cutoff: str) -> Optional[str]:
    """Compose the LIVE Today section. Returns None if no live strategies."""
    if not live_strategies:
        return None
    # Stable sort for readability — sports first, then everything else
    ordered = sorted(live_strategies, key=lambda s: (not s.startswith("sports_"), s))
    rows = [await _live_daily_row(db, n, today_cutoff) for n in ordered]
    # Aggregate totals across live strategies
    all_today = []
    for n in ordered:
        trades = await db.get_trades(strategy=n)
        all_today.extend(
            t for t in trades
            if t.get("status") == "settled" and t.get("timestamp", "") >= today_cutoff
        )
    total_n = len(all_today)
    total_pnl = sum(t.get("pnl", 0) or 0 for t in all_today)
    total_wins = sum(1 for t in all_today if (t.get("pnl", 0) or 0) > 0)
    total_losses = sum(1 for t in all_today if (t.get("pnl", 0) or 0) < 0)
    total_wr = (total_wins / (total_wins + total_losses) * 100) if (total_wins + total_losses) > 0 else None
    summary = (f"  <b>TOTAL: {total_n} trades | PnL "
               f"{total_pnl:+.2f} | {total_wins}W / {total_losses}L | "
               f"WR {f'{total_wr:.0f}%' if total_wr is not None else '—'}</b>")
    return "<b>🔴 LIVE Today (since 06:00 ET)</b>\n" + "\n".join(rows + [summary])


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

    # LIVE Today (real-money) — only if any live strategies configured
    if live_strategies:
        today_cutoff = _today_cutoff_utc_iso()
        live_block = await _live_section(db, live_strategies, today_cutoff)
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
