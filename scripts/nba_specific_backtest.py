"""NBA-specific backtest (no ESPN predictor — collector forward-only).

Patterns tested:
  A. Chalk-buy by ya band (where do NBA favorites win?)
  B. Underdog-buy by ya band
  C. Pinnacle-agreement + ya band
  D. Pinnacle edge: pin - ya ≥ X (Kalshi underprices)
  E. Home/away split
  F. Fade big favorites (bet NO at high ya)
  G. Playoff vs regular: NBA Playoffs ongoing (April-June)
"""
import os, sqlite3
DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")
FEE = 0.07; LB = 60

c = sqlite3.connect(DB); c.execute("PRAGMA query_only=1")

cur = c.execute("""
    SELECT kalshi_ticker, result FROM ticker_settlements
    WHERE kalshi_ticker LIKE 'KXNBAGAME-%'
      AND fetched_at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', ?)
      AND result IN ('yes', 'no')
""", (f"-{LB} days",))
settled = list(cur.fetchall())
print(f"NBA settled last {LB}d: {len(settled)}")

rows = []
for tk, result in settled:
    snap = c.execute("""
        SELECT minutes_to_start, kalshi_yes_ask, pinnacle_yes_prob,
               is_yes_home, is_playoff
        FROM odds_observations
        WHERE kalshi_ticker = ? AND minutes_to_start > 30
          AND kalshi_yes_ask IS NOT NULL
        ORDER BY minutes_to_start ASC LIMIT 1
    """, (tk,)).fetchone()
    if not snap: continue
    mts, ya, pin, is_yh, is_po = snap
    rows.append({
        "ticker": tk, "ya": ya, "pin": pin,
        "is_yes_home": is_yh, "is_playoff": is_po,
        "won": (result == "yes"),
    })

print(f"Usable: {len(rows)}\n")

def pnl_yes(r, ya): return (1-ya-FEE) if r["won"] else (-ya-FEE)
def pnl_no(r, na):  return (1-na-FEE) if not r["won"] else (-na-FEE)

print("=== A. Chalk-buy YES at ya ===")
print(f"{'ya band':<12} {'n':>4} {'WR%':>6} {'EV/$1':>9}")
for lo, hi in [(0.50,0.60),(0.60,0.70),(0.70,0.80),(0.80,0.90),(0.90,1.00)]:
    sub = [r for r in rows if lo <= r["ya"] < hi]
    if not sub: print(f"{lo:.2f}-{hi:.2f}    n=0"); continue
    n=len(sub); w=sum(1 for r in sub if r["won"])
    p=sum(pnl_yes(r,r["ya"]) for r in sub)
    print(f"{lo:.2f}-{hi:.2f}    {n:>4} {100*w/n:>5.1f}% {p/n:>+8.3f}")

print("\n=== B. Underdog-buy YES at low ya ===")
for lo, hi in [(0.05,0.15),(0.15,0.25),(0.25,0.35),(0.35,0.50)]:
    sub = [r for r in rows if lo <= r["ya"] < hi]
    if not sub: print(f"{lo:.2f}-{hi:.2f}    n=0"); continue
    n=len(sub); w=sum(1 for r in sub if r["won"])
    p=sum(pnl_yes(r,r["ya"]) for r in sub)
    print(f"{lo:.2f}-{hi:.2f}    {n:>4} {100*w/n:>5.1f}% {p/n:>+8.3f}")

print("\n=== D. Pinnacle edge (pin - ya ≥ X) ===")
for thr in [0.03, 0.05, 0.08]:
    sub = [r for r in rows if r["pin"] is not None and (r["pin"]-r["ya"]) >= thr and 0.20 <= r["ya"] <= 0.80]
    if not sub: print(f"  pin-ya ≥ {thr}: n=0"); continue
    n=len(sub); w=sum(1 for r in sub if r["won"])
    p=sum(pnl_yes(r,r["ya"]) for r in sub)
    print(f"  pin-ya ≥ {thr:.2f}: n={n:>3} WR={100*w/n:>5.1f}% EV={p/n:+7.3f}")

print("\n=== E. Home/away (ya 0.55-0.80) ===")
for is_home in (1, 0):
    sub = [r for r in rows if 0.55 <= r["ya"] <= 0.80 and r["is_yes_home"] == is_home]
    if not sub: continue
    n=len(sub); w=sum(1 for r in sub if r["won"])
    p=sum(pnl_yes(r,r["ya"]) for r in sub)
    label = "HOME ya=YES" if is_home else "AWAY ya=YES"
    print(f"  {label}: n={n:>3} WR={100*w/n:>5.1f}% EV={p/n:+7.3f}")

print("\n=== F. Fade big favorites (bet NO at 1-ya) ===")
for lo, hi in [(0.65,0.75),(0.75,0.85),(0.85,0.95)]:
    sub = [r for r in rows if lo <= r["ya"] < hi]
    if not sub: continue
    n=len(sub); w=sum(1 for r in sub if not r["won"])
    p=sum(pnl_no(r, 1-r["ya"]) for r in sub)
    print(f"  ya {lo:.2f}-{hi:.2f} fade→NO  n={n:>3} fade-WR={100*w/n:>5.1f}% EV={p/n:+7.3f}")

print("\n=== G. Playoff vs Regular splits (ya 0.40-0.70) ===")
for is_po in (1, 0):
    sub = [r for r in rows if 0.40 <= r["ya"] <= 0.70 and r["is_playoff"] == is_po]
    if not sub: continue
    n=len(sub); w=sum(1 for r in sub if r["won"])
    p=sum(pnl_yes(r,r["ya"]) for r in sub)
    label = "PLAYOFF" if is_po else "REGULAR"
    print(f"  {label}: n={n:>3} WR={100*w/n:>5.1f}% EV={p/n:+7.3f}")
