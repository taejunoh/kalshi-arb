"""Backfill historical trades' fee + pnl to Kalshi's REAL fee model.

The bot recorded fee as flat $0.07/ct (estimate_fee) and, on LOSSES, recorded
pnl=-size with NO fee. Kalshi's actual fee = 0.07*p*(1-p)/ct (confirmed against
/portfolio/fills fee_cost, 2026-06-02). This rewrites fee and pnl for SETTLED
trades using config.real_fee() + the corrected loss branch (pnl_loss=-size-fee).

Win/loss is inferred from the stored pnl (loss if pnl is closer to -size than to
the win payout) — no result column exists, but this is unambiguous in practice.

DRY-RUN by default (prints the re-based book, writes nothing). Pass --apply to
write (makes a timestamped backup of the trades table first).

Usage:
    venv/bin/python scripts/backfill_real_fee.py [--db data/bot.db] [--apply]
"""
from __future__ import annotations
import argparse, sqlite3, sys, os
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.config import Config

def recompute(price, size, eff_price, stored_pnl, stored_fee, cfg):
    """Return (new_fee, new_pnl, won) using real fee + corrected accounting.
    eff_price = realized_fill_price or price (entry actually paid)."""
    p = eff_price if (eff_price and eff_price > 0) else price
    if p <= 0 or p >= 1 or not size:
        return stored_fee, stored_pnl, None
    contracts = size / p
    new_fee = cfg.real_fee(contracts, int(round(p * 100)))
    win_pnl_old = size * ((1.0 - p) / p) - (stored_fee or 0)
    loss_pnl_old = -size
    won = abs((stored_pnl or 0) - win_pnl_old) <= abs((stored_pnl or 0) - loss_pnl_old)
    new_pnl = (size * ((1.0 - p) / p) - new_fee) if won else (-size - new_fee)
    return round(new_fee, 4), round(new_pnl, 2), won

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/bot.db")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    cfg = Config()
    conn = sqlite3.connect(a.db)
    if not a.apply:
        conn.execute("PRAGMA query_only=1")
    rows = conn.execute("""SELECT id,price,size,fee,pnl,status,realized_fill_price
        FROM trades WHERE status IN ('settled','paper_settled') AND price>0 AND size>0""").fetchall()
    agg = defaultdict(lambda: [0, 0.0, 0.0])  # n, old_pnl, new_pnl  (per status)
    live = defaultdict(lambda: [0, 0.0, 0.0])
    updates = []
    for tid, price, size, fee, pnl, status, rfp in rows:
        new_fee, new_pnl, won = recompute(price, size, rfp, pnl, fee, cfg)
        if won is None:
            continue
        bucket = "LIVE" if status == "settled" else "PAPER"
        agg[bucket][0] += 1; agg[bucket][1] += (pnl or 0); agg[bucket][2] += new_pnl
        updates.append((new_fee, new_pnl, tid))
    print(f"{'bucket':8}{'n':>7}{'pnl_OLD':>12}{'pnl_REBASED':>13}{'delta':>11}")
    print("-"*51)
    for b in ("LIVE", "PAPER"):
        n, o, nw = agg[b]
        print(f"{b:8}{n:>7}{o:>12.1f}{nw:>13.1f}{nw-o:>+11.1f}")
    tn = sum(v[0] for v in agg.values()); to = sum(v[1] for v in agg.values()); tnw = sum(v[2] for v in agg.values())
    print("-"*51)
    print(f"{'TOTAL':8}{tn:>7}{to:>12.1f}{tnw:>13.1f}{tnw-to:>+11.1f}")
    print(f"\n{'(DRY-RUN — no writes)' if not a.apply else 'APPLYING...'}")
    if a.apply:
        import time
        bk = f"trades_backup_realfee"
        conn.execute(f"DROP TABLE IF EXISTS {bk}")
        conn.execute(f"CREATE TABLE {bk} AS SELECT * FROM trades")
        conn.executemany("UPDATE trades SET fee=?, pnl=? WHERE id=?", updates)
        conn.commit()
        print(f"Backed up trades -> {bk}; updated {len(updates)} rows.")

if __name__ == "__main__":
    main()
