"""Reconcile Kalshi-side fills/settlements into the local `trades` table.

Background — the `_live_limit_order` path (used by mentions_trader,
market_maker, maker_preorder, spread_scanner, econ_trader) writes to
`orders` but never to `trades`. When the order fills on Kalshi or the
market resolves, no record_trade or settle_trade hook runs. Result: PnL
attribution is broken for any strategy that posts resting limit orders.

This script reads Kalshi as source-of-truth and inserts missing rows.

Idempotency: keyed on (strategy, market_id, status). One settled row per
(strategy, market_id) pair — the Kalshi settlement aggregates net cost
and net result, so per-leg granularity isn't recoverable anyway.

Strategy attribution by ticker prefix:
  KXFEDMENTION-*   → mentions_trader
  KXCPI-* / KXGDP-* / KXUNEMPLOY-* → econ_trader
  KXFED-* (rate)   → spread_scanner
  Sports / others  → already tracked via the existing scan_and_execute
                     path; skipped here.

Run modes:
  --dry-run  (default) Print what would be inserted, no DB writes.
  --apply              Actually insert rows.
"""
import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config
from core.database import Database
from core.kalshi_client import KalshiClient

logger = logging.getLogger("reconcile")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _strategy_for_ticker(ticker: str) -> Optional[str]:
    """Map a Kalshi ticker to the strategy that owns it. Returns None for
    tickers handled by the existing scan/settle path (sports etc.)."""
    if ticker.startswith("KXFEDMENTION"):
        return "mentions_trader"
    if any(ticker.startswith(p) for p in ("KXCPI", "KXGDP", "KXUNEMPLOY")):
        return "econ_trader"
    if ticker.startswith("KXFED-"):
        return "spread_scanner"
    return None


def _settlement_to_trade_fields(s: dict) -> Optional[dict]:
    """Translate a Kalshi settlement record into trades-table fields.
    Returns None if the settlement has zero counts on both sides (no fill)."""
    no_count = float(s.get("no_count_fp") or 0)
    yes_count = float(s.get("yes_count_fp") or 0)
    no_cost = float(s.get("no_total_cost_dollars") or 0)
    yes_cost = float(s.get("yes_total_cost_dollars") or 0)
    fee = float(s.get("fee_cost") or 0)
    result = s.get("market_result")  # 'yes' | 'no'

    if no_count == 0 and yes_count == 0:
        return None
    if no_count > 0 and yes_count > 0:
        # Mixed fills on both sides — pick the larger leg as the dominant
        # position. Net cost is the position we still hold (the other side
        # netted out). Rare edge case; logged for review.
        if no_count >= yes_count:
            side = "no"; count = no_count - yes_count; cost = no_cost - yes_cost
        else:
            side = "yes"; count = yes_count - no_count; cost = yes_cost - no_cost
    elif no_count > 0:
        side = "no"; count = no_count; cost = no_cost
    else:
        side = "yes"; count = yes_count; cost = yes_cost

    if count <= 0:
        return None

    avg_price = cost / count if count > 0 else 0.0
    won = (side == result)
    payout = count * 1.0 if won else 0.0  # Kalshi pays $1 per winning contract
    pnl = round(payout - cost - fee, 2)

    return {
        "direction": side,
        "price": round(avg_price, 4),
        "size": round(cost, 2),
        "fee": round(fee, 2),
        "pnl": pnl,
        "won": won,
    }


