"""MLB lineup confirmation gate backtest.

For each settled MLB game, check whether the starter_home / starter_away
were populated at pre-game snapshot, then split realized PnL by:
  - Both confirmed (final lineup set ~3h pre-game in MLB)
  - One side confirmed
  - Neither confirmed (probable pitchers only — usually changes ≥24h out)

Tests whether betting only on games with confirmed lineups improves EV.
"""
import os, sqlite3
DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")
FEE = 0.07
LB = 60

c = sqlite3.connect(DB); c.execute("PRAGMA query_only=1")

cur = c.execute("""
    SELECT kalshi_ticker, result FROM ticker_settlements
    WHERE kalshi_ticker LIKE 'KXMLBGAME-%'
      AND fetched_at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', ?)
      AND result IN ('yes','no')
""", (f"-{LB} days",))
settled = list(cur.fetchall())
print(f"MLB settled last {LB}d: {len(settled)}")

rows = []
for tk, result in settled:
    snap = c.execute("""
        SELECT minutes_to_start, kalshi_yes_ask, starter_home, starter_away, pinnacle_yes_prob
        FROM odds_observations
        WHERE kalshi_ticker = ?
          AND minutes_to_start > 30
          AND kalshi_yes_ask IS NOT NULL
        ORDER BY minutes_to_start ASC
        LIMIT 1
    """, (tk,)).fetchone()
    if not snap: continue
    mts, ya, s_h, s_a, pin = snap
    won = (result == "yes")
    h_conf = s_h is not None and s_h != ""
    a_conf = s_a is not None and s_a != ""
    rows.append({
        "ya": ya, "won": won, "mts": mts,
        "h_conf": h_conf, "a_conf": a_conf,
        "both_conf": h_conf and a_conf,
        "any_conf": h_conf or a_conf,
        "pin": pin,
    })

print(f"Usable: {len(rows)}\n")


def stat(sub, label):
    if not sub:
        print(f"  {label:<30} n=0"); return
    n = len(sub)
    w = sum(1 for r in sub if r["won"])
    # Just take YES bet at ya (representative — chalk thesis)
    p = sum((1-r["ya"]-FEE) if r["won"] else (-r["ya"]-FEE) for r in sub)
    avg_mts = sum(r["mts"] for r in sub) / n
    avg_ya = sum(r["ya"] for r in sub) / n
    print(f"  {label:<30} n={n:>4}  WR={100*w/n:>5.1f}%  EV(YES)={p/n:+7.3f}  avg_mts={avg_mts:>4.0f}  avg_ya={avg_ya:.3f}")


print("=== Lineup confirmation split (all MLB) ===")
stat(rows, "ALL")
stat([r for r in rows if r["both_conf"]], "BOTH starters confirmed")
stat([r for r in rows if r["any_conf"] and not r["both_conf"]], "ONE side confirmed")
stat([r for r in rows if not r["any_conf"]], "NEITHER confirmed (probable)")

print("\n=== Same splits, but filtered to ya ∈ [0.40, 0.70] mid-range ===")
mid = [r for r in rows if 0.40 <= r["ya"] <= 0.70]
print(f"  (n={len(mid)} in mid-range)")
stat(mid, "ALL mid-range")
stat([r for r in mid if r["both_conf"]], "BOTH confirmed mid")
stat([r for r in mid if not r["any_conf"]], "NEITHER confirmed mid")

print("\n=== Pin edge × lineup confirmation (ya 0.40-0.70) ===")
def pin_edge_yes(r):
    return r["pin"] is not None and (r["pin"] - r["ya"]) >= 0.03
sub_edge_conf = [r for r in mid if pin_edge_yes(r) and r["both_conf"]]
sub_edge_unconf = [r for r in mid if pin_edge_yes(r) and not r["any_conf"]]
stat(sub_edge_conf,   "Pin+edge AND both confirmed")
stat(sub_edge_unconf, "Pin+edge but probable only")

print("\n=== MTS distribution by lineup status ===")
for status_lbl, cohort in [
    ("BOTH confirmed", [r for r in rows if r["both_conf"]]),
    ("ONE side", [r for r in rows if r["any_conf"] and not r["both_conf"]]),
    ("NEITHER", [r for r in rows if not r["any_conf"]]),
]:
    if not cohort: continue
    mts_vals = sorted(r["mts"] for r in cohort)
    median = mts_vals[len(mts_vals)//2]
    print(f"  {status_lbl:<20} n={len(cohort):>4}  median_mts={median:.0f}  min={mts_vals[0]:.0f}  max={mts_vals[-1]:.0f}")
