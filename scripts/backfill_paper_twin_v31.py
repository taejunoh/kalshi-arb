"""Backfill paper twin rows for v31 live settled trades in the last 16h.

Retroactively creates a paper_settled row for each live settled v31 trade so
Strategy Performance (Paper) has a matching observation for every live entry.
Paper size uses the strategy's intent ($3) rather than live's integer-contract
actual. Market result is inferred from the sign of live.pnl + live.direction,
since a winning direction has strictly positive PnL under calculate_pnl.

Usage:
    python3 scripts/backfill_paper_twin_v31.py           # dry-run preview
    python3 scripts/backfill_paper_twin_v31.py --apply   # write rows
"""
import argparse
import asyncio
import sqlite3
import sys

sys.path.insert(0, "/home/ubuntu/kalshi-arb")
from core.config import Config
from core.database import Database

CUTOFF = "2026-04-15T16:56:00"
DB_PATH = "/home/ubuntu/kalshi-arb/data/bot.db"


def compute_paper(price: float, direction: str, live_pnl: float, cfg: Config):
    """Return (paper_size, paper_fee, paper_pnl, result)."""
    result = direction if live_pnl > 0 else ("no" if direction == "yes" else "yes")
    size = 3.0
    count = max(1, int(size / price))
    price_c = int(price * 100)
    fee = cfg.estimate_fee(count, price_c)
    if direction == result:
        pnl = round(size * ((1.0 - price) / price) - fee, 2)
    else:
        pnl = round(-size, 2)
    return size, fee, pnl, result


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually insert rows")
    args = ap.parse_args()

    cfg = Config()
    c = sqlite3.connect(DB_PATH)
    rows = list(c.execute("""
        SELECT id, timestamp, strategy, market_id, direction, price,
               size, fee, pnl, minutes_to_start
        FROM trades
        WHERE strategy LIKE '%v31' AND status='settled' AND timestamp >= ?
        ORDER BY id
    """, (CUTOFF,)))
    print(f"Live settled v31 rows in window: {len(rows)}")

    # Guard: skip any live trade that already has a paper_settled twin
    # (matched on strategy + market_id + direction + price + timestamp proximity)
    existing = set()
    for ts, strat, mid, dir_, price in c.execute("""
        SELECT timestamp, strategy, market_id, direction, price
        FROM trades
        WHERE strategy LIKE '%v31' AND status='paper_settled' AND timestamp >= ?
    """, (CUTOFF,)):
        existing.add((strat, mid, dir_, round(price, 4)))

    summary = {}
    inserts = []
    for r in rows:
        _id, ts, strat, mid, dir_, price, live_size, live_fee, live_pnl, mts = r
        key = (strat, mid, dir_, round(price, 4))
        if key in existing:
            continue
        size, fee, pnl, result = compute_paper(price, dir_, live_pnl, cfg)
        inserts.append((ts, strat, mid, dir_, price, size, fee, pnl, "paper_settled", mts, price))
        s = summary.setdefault(strat, {"n": 0, "pnl": 0.0, "wins": 0, "losses": 0})
        s["n"] += 1
        s["pnl"] += pnl
        if pnl > 0:
            s["wins"] += 1
        elif pnl < 0:
            s["losses"] += 1

    print(f"New paper twin rows to insert: {len(inserts)} (skipped {len(rows)-len(inserts)} already present)")
    print()
    print(f"{'Strategy':25s} {'n':>4s} {'W':>3s} {'L':>3s} {'Paper PnL':>12s} {'Live PnL':>12s}")
    print("-" * 70)
    live_pnl_by_strat = {}
    for r in rows:
        live_pnl_by_strat.setdefault(r[2], 0.0)
        live_pnl_by_strat[r[2]] += r[8]
    for s in sorted(summary.keys()):
        v = summary[s]
        lp = live_pnl_by_strat.get(s, 0.0)
        print(f"{s:25s} {v['n']:>4d} {v['wins']:>3d} {v['losses']:>3d} "
              f"${v['pnl']:>+10.2f} ${lp:>+10.2f}")
    print("-" * 70)
    tot_paper = sum(v["pnl"] for v in summary.values())
    tot_live = sum(live_pnl_by_strat.values())
    print(f"{'TOTAL':25s} {sum(v['n'] for v in summary.values()):>4d} "
          f"{sum(v['wins'] for v in summary.values()):>3d} "
          f"{sum(v['losses'] for v in summary.values()):>3d} "
          f"${tot_paper:>+10.2f} ${tot_live:>+10.2f}")

    if not args.apply:
        print("\n(dry-run — pass --apply to write)")
        return

    # INSERT using the same Database helper so we don't diverge from schema
    db = Database(DB_PATH)
    await db.initialize()
    for (ts, strat, mid, dir_, price, size, fee, pnl, status, mts, realized) in inserts:
        # record_trade does not accept pre-set timestamp or status, so go raw
        await db.record_trade_raw(
            timestamp=ts, strategy=strat, market_id=mid, direction=dir_,
            price=price, size=size, fee=fee, pnl=pnl, status=status,
            minutes_to_start=mts, realized_fill_price=realized,
        ) if hasattr(db, "record_trade_raw") else None
    # Fallback: direct INSERT if helper missing
    if inserts and not hasattr(db, "record_trade_raw"):
        conn = sqlite3.connect(DB_PATH)
        conn.executemany("""
            INSERT INTO trades (timestamp, strategy, market_id, direction,
                                price, size, fee, pnl, status, minutes_to_start,
                                realized_fill_price)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, inserts)
        conn.commit()
        conn.close()
    print(f"\nInserted {len(inserts)} paper_settled rows.")


asyncio.run(main())
