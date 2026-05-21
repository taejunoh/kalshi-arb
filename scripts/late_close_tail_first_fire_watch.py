"""One-shot Telegram watcher for late_close_tail_v1's first LIVE fire.

Background: late_close_tail_v1 was promoted paper → LIVE on 2026-05-21 PM
(commit aad7708). User wants a Telegram alert the moment the FIRST real-money
fill lands so they don't have to babysit the dashboard.

Behavior:
  • Records baseline MAX(id) for the strategy at startup (so prior paper
    fills are not counted).
  • Polls trades table every POLL_SECONDS for new rows with status in
    ('filled', 'settled', 'limit_ttl_unfilled').
  • On first hit: sends Telegram with trade details + exits 0 (one-shot).
  • If MAX_WAIT_HOURS elapse with no hit: sends a "still silent" Telegram
    so the user knows the watcher is alive but nothing fired — then exits 0.

Run-as-systemd one-shot:
  sudo systemctl start kalshi-late-close-tail-firewatch
  # or interactively:
  cd /home/ubuntu/kalshi-arb && venv/bin/python3 scripts/late_close_tail_first_fire_watch.py
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config

STRATEGY_NAME = "late_close_tail_v1"
WATCH_STATUSES = ("filled", "settled", "limit_ttl_unfilled")
DEFAULT_POLL_SECONDS = 60
DEFAULT_MAX_WAIT_HOURS = 6.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("late_close_tail_firewatch")


def _baseline_max_id(db_path: str) -> int:
    """Record the highest trade id at startup so we only alert on NEW rows."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            """SELECT COALESCE(MAX(id), 0) FROM trades
               WHERE strategy = ? AND status IN ('filled', 'settled', 'limit_ttl_unfilled')""",
            (STRATEGY_NAME,),
        ).fetchone()
    finally:
        conn.close()
    return int(row[0] or 0)


def _check_new_fires(db_path: str, baseline_id: int) -> list[dict]:
    """Return any trades.id > baseline_id for STRATEGY_NAME with relevant status."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """SELECT id, timestamp, market_id, direction, price, size, fee, pnl, status, realized_fill_price
               FROM trades
               WHERE strategy = ? AND id > ?
                 AND status IN ('filled', 'settled', 'limit_ttl_unfilled')
               ORDER BY id ASC""",
            (STRATEGY_NAME, baseline_id),
        ).fetchall()
    finally:
        conn.close()
    out: list[dict] = []
    for r in rows:
        out.append({
            "id": r[0],
            "ts": r[1],
            "market_id": r[2],
            "direction": r[3],
            "price": r[4],
            "size": r[5],
            "fee": r[6],
            "pnl": r[7],
            "status": r[8],
            "realized_fill_price": r[9],
        })
    return out


def _format_alert(trade: dict) -> str:
    realized = trade.get("realized_fill_price")
    realized_s = f"  realized fill: ${realized:.3f}" if realized is not None else ""
    pnl = trade.get("pnl")
    pnl_s = f"  pnl: ${pnl:+.2f}" if pnl is not None else ""
    return (
        "🎯 <b>late_close_tail_v1 FIRST LIVE FIRE</b>\n"
        f"  trade_id: {trade['id']}  status: {trade['status']}\n"
        f"  ticker: <code>{trade['market_id']}</code>\n"
        f"  side: {trade['direction']}  price: ${trade['price']:.3f}  size: ${trade['size']:.2f}\n"
        f"  fee: ${trade.get('fee', 0):.2f}{pnl_s}{realized_s}\n"
        f"  ts: {trade['ts']}"
    )


def _format_silent_alert(elapsed_hours: float, baseline_id: int) -> str:
    return (
        "⏱ <b>late_close_tail_v1 watcher: no fire yet</b>\n"
        f"  watched for {elapsed_hours:.1f}h since baseline trade_id={baseline_id}\n"
        f"  daily_live_audit (13:00 UTC) will pick up next."
    )


async def _send_telegram(config: Config, message: str, msg_type: str) -> None:
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        logger.warning("Telegram credentials missing — printing instead:\n%s", message)
        return
    from monitoring.notifier import TelegramNotifier
    notifier = TelegramNotifier(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
    await notifier.send(message, msg_type=msg_type)


async def watch(db_path: str, poll_secs: float, max_wait_hours: float) -> int:
    config = Config()
    baseline_id = _baseline_max_id(db_path)
    logger.info(
        "watching %s for first LIVE fire | db=%s baseline_id=%d poll=%ss max_wait=%sh",
        STRATEGY_NAME, db_path, baseline_id, poll_secs, max_wait_hours,
    )
    start_ts = datetime.now(timezone.utc)
    deadline_secs = max_wait_hours * 3600.0

    while True:
        elapsed = (datetime.now(timezone.utc) - start_ts).total_seconds()
        if elapsed > deadline_secs:
            elapsed_h = elapsed / 3600.0
            logger.info("max wait reached (%.2fh) — sending silent alert + exiting.", elapsed_h)
            await _send_telegram(
                config, _format_silent_alert(elapsed_h, baseline_id), "bot_status"
            )
            return 0
        try:
            fires = _check_new_fires(db_path, baseline_id)
        except sqlite3.OperationalError as e:
            # DB might be momentarily locked by the bot — back off and retry
            logger.warning("sqlite read failed: %s — backing off", e)
            await asyncio.sleep(poll_secs)
            continue
        if fires:
            first = fires[0]
            logger.info("FIRST FIRE detected: trade_id=%d ticker=%s status=%s",
                        first["id"], first["market_id"], first["status"])
            # msg_type=trade goes through UNTHROTTLED_MSG_TYPES so we don't lose
            # the alert to monitoring/notifier.py's per-type rate limit.
            await _send_telegram(config, _format_alert(first), "trade")
            return 0
        await asyncio.sleep(poll_secs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/bot.db")
    ap.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    ap.add_argument("--max-wait-hours", type=float, default=DEFAULT_MAX_WAIT_HOURS)
    args = ap.parse_args()
    if not os.path.exists(args.db):
        logger.error("DB not found at %s", args.db)
        return 1
    return asyncio.run(watch(args.db, args.poll_seconds, args.max_wait_hours))


if __name__ == "__main__":
    raise SystemExit(main())
