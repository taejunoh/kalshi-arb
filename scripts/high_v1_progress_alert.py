"""30-min progress alert for deribit_tail_fade_high_v1 LIMIT mode.

Built 2026-05-23 ad-hoc to monitor the LIMIT+TTL redesign rollout (commit
a2c56bb) while user is away. Sends concise Telegram every 30 min for 3
hours starting 12:59 UTC. Timer has 6 explicit OnCalendar entries —
after the 6th fires (15:29 UTC), no more triggers match.

Differs from the daily eval script:
  - Aggregates SINCE redesign cutoff (cumulative under LIMIT mode)
  - Includes attempt counts + cancel breakdown
  - Compact format
  - No retire/promote decisions — status reporting only

Read-only on DB. Run number tracked in /tmp/high_v1_progress_runs (so
each alert shows N/6).
"""
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config
from core.database import Database

logger = logging.getLogger("high_v1_progress")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

STRATEGY = "deribit_tail_fade_high_v1"
REDESIGN_CUTOFF = datetime(2026, 5, 23, 3, 30, tzinfo=timezone.utc)
RUN_COUNT_FILE = Path("/tmp/high_v1_progress_runs")
MAX_RUNS = 6


def _read_run_count() -> int:
    try:
        return int(RUN_COUNT_FILE.read_text().strip())
    except Exception:
        return 0


def _write_run_count(n: int) -> None:
    try:
        RUN_COUNT_FILE.write_text(str(n))
    except Exception as e:
        logger.warning(f"Could not persist run count: {e}")


async def alert():
    run_n = _read_run_count() + 1
    _write_run_count(run_n)

    config = Config()
    db = Database()
    await db.initialize()

    import aiosqlite
    cutoff_iso = REDESIGN_CUTOFF.isoformat()
    async with aiosqlite.connect(db.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        rows_cur = await conn.execute(
            "SELECT timestamp, market_id, price, size, pnl, status "
            "FROM trades WHERE strategy = ? AND timestamp >= ? "
            "ORDER BY timestamp",
            (STRATEGY, cutoff_iso),
        )
        rows = [dict(r) for r in await rows_cur.fetchall()]

    # Split by status
    settled = [r for r in rows if r["status"] in ("paper_settled", "settled")]
    filled = [r for r in rows if r["status"] in ("paper_filled", "filled")]
    cancelled = [r for r in rows if r["status"] == "cancelled"]
    placing = [r for r in rows if r["status"] == "placing"]

    total_attempts = len(rows)
    pnl_total = sum(r["pnl"] for r in settled)
    avg_fill_ct = 0.0
    if settled:
        avg_fill_ct = sum(r["size"] / r["price"] for r in settled if r["price"] > 0) / len(settled)
    fill_rate = (len(settled) + len(filled)) / total_attempts * 100 if total_attempts > 0 else 0

    now = datetime.now(timezone.utc)
    elapsed_h = (now - REDESIGN_CUTOFF).total_seconds() / 3600

    lines = [
        f"⏱ <b>high_v1 progress {run_n}/{MAX_RUNS}</b>",
        f"<i>{now.strftime('%H:%M UTC')} · since redesign +{elapsed_h:.1f}h</i>",
        "",
        f"Attempts: {total_attempts} · Settled: {len(settled)} · Cancelled: {len(cancelled)} · Placing: {len(placing)}",
        f"Fill rate: {fill_rate:.1f}% · Avg fill: {avg_fill_ct:.1f} ct",
        f"PnL: ${pnl_total:+.2f}",
    ]
    if cancelled:
        # FOK detection — under LIMIT, cancels are TTL-expires (unfilled), not FOK rejects
        lines.append(f"<i>{len(cancelled)} cancels (TTL expiry on LIMIT order, not FOK)</i>")
    if not rows:
        lines.append("<i>No fires yet. Observer may be quiet or gates filtering.</i>")

    msg = "\n".join(lines)
    print(msg.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", ""))

    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
        from monitoring.notifier import TelegramNotifier
        notifier = TelegramNotifier(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
        await notifier.send(msg, msg_type=f"high_v1_progress_{run_n}")
        logger.info(f"Telegram sent (run {run_n}/{MAX_RUNS})")
    else:
        logger.warning("Telegram credentials missing")


def main():
    asyncio.run(alert())


if __name__ == "__main__":
    main()
