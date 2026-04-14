"""Daily V3/V3.1/V4 Sports comparison report — sent to Telegram on cron.

Designed for ~6-line groups so a two-check daily cadence stays readable.
Runs on EC2 via: `python3 scripts/daily_report.py`.
"""
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone, timedelta, date

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

GROUPS = [
    ("V3 (us, baseline)", [
        "sports_longshot", "sports_midshot", "sports_underdog",
    ]),
    ("V3.1 (eu, sharp consensus)", [
        "sports_longshot_v31", "sports_midshot_v31", "sports_underdog_v31",
    ]),
    ("V4 (eu, edge/depth-based)", [
        "sports_pinnacle_edge", "sports_v4_edge",
        "sports_liquidity_filtered", "sports_consolidated",
    ]),
]


async def _strategy_row(db: Database, name: str) -> str:
    trades = await db.get_trades(strategy=name)
    total = len(trades)
    settled = [t for t in trades if t.get("status") == "paper_settled"]
    pending = total - len(settled)
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


async def build_report(db: Database, bankroll: float) -> str:
    """Compose a short multi-line Telegram summary."""
    now = datetime.now(timezone.utc)
    et = now - timedelta(hours=4)  # EDT = UTC-4 during daylight saving
    header = (
        f"<b>🏈 Sports V3 / V3.1 / V4 Report</b>\n"
        f"{et.strftime('%Y-%m-%d %H:%M')} ET\n"
        f"Bankroll: ${bankroll:.2f}"
    )
    sections = []
    # Season-opening reminders fire exactly 7 days before a tracked sport's
    # regular season begins. Prepended so they show at the top of Telegram.
    for reminder in get_active_reminders(_today_et()):
        sections.append(reminder)
    sections.append(header)
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

    text = await build_report(db, bankroll=bankroll)
    logger.info("Report:\n%s", text)

    notifier = TelegramNotifier(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
    await notifier.send(text, msg_type=f"daily_report_{et_hour(datetime.now(timezone.utc))}")


def et_hour(now_utc: datetime) -> int:
    return (now_utc - timedelta(hours=4)).hour


if __name__ == "__main__":
    asyncio.run(main())
