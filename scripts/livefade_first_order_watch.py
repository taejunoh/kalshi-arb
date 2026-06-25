"""Timer-driven Telegram watcher for sports_tennis_live_fade_v1's first REAL order.

Background: tennis_live_fade_v1 was promoted paper → LIVE on 2026-06-25 18:42 UTC
(commit 03c3003, user eval-override). It's an in-play strategy on thin live books,
so — like tennis_underdog_v1 — the first real-money event is likely a *placed-then-
cancelled* limit order (no-fill), not a fill. The user wants a Telegram alert the
moment the FIRST real order reaches Kalshi, and again on the first actual FILL.

Real-order statuses (paper twins NEVER produce these — they are instant-fill sims
logged as paper_filled/paper_settled):
  • cancelled / limit_ttl_unfilled → real limit order rested on Kalshi, TTL expired
    unfilled. Proves the live order path works end-to-end (plumbing).  [underdog: 13]
  • filled / settled               → real order actually FILLED.  (the real milestone)

Two independent one-time alerts, tracked in a persistent JSON state file so the
systemd *timer* can re-run this cheaply every few minutes without re-alerting:
  • first real ORDER (any real status)  → 📡
  • first real FILL  (filled/settled)   → 🎯

Run-as-systemd oneshot (driven by kalshi-livefade-firstorder-watch.timer):
  sudo systemctl start kalshi-livefade-firstorder-watch.service
Interactively:
  cd /home/ubuntu/kalshi-arb && venv/bin/python3 scripts/livefade_first_order_watch.py
One-time "watcher armed" confirmation (sends a single Telegram, then exits):
  venv/bin/python3 scripts/livefade_first_order_watch.py --arm-notify
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config

STRATEGY_NAME = "sports_tennis_live_fade_v1"
ORDER_STATUSES = ("filled", "settled", "cancelled", "limit_ttl_unfilled")
FILL_STATUSES = ("filled", "settled")
DEFAULT_DB = "data/bot.db"
DEFAULT_STATE = "data/.livefade_firstorder_watch.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("livefade_firstorder_watch")


def _load_state(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("state read failed (%s) — treating as fresh", e)
    return {}


def _save_state(path: str, state: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def _all_max_id(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM trades WHERE strategy = ?", (STRATEGY_NAME,)
        ).fetchone()
    finally:
        conn.close()
    return int(row[0] or 0)


def _new_real_rows(db_path: str, baseline_id: int) -> list[dict]:
    """Real-order rows (NOT paper twins) with id > baseline, oldest first."""
    placeholders = ",".join("?" * len(ORDER_STATUSES))
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            f"""SELECT id, timestamp, market_id, direction, price, size, fee, pnl, status,
                       realized_fill_price
                FROM trades
                WHERE strategy = ? AND id > ? AND status IN ({placeholders})
                ORDER BY id ASC""",
            (STRATEGY_NAME, baseline_id, *ORDER_STATUSES),
        ).fetchall()
    finally:
        conn.close()
    keys = ("id", "ts", "market_id", "direction", "price", "size", "fee", "pnl",
            "status", "realized_fill_price")
    return [dict(zip(keys, r)) for r in rows]


def _fmt(trade: dict, kind: str) -> str:
    head = ("🎯 <b>tennis_live_fade_v1 — FIRST REAL FILL</b>" if kind == "fill"
            else "📡 <b>tennis_live_fade_v1 — FIRST REAL ORDER on Kalshi</b>")
    note = ("  ✅ real order FILLED — end-to-end live execution confirmed."
            if kind == "fill"
            else "  real limit order reached Kalshi (status="
                 f"{trade['status']}); fill TBD. Plumbing confirmed.")
    realized = trade.get("realized_fill_price")
    realized_s = f"\n  realized fill: ${realized:.3f}" if realized is not None else ""
    pnl = trade.get("pnl")
    pnl_s = f"  pnl: ${pnl:+.2f}" if pnl is not None else ""
    return (
        f"{head}\n"
        f"  trade_id: {trade['id']}  status: {trade['status']}\n"
        f"  ticker: <code>{trade['market_id']}</code>\n"
        f"  side: {trade['direction']}  price: ${trade['price']:.3f}  "
        f"size: ${trade['size']:.2f}\n"
        f"  fee: ${trade.get('fee', 0) or 0:.2f}{pnl_s}{realized_s}\n"
        f"  ts: {trade['ts']}\n"
        f"{note}"
    )


async def _send(config: Config, message: str) -> bool:
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        logger.warning("Telegram creds missing — printing:\n%s", message)
        return False
    from monitoring.notifier import TelegramNotifier
    notifier = TelegramNotifier(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
    # msg_type=trade → unthrottled, so we never lose the alert to the rate limiter.
    await notifier.send(message, msg_type="trade")
    return True


async def run(db_path: str, state_path: str, arm_notify: bool) -> int:
    config = Config()
    state = _load_state(state_path)

    # First ever run: pin baseline to current MAX(id) so the 95 pre-existing paper
    # rows (and anything before promotion) are excluded; only NEW real orders alert.
    if "baseline_max_id" not in state:
        state["baseline_max_id"] = _all_max_id(db_path)
        logger.info("initialized baseline_max_id=%d", state["baseline_max_id"])

    if arm_notify:
        ok = await _send(config,
            "🟢 <b>tennis_live_fade_v1 first-order watcher ARMED</b>\n"
            f"  baseline trade_id={state['baseline_max_id']}  poll=5min\n"
            "  will ping on first real ORDER (cancelled/filled), then on first FILL.")
        state["armed_notified"] = True
        _save_state(state_path, state)
        logger.info("arm-notify sent=%s", ok)
        return 0

    try:
        rows = _new_real_rows(db_path, int(state["baseline_max_id"]))
    except sqlite3.OperationalError as e:
        logger.warning("sqlite read failed (%s) — will retry next tick", e)
        return 0

    if not rows:
        logger.info("no real orders yet (baseline_id=%d)", state["baseline_max_id"])
        _save_state(state_path, state)
        return 0

    changed = False
    # First real ORDER (any real status)
    if not state.get("alerted_order_id"):
        first = rows[0]
        kind = "fill" if first["status"] in FILL_STATUSES else "order"
        await _send(config, _fmt(first, kind))
        state["alerted_order_id"] = first["id"]
        if kind == "fill":
            state["alerted_fill_id"] = first["id"]
        logger.info("ALERTED first real order: id=%d status=%s", first["id"], first["status"])
        changed = True

    # First real FILL (filled/settled) — separate milestone if the order alert was a no-fill
    if not state.get("alerted_fill_id"):
        fills = [r for r in rows if r["status"] in FILL_STATUSES]
        if fills:
            f0 = fills[0]
            await _send(config, _fmt(f0, "fill"))
            state["alerted_fill_id"] = f0["id"]
            logger.info("ALERTED first real fill: id=%d", f0["id"])
            changed = True

    _save_state(state_path, state)
    return 0 if changed or rows else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--state", default=DEFAULT_STATE)
    ap.add_argument("--arm-notify", action="store_true",
                    help="send a one-time 'watcher armed' Telegram and exit")
    args = ap.parse_args()
    if not os.path.exists(args.db):
        logger.error("DB not found at %s", args.db)
        return 1
    return asyncio.run(run(args.db, args.state, args.arm_notify))


if __name__ == "__main__":
    raise SystemExit(main())
