"""B3 backtest — does crypto strategy EV vary with hour-of-day?

Hypothesis: during US sports prime-time / NFL Sundays / NBA evenings, MM
retreats from crypto markets → spreads widen → signal reliability differs.

If true, time-of-day meta-gate could revive borderline strategies OR add
filtering to deribit_tail_fade_v1 / late_close_tail_v1 (only +EV remaining).

Test on all crypto Phase 1 strategies. Bucket EV by UTC hour.
"""
import sqlite3
from collections import defaultdict
from datetime import datetime

DB = "/home/ubuntu/kalshi-arb/data/bot.db"


def parse_ts(s):
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def run():
    c = sqlite3.connect(DB)
    c.execute("PRAGMA busy_timeout=5000")

    strategies = [
        "deribit_tail_fade_v1",   # +EV PAPER
        "late_close_tail_v1",     # LIVE mild bleed
        "cme_basis_fade_v1",      # mothballed (was -EV but had E ≥0.85 +EV slice)
        "open_no_touch_v1",
        "atm_hold_v1",
        "bid_imbalance_v1",
        "bid_imbalance_fade_v1",
        "deribit_atm_fade_v1",
    ]

    for strat in strategies:
        cur = c.execute(
            """SELECT timestamp, pnl, size FROM trades
               WHERE strategy = ?
                 AND status IN ('paper_settled', 'settled', 'closed')
                 AND timestamp > datetime('now', '-30 days')""",
            (strat,),
        )

        by_hour = defaultdict(lambda: {"n": 0, "pnl": 0.0, "size": 0.0})
        for ts, pnl, size in cur:
            try:
                h = parse_ts(ts).hour
            except Exception:
                continue
            by_hour[h]["n"] += 1
            by_hour[h]["pnl"] += pnl or 0
            by_hour[h]["size"] += size or 0

        total_n = sum(b["n"] for b in by_hour.values())
        if total_n == 0:
            continue

        print(f"\n{'='*70}")
        print(f"{strat}  (total n={total_n})")
        print(f"{'='*70}")
        print(f"  {'UTC hr':>6}  {'N':>5}  {'PnL':>8}  {'EV%':>7}  {'Note'}")

        # Find best/worst hours
        sorted_hrs = sorted(by_hour.keys())
        positives = []
        worst = []
        for h in sorted_hrs:
            b = by_hour[h]
            if b["size"] == 0:
                continue
            ev_pct = 100 * b["pnl"] / b["size"]
            note = ""
            if ev_pct > 1:
                note = "+EV"
                positives.append((h, b["n"], b["pnl"], ev_pct))
            if ev_pct < -15:
                note = "WORST"
                worst.append((h, b["n"], b["pnl"], ev_pct))
            print(f"  {h:>6}  {b['n']:>5}  ${b['pnl']:>+7.2f}  {ev_pct:>+6.2f}  {note}")

        if positives:
            print(f"\n  >>> +EV hours: {[(h, n) for h, n, _, _ in positives]}")
        if worst:
            print(f"  >>> WORST hours (EV<-15%): {[(h, n) for h, n, _, _ in worst]}")


if __name__ == "__main__":
    run()
