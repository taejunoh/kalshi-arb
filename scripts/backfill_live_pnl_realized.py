"""Backfill LIVE trade PnL using realized_fill_price.

Built 2026-05-20 after commit 48b409d fixed forward-going PnL calc, but
historical LIVE rows still hold signal-price-derived PnL. Rows already
settled by `_live_limit_ttl_order` (pre-fix) stored:

  size = filled_count × signal_price  (intended stake, not actual paid)
  pnl  = derived from signal-price formula in calculate_pnl

When realized_fill_price differs from signal (Kalshi crosses our limit
through cheaper makers), the stored PnL is systematically off. Loss rows
overstate the loss; win rows understate the gain.

This script recomputes both columns from `realized_fill_price`:

  intended_count = round(old_size / old_price)   # signal × count → count
  new_size       = intended_count × realized_fill_price  (actual paid)
  result_inferred = direction if old_pnl > 0 else opposite
  new_pnl        = calculate_pnl(direction, realized_fill_price, new_size,
                                 fee, result_inferred)

Idempotent — only touches rows where realized_fill_price is non-null AND
non-equal to current `price` column (the fingerprint of a pre-fix row).

Run modes:
  --dry-run  (default) Print what would change, no DB writes.
  --apply              Update rows.
"""
import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import Database
from core.settlement import SettlementChecker

logger = logging.getLogger("backfill_live_pnl_realized")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _calculate_pnl(direction: str, price: float, size_usdc: float,
                   fee: float, result: str) -> float:
    """Mirror of SettlementChecker.calculate_pnl — no checker instance needed."""
    if direction == result:
        return round(size_usdc * ((1.0 - price) / price) - fee, 2)
    return round(-size_usdc, 2)


async def backfill(apply: bool):
    db = Database()
    await db.initialize()
    import aiosqlite
    async with aiosqlite.connect(db.db_path, timeout=30.0) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT id, strategy, market_id, direction, price, size, fee, "
            "       pnl, realized_fill_price "
            "FROM trades "
            "WHERE status = 'settled' "
            "  AND realized_fill_price IS NOT NULL "
            "  AND realized_fill_price > 0 "
            "ORDER BY timestamp"
        )
        rows = await cur.fetchall()

    candidates = []
    for r in rows:
        old_price = r["price"]
        realized = r["realized_fill_price"]
        # Skip rows where realized matches signal — already correct or paper-like
        if abs(old_price - realized) < 0.005:
            continue
        old_size = r["size"]
        old_pnl = r["pnl"] or 0.0
        fee = r["fee"] or 0.0
        direction = r["direction"]
        if old_price <= 0:
            continue
        intended_count = round(old_size / old_price)
        if intended_count <= 0:
            continue
        new_size = round(intended_count * realized, 2)
        # Infer result from existing PnL. Cannot use `pnl > 0` alone — a deep
        # tail NO @ $0.95 win nets ~$0 (size × 0.05/0.95 - fee ≈ 0) yet is a
        # WIN. calculate_pnl returns -size_usdc on loss, so use distance from
        # the loss baseline: if pnl ≈ -size, the trade lost; otherwise it won.
        won = abs(old_pnl + old_size) > 1.0
        result = direction if won else ("no" if direction == "yes" else "yes")
        new_pnl = _calculate_pnl(direction, realized, new_size, fee, result)
        delta_pnl = round(new_pnl - old_pnl, 2)
        candidates.append({
            "id": r["id"], "strategy": r["strategy"], "market_id": r["market_id"],
            "direction": direction, "old_price": old_price,
            "realized": realized, "old_size": old_size, "new_size": new_size,
            "old_pnl": old_pnl, "new_pnl": new_pnl, "delta_pnl": delta_pnl,
            "fee": fee, "won": won, "result": result,
        })

    if not candidates:
        logger.info("No backfill candidates found (all settled LIVE rows already "
                    "have realized matching price, or no rows qualify).")
        return

    logger.info(f"Found {len(candidates)} candidate rows to backfill:")
    print(f"{'id':>5} {'strategy':25} {'market_id':35} {'dir':3} "
          f"{'old_p':>5} {'realiz':>6} {'old_sz':>7} {'new_sz':>7} "
          f"{'old_pnl':>8} {'new_pnl':>8} {'Δpnl':>7}")
    total_old = 0.0
    total_new = 0.0
    by_strat: dict[str, float] = {}
    for c in candidates:
        print(f"{c['id']:>5} {c['strategy']:25} {c['market_id']:35} {c['direction']:3} "
              f"{c['old_price']:>5.3f} {c['realized']:>6.3f} "
              f"{c['old_size']:>7.2f} {c['new_size']:>7.2f} "
              f"{c['old_pnl']:>+8.2f} {c['new_pnl']:>+8.2f} {c['delta_pnl']:>+7.2f}")
        total_old += c["old_pnl"]
        total_new += c["new_pnl"]
        by_strat[c["strategy"]] = by_strat.get(c["strategy"], 0.0) + c["delta_pnl"]

    print()
    print(f"Total old_pnl: ${total_old:+.2f}")
    print(f"Total new_pnl: ${total_new:+.2f}")
    print(f"Net delta:     ${total_new - total_old:+.2f}")
    print()
    print("Per-strategy delta:")
    for strat, delta in sorted(by_strat.items(), key=lambda x: x[1]):
        print(f"  {strat:30} {delta:>+8.2f}")

    if not apply:
        print()
        logger.info("DRY-RUN — no DB writes. Re-run with --apply to commit.")
        return

    async with aiosqlite.connect(db.db_path, timeout=30.0) as conn:
        for c in candidates:
            await conn.execute(
                "UPDATE trades SET size = ?, pnl = ? WHERE id = ?",
                (c["new_size"], c["new_pnl"], c["id"]),
            )
        await conn.commit()
    logger.info(f"Applied: updated {len(candidates)} rows.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true",
                   help="Commit the recompute (default: dry-run only).")
    args = p.parse_args()
    asyncio.run(backfill(apply=args.apply))


if __name__ == "__main__":
    main()
