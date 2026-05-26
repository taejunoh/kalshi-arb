"""Short-duration silence watch (12h, 30min ticks).

Watches 7 sports strategies the user flagged on 5/20:
- sports_soccer_underdog_v1, sports_underdog_v37, sports_longshot_v31
- sports_underdog_v34, sports_underdog_v35
- sports_midshot_v34, sports_midshot_v35

Each tick: count fires (filled/paper_filled) since last tick.
- New fire → immediate Telegram with details.
- Tick 24 (end of 12h) → final summary regardless.

Run with `nohup ... &` on the EC2 host. Self-terminates after 24 ticks.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sports_silence_watch")

WATCH_LIST = [
    "sports_soccer_underdog_v1",
    "sports_underdog_v37",
    "sports_longshot_v31",
    "sports_underdog_v34",
    "sports_underdog_v35",
    "sports_midshot_v34",
    "sports_midshot_v35",
]
TICK_SECS = 1800       # 30 min
NUM_TICKS = 24         # 12 hours
ACTIVITY = ("filled", "paper_filled", "settled", "paper_settled")
DB_PATH = "data/bot.db"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _new_fires_since(conn: sqlite3.Connection, strategy: str, since_iso: str) -> list[dict]:
    placeholders = ",".join("?" * len(ACTIVITY))
    rows = conn.execute(
        f"""SELECT id, timestamp, market_id, direction, price, size, status
            FROM trades
            WHERE strategy = ? AND status IN ({placeholders})
              AND timestamp >= ?
            ORDER BY timestamp ASC""",
        (strategy, *ACTIVITY, since_iso),
    ).fetchall()
    cols = ["id", "timestamp", "market_id", "direction", "price", "size", "status"]
    return [dict(zip(cols, r)) for r in rows]


async def _send_telegram(text: str) -> None:
    from core.config import Config
    cfg = Config()
    if not cfg.TELEGRAM_BOT_TOKEN or not cfg.TELEGRAM_CHAT_ID:
        logger.warning("No TELEGRAM creds — printing instead:\n%s", text)
        return
    from monitoring.notifier import TelegramNotifier
    notifier = TelegramNotifier(cfg.TELEGRAM_BOT_TOKEN, cfg.TELEGRAM_CHAT_ID)
    await notifier.send(text, msg_type="sports_silence_watch")
    logger.info(f"Sent Telegram ({len(text)} chars)")


def _format_fire(strategy: str, fire: dict) -> str:
    return (
        f"🎯 <b>{strategy}</b> fired\n"
        f"  {fire['market_id']}\n"
        f"  {fire['direction']} sz=${fire['size']:.0f} px={fire['price']:.3f} "
        f"({fire['status']})\n"
        f"  ts={fire['timestamp'][:19]}"
    )


def _format_final_summary(start: datetime, totals: dict[str, int],
                         last_fire_ts: dict[str, str]) -> str:
    lines = [
        f"📊 <b>12h sports silence watch — final summary</b>",
        f"Started: {start.strftime('%Y-%m-%d %H:%M UTC')}",
        f"Window: 12h, 30-min polling × 24 ticks",
        f"",
    ]
    silent = [s for s, n in totals.items() if n == 0]
    fired = {s: n for s, n in totals.items() if n > 0}
    if fired:
        lines.append(f"<b>✅ Fired during watch</b>")
        for s, n in sorted(fired.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {s}: {n} fires")
        lines.append("")
    if silent:
        lines.append(f"<b>🔴 Still silent (full 12h, 0 fires)</b>")
        for s in silent:
            last = last_fire_ts.get(s) or "never"
            lines.append(f"  {s} | last ever: {last[:19] if last != 'never' else 'never'}")
    return "\n".join(lines)


async def main():
    if not os.path.exists(DB_PATH):
        logger.error(f"DB not found: {DB_PATH}")
        sys.exit(1)

    start = _now()
    cursor = start
    totals = {s: 0 for s in WATCH_LIST}

    # Snapshot "last ever fire" for each strategy at start (for the final summary).
    conn = sqlite3.connect(DB_PATH)
    placeholders = ",".join("?" * len(ACTIVITY))
    last_fire_ts = {}
    for s in WATCH_LIST:
        last_fire_ts[s] = conn.execute(
            f"SELECT MAX(timestamp) FROM trades WHERE strategy=? AND status IN ({placeholders})",
            (s, *ACTIVITY),
        ).fetchone()[0]
    conn.close()

    startup = (
        f"👀 <b>12h sports silence watch armed</b>\n"
        f"Watching {len(WATCH_LIST)} strategies, 30-min ticks, "
        f"{NUM_TICKS} total. Started {start.strftime('%H:%M UTC')}.\n\n"
        + "\n".join(f"  • <code>{s}</code>" for s in WATCH_LIST)
    )
    await _send_telegram(startup)

    for tick in range(1, NUM_TICKS + 1):
        await asyncio.sleep(TICK_SECS)
        prev_cursor = cursor
        cursor = _now()
        try:
            conn = sqlite3.connect(DB_PATH)
            for s in WATCH_LIST:
                fires = _new_fires_since(conn, s, _iso(prev_cursor))
                for f in fires:
                    totals[s] += 1
                    last_fire_ts[s] = f["timestamp"]
                    await _send_telegram(_format_fire(s, f))
            conn.close()
            logger.info(
                f"tick {tick}/{NUM_TICKS} done — totals: {totals}"
            )
        except Exception as e:
            logger.exception(f"tick {tick} error: {e}")

    summary = _format_final_summary(start, totals, last_fire_ts)
    await _send_telegram(summary)
    logger.info("Watch complete.")


if __name__ == "__main__":
    asyncio.run(main())