async def reconcile(apply: bool):
    config = Config()
    db = Database()
    await db.initialize()
    client = KalshiClient(config)

    # --- Pull Kalshi truth ---
    settlements = client.get_settlements(limit=500)
    positions = client.get_positions()
    logger.info(f"Kalshi settlements fetched: {len(settlements)}")
    logger.info(f"Kalshi positions fetched: {len(positions)}")

    # --- Index existing trades for idempotency (strategy, market_id, status_class) ---
    import aiosqlite
    existing_settled: set[tuple[str, str]] = set()
    existing_filled: set[tuple[str, str]] = set()
    async with aiosqlite.connect(db.db_path) as conn:
        cursor = await conn.execute(
            "SELECT strategy, market_id, status FROM trades "
            "WHERE status IN ('settled', 'paper_settled', 'filled', 'paper_filled')"
        )
        rows = await cursor.fetchall()
    for strategy, market_id, status in rows:
        if status in ("settled", "paper_settled"):
            existing_settled.add((strategy, market_id))
        else:
            existing_filled.add((strategy, market_id))

    # --- Reconcile settlements → trades (status='settled', with pnl) ---
    inserted_settled = 0
    skipped_settled = 0
    skipped_unowned = 0
    total_pnl = 0.0
    by_strategy: dict[str, dict] = {}

    for s in settlements:
        ticker = s.get("ticker")
        if not ticker:
            continue
        strategy = _strategy_for_ticker(ticker)
        if strategy is None:
            skipped_unowned += 1
            continue
        if (strategy, ticker) in existing_settled:
            skipped_settled += 1
            continue
        fields = _settlement_to_trade_fields(s)
        if fields is None:
            continue

        agg = by_strategy.setdefault(strategy, {"n": 0, "wins": 0, "pnl": 0.0})
        agg["n"] += 1
        if fields["won"]:
            agg["wins"] += 1
        agg["pnl"] += fields["pnl"]
        total_pnl += fields["pnl"]

        action = "INSERT" if apply else "DRY"
        logger.info(
            f"  [{action}] settled {strategy:18} {ticker:35} {fields['direction']:3} "
            f"@{fields['price']:.3f} cost=${fields['size']:6.2f} fee=${fields['fee']:.2f} "
            f"pnl=${fields['pnl']:+7.2f} ({'WIN' if fields['won'] else 'LOSE'})"
        )
        if apply:
            await db.record_trade(
                strategy=strategy,
                market_id=ticker,
                direction=fields["direction"],
                price=fields["price"],
                size=fields["size"],
                fee=fields["fee"],
                pnl=fields["pnl"],
                status="settled",
            )
            inserted_settled += 1

    # --- Reconcile open positions → trades (status='filled', pnl=0) ---
    inserted_filled = 0
    skipped_filled = 0
    for p in positions:
        ticker = p.get("ticker")
        if not ticker:
            continue
        strategy = _strategy_for_ticker(ticker)
        if strategy is None:
            continue
        position_fp = float(p.get("position_fp") or 0)
        if position_fp == 0:
            continue
        if (strategy, ticker) in existing_filled or (strategy, ticker) in existing_settled:
            skipped_filled += 1
            continue
        cost = float(p.get("market_exposure_dollars") or 0)
        fee = float(p.get("fees_paid_dollars") or 0)
        side = "yes" if position_fp > 0 else "no"
        count = abs(position_fp)
        avg_price = cost / count if count > 0 else 0.0

        action = "INSERT" if apply else "DRY"
        logger.info(
            f"  [{action}] open    {strategy:18} {ticker:35} {side:3} "
            f"count={count:.0f} cost=${cost:.2f} avg_px={avg_price:.3f}"
        )
        if apply:
            await db.record_trade(
                strategy=strategy,
                market_id=ticker,
                direction=side,
                price=round(avg_price, 4),
                size=round(cost, 2),
                fee=round(fee, 2),
                pnl=0.0,
                status="filled",
            )
            inserted_filled += 1

    # --- Summary ---
    logger.info("")
    logger.info("─── Summary ───")
    logger.info(f"Settlements seen: {len(settlements)} (un-owned ticker: {skipped_unowned}, already in trades: {skipped_settled})")
    logger.info(f"Settled rows inserted: {inserted_settled}" + ("" if apply else " (DRY-RUN — no DB writes)"))
    logger.info(f"Filled rows inserted:  {inserted_filled}" + ("" if apply else " (DRY-RUN — no DB writes)"))
    logger.info("")
    for strategy, agg in sorted(by_strategy.items()):
        wr = (agg["wins"] / agg["n"] * 100) if agg["n"] > 0 else 0
        logger.info(f"  {strategy:18} n={agg['n']:3} wins={agg['wins']:3} ({wr:.0f}%) pnl=${agg['pnl']:+8.2f}")
    logger.info(f"  {'TOTAL':18} pnl=${total_pnl:+8.2f}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Insert rows. Without this flag, runs in dry-run mode.")
    args = parser.parse_args()
    asyncio.run(reconcile(apply=args.apply))


if __name__ == "__main__":
    main()
