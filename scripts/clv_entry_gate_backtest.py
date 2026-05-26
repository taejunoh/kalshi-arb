"""CLV entry-gate backtest — which entry-time features predict +CLV?

Pulls all paper sports trades with both entry + closing clv_snapshots,
computes per-trade CLV, and bins by various entry features to identify
which features predict +CLV (and thus +PnL).

Features tested:
  - Pinnacle-Kalshi gap at entry (pin_yes_prob - entry yes_ask)
  - Minutes-to-start at entry
  - Strategy-level avg CLV
  - n_books at entry (eu + us)
  - Entry ya range
"""
import os, sqlite3
from collections import defaultdict

DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")
LB = 30

c = sqlite3.connect(DB); c.execute("PRAGMA query_only=1")

cur = c.execute("""
    SELECT t.id, t.strategy, t.market_id, t.direction, t.price, t.pnl, t.status,
           e.pinnacle_yes_prob, e.snapshot_time,
           cl.yes_ask AS c_yes, cl.no_ask AS c_no
    FROM trades t
    JOIN clv_snapshots e ON e.trade_id = t.id AND e.snapshot_at = 'entry'
    JOIN clv_snapshots cl ON cl.trade_id = t.id AND cl.snapshot_at = 'closing'
    WHERE t.status IN ('settled','paper_settled','paper_filled','filled')
      AND t.timestamp >= datetime('now', ?)
      AND e.pinnacle_yes_prob IS NOT NULL
      AND cl.yes_ask IS NOT NULL
""", (f"-{LB} days",))

rows = []
for tid, strat, mkt, dir_, price, pnl, status, e_pin, e_snap_t, c_yes, c_no in cur.fetchall():
    if dir_ == "yes":
        clv = c_yes - price
    else:
        clv = c_no - (1 - price) if price < 1 else None
    if clv is None: continue
    pin_gap = (e_pin - price) if dir_ == "yes" else (price - e_pin)  # entry "edge" toward pinnacle
    rows.append({
        "strat": strat, "dir": dir_, "price": price, "pnl": pnl,
        "clv": clv, "pin_gap": pin_gap, "settled": status in ("settled","paper_settled"),
        "e_pin": e_pin, "c_yes": c_yes, "c_no": c_no,
    })

print(f"Trades with full CLV snapshots last {LB}d: {len(rows)}")
settled = [r for r in rows if r["settled"]]
print(f"  Settled (have realized PnL): {len(settled)}\n")


def stat(sub, label):
    if not sub:
        print(f"  {label:<30} n=0"); return
    n = len(sub)
    avg_clv = sum(r["clv"] for r in sub) / n
    clv_pos = sum(1 for r in sub if r["clv"] > 0)
    realized = [r for r in sub if r["settled"] and r["pnl"] is not None]
    if realized:
        total_pnl = sum(r["pnl"] for r in realized)
        ev = total_pnl / len(realized)
        print(f"  {label:<30} n={n:>4}  CLV+ {100*clv_pos/n:>5.1f}%  avgCLV={avg_clv:+7.4f}  realized n={len(realized):>3} EV={ev:+7.3f}")
    else:
        print(f"  {label:<30} n={n:>4}  CLV+ {100*clv_pos/n:>5.1f}%  avgCLV={avg_clv:+7.4f}  (no realized)")


print("=== Overall ===")
stat(rows, "ALL")

print("\n=== By Pinnacle-Kalshi gap at entry ===")
print("    (gap > 0 = Pinnacle thinks our side more likely than Kalshi)")
for lo, hi in [(-1.0, -0.05), (-0.05, -0.02), (-0.02, 0.02), (0.02, 0.05), (0.05, 1.0)]:
    sub = [r for r in rows if lo <= r["pin_gap"] < hi]
    stat(sub, f"pin_gap ∈ [{lo:+.2f}, {hi:+.2f})")

print("\n=== By entry price band ===")
for lo, hi in [(0.0, 0.20), (0.20, 0.40), (0.40, 0.60), (0.60, 0.80), (0.80, 1.0)]:
    sub = [r for r in rows if lo <= r["price"] < hi]
    stat(sub, f"price ∈ [{lo:.2f}, {hi:.2f})")

print("\n=== Top strategies by avg CLV (n≥30) ===")
by_strat = defaultdict(list)
for r in rows:
    by_strat[r["strat"]].append(r)
ranked = []
for s, sub in by_strat.items():
    if len(sub) < 30: continue
    avg_clv = sum(r["clv"] for r in sub) / len(sub)
    ranked.append((s, len(sub), avg_clv))
for s, n, avg_clv in sorted(ranked, key=lambda x: -x[2])[:15]:
    realized = [r for r in by_strat[s] if r["settled"] and r["pnl"] is not None]
    if realized:
        ev = sum(r["pnl"] for r in realized) / len(realized)
        print(f"  {s:<35} n={n:>4} avgCLV={avg_clv:+7.4f} realized_EV={ev:+7.3f}")
    else:
        print(f"  {s:<35} n={n:>4} avgCLV={avg_clv:+7.4f}")
