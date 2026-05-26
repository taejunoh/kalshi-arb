"""WNBA-specific backtest from existing data (no ESPN predictor needed).

WNBA collector started 2026-05-26 so no historical ESPN predictor available.
Instead test signals derivable from existing odds_observations + ticker_settlements:

  A) Chalk-buy (favorite-buy) by ya band
  B) Underdog-buy by ya band
  C) Pinnacle-agreement chalk-buy: |pin - ya| ≤ 0.05 + ya ∈ favorite range
  D) Pinnacle edge: ya much lower than Pinnacle prob (Kalshi underprices favorite)

Compares to existing WNBA tennis-like favorite-longshot bias hypothesis.

Run: python3 scripts/wnba_specific_backtest.py
"""
import os, sqlite3
DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")
FEE = 0.07
LB = 60

c = sqlite3.connect(DB); c.execute("PRAGMA query_only = 1")

# Get WNBA settled tickers
cur = c.execute("""
    SELECT kalshi_ticker, result FROM ticker_settlements
    WHERE kalshi_ticker LIKE 'KXWNBAGAME-%'
      AND fetched_at >= datetime('now', ?)
      AND result IN ('yes', 'no')
""", (f"-{LB} days",))
settled = list(cur.fetchall())
print(f"WNBA settled last {LB}d: {len(settled)}")

rows = []
for tk, result in settled:
    snap = c.execute("""
        SELECT minutes_to_start, kalshi_yes_ask, pinnacle_yes_prob,
               us_yes_prob, eu_yes_prob, is_yes_home
        FROM odds_observations
        WHERE kalshi_ticker = ?
          AND minutes_to_start > 30
          AND kalshi_yes_ask IS NOT NULL
        ORDER BY minutes_to_start ASC
        LIMIT 1
    """, (tk,)).fetchone()
    if not snap: continue
    mts, ya, pin, us, eu, is_yh = snap
    rows.append({
        "ticker": tk, "ya": ya, "pin": pin, "us": us, "eu": eu,
        "is_yes_home": is_yh, "won": (result == "yes"),
    })

print(f"Usable rows: {len(rows)}\n")


def pnl_yes(r, ya):
    return (1.0 - ya - FEE) if r["won"] else (-ya - FEE)

def pnl_no(r, na):
    return (1.0 - na - FEE) if not r["won"] else (-na - FEE)


# A. Chalk-buy by ya band (bet YES at observed ya)
print("=== A. Chalk-buy YES at observed ya ===")
print(f"{'ya band':<14} {'n':>4} {'WR%':>6} {'EV/$1':>9}")
for lo, hi in [(0.50,0.60),(0.60,0.70),(0.70,0.80),(0.80,0.90),(0.90,1.00)]:
    sub = [r for r in rows if lo <= r["ya"] < hi]
    if not sub:
        print(f"{lo:.2f}-{hi:.2f}      n=0"); continue
    n = len(sub)
    w = sum(1 for r in sub if r["won"])
    p = sum(pnl_yes(r, r["ya"]) for r in sub)
    print(f"{lo:.2f}-{hi:.2f}      {n:>4} {100*w/n:>5.1f}% {p/n:>+8.3f}")

# B. Underdog-buy by ya band (bet YES at low ya)
print("\n=== B. Underdog-buy YES at low ya ===")
for lo, hi in [(0.05,0.15),(0.15,0.25),(0.25,0.35),(0.35,0.50)]:
    sub = [r for r in rows if lo <= r["ya"] < hi]
    if not sub:
        print(f"{lo:.2f}-{hi:.2f}      n=0"); continue
    n = len(sub)
    w = sum(1 for r in sub if r["won"])
    p = sum(pnl_yes(r, r["ya"]) for r in sub)
    print(f"{lo:.2f}-{hi:.2f}      {n:>4} {100*w/n:>5.1f}% {p/n:>+8.3f}")

# C. Pinnacle-agreement chalk-buy: |pin - ya| ≤ 0.05 AND ya in favorite range
print("\n=== C. Chalk-buy with Pinnacle agreement (|pin - ya| ≤ 0.05) ===")
for lo, hi in [(0.55,0.70),(0.70,0.85),(0.85,0.95)]:
    sub = [r for r in rows
           if r["pin"] is not None
           and abs(r["pin"] - r["ya"]) <= 0.05
           and lo <= r["ya"] < hi]
    if not sub:
        print(f"ya {lo:.2f}-{hi:.2f}   n=0"); continue
    n = len(sub)
    w = sum(1 for r in sub if r["won"])
    p = sum(pnl_yes(r, r["ya"]) for r in sub)
    print(f"ya {lo:.2f}-{hi:.2f}   {n:>4} {100*w/n:>5.1f}% {p/n:>+8.3f}")

# D. Pinnacle edge: Pinnacle prob >> Kalshi ya (Kalshi underprices side)
print("\n=== D. Pinnacle edge (pin >> ya): bet YES when pin - ya ≥ X ===")
for thresh in [0.03, 0.05, 0.08, 0.10]:
    sub = [r for r in rows
           if r["pin"] is not None
           and (r["pin"] - r["ya"]) >= thresh
           and 0.30 <= r["ya"] <= 0.70]
    if not sub:
        print(f"  pin-ya ≥ {thresh}: n=0"); continue
    n = len(sub)
    w = sum(1 for r in sub if r["won"])
    p = sum(pnl_yes(r, r["ya"]) for r in sub)
    print(f"  pin-ya ≥ {thresh:.2f}: n={n:>3} WR={100*w/n:>5.1f}% EV={p/n:+7.3f}")

# E. Home/away splits at chalk level
print("\n=== E. Home/away advantage in WNBA (ya 0.55-0.80) ===")
for is_home in (1, 0):
    sub = [r for r in rows if 0.55 <= r["ya"] <= 0.80 and r["is_yes_home"] == is_home]
    if not sub: continue
    n = len(sub); w = sum(1 for r in sub if r["won"])
    p = sum(pnl_yes(r, r["ya"]) for r in sub)
    label = "HOME ya=YES" if is_home else "AWAY ya=YES"
    print(f"  {label}: n={n:>3} WR={100*w/n:>5.1f}% EV={p/n:+7.3f}")

# F. Big-favorite fade (bet NO on heavy favorite — opposite of chalk)
print("\n=== F. Fade big favorites (bet NO when ya is high) ===")
for lo, hi in [(0.70,0.80),(0.80,0.90),(0.90,1.00)]:
    sub = [r for r in rows if lo <= r["ya"] < hi]
    if not sub: continue
    n = len(sub)
    na = lambda r: 1.0 - r["ya"]
    w = sum(1 for r in sub if not r["won"])
    p = sum(pnl_no(r, na(r)) for r in sub)
    print(f"  ya {lo:.2f}-{hi:.2f} fade→NO  n={n:>3} fade-WR={100*w/n:>5.1f}% EV={p/n:+7.3f}")
