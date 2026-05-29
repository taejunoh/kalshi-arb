"""Backtest 2 unattached Phase 0 observers (5/26 audit follow-up).

Targets:
- lognormal_fair_dev (n=97k, 60% flip WR observed)
- strike_ladder_literal (n=42k, 61% flip WR observed)

Each settled signal: known predicted_outcome + actual_outcome + price at signal time.
Compute per-contract PnL with $0.07 flat Kalshi fee. Aggregate by price band
and signal type/feature to find +EV cohort (if any).

Run locally vs EC2 DB via SSH; this is run on EC2.
"""
import json
import sqlite3
from collections import defaultdict

FEE_PER_CT = 0.07
DB = "/home/ubuntu/kalshi-arb/data/bot.db"


def pnl(pred, ya, na, actual):
    """Per-contract PnL after $0.07 fee."""
    if pred == "yes":
        price = ya
        win = (actual == "yes")
    else:
        price = na
        win = (actual == "no")
    if price is None or price <= 0 or price >= 1:
        return None
    if win:
        return 1.0 - price - FEE_PER_CT
    return -price - FEE_PER_CT


def get_price(observer, features):
    """Extract (ya, na) from features_json. Returns (ya, na) or (None, None)."""
    if observer == "lognormal_fair_dev":
        return features.get("ya"), features.get("na")
    if observer == "strike_ladder_literal":
        return features.get("target_ya"), features.get("target_na")
    return None, None


def band_of(price):
    if price is None:
        return "X"
    if price < 0.05: return "A_<0.05"
    if price < 0.10: return "B_0.05-0.10"
    if price < 0.20: return "C_0.10-0.20"
    if price < 0.35: return "D_0.20-0.35"
    if price < 0.50: return "E_0.35-0.50"
    if price < 0.65: return "F_0.50-0.65"
    if price < 0.80: return "G_0.65-0.80"
    if price < 0.93: return "H_0.80-0.93"
    return "I_>=0.93"


def run(observer):
    c = sqlite3.connect(DB)
    c.execute("PRAGMA busy_timeout=5000")

    cur = c.execute(
        """SELECT predicted_outcome, actual_outcome, features_json, market_id
           FROM crypto_observer_signals
           WHERE observer = ?
             AND actual_outcome IS NOT NULL
             AND ts > strftime('%Y-%m-%dT%H:%M:%S', 'now','-30 days')""",
        (observer,),
    )

    # Aggregations
    pooled_n = 0
    pooled_pnl = 0.0
    pooled_wins = 0
    rejected = 0

    bands = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})
    flip_bands = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})

    sides = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})

    for pred, actual, features_json, market_id in cur:
        try:
            f = json.loads(features_json)
        except Exception:
            rejected += 1
            continue
        ya, na = get_price(observer, f)
        if ya is None or na is None:
            rejected += 1
            continue

        # Original direction
        p = pnl(pred, ya, na, actual)
        if p is None:
            rejected += 1
            continue
        pooled_n += 1
        pooled_pnl += p
        if p > 0:
            pooled_wins += 1
        price = ya if pred == "yes" else na
        b = band_of(price)
        bands[b]["n"] += 1
        bands[b]["pnl"] += p
        if p > 0:
            bands[b]["wins"] += 1
        sides[pred]["n"] += 1
        sides[pred]["pnl"] += p
        if p > 0:
            sides[pred]["wins"] += 1

        # Flipped direction (test if observer is contrarian)
        flip_pred = "no" if pred == "yes" else "yes"
        fp = pnl(flip_pred, ya, na, actual)
        if fp is not None:
            flip_price = ya if flip_pred == "yes" else na
            fb = band_of(flip_price)
            flip_bands[fb]["n"] += 1
            flip_bands[fb]["pnl"] += fp
            if fp > 0:
                flip_bands[fb]["wins"] += 1

    c.close()

    print(f"\n========== {observer} ==========")
    print(f"Rejected (bad features): {rejected}")
    print()
    print(f"POOLED (original direction): n={pooled_n} "
          f"PnL=${pooled_pnl:+.2f} "
          f"EV/ct=${pooled_pnl/pooled_n if pooled_n else 0:+.4f} "
          f"WR={100*pooled_wins/pooled_n if pooled_n else 0:.1f}%")

    print("\n--- By predicted side (original) ---")
    for s, st in sorted(sides.items()):
        if st["n"] == 0: continue
        print(f"  {s:3} n={st['n']:6d}  PnL=${st['pnl']:+8.2f}  "
              f"EV/ct=${st['pnl']/st['n']:+.4f}  WR={100*st['wins']/st['n']:.1f}%")

    print("\n--- By price band (original direction) ---")
    for b in sorted(bands.keys()):
        st = bands[b]
        if st["n"] < 20: continue
        print(f"  {b:14} n={st['n']:6d}  PnL=${st['pnl']:+9.2f}  "
              f"EV/ct=${st['pnl']/st['n']:+.4f}  WR={100*st['wins']/st['n']:.1f}%")

    print("\n--- By price band (FLIPPED — buy other side) ---")
    for b in sorted(flip_bands.keys()):
        st = flip_bands[b]
        if st["n"] < 20: continue
        print(f"  {b:14} n={st['n']:6d}  PnL=${st['pnl']:+9.2f}  "
              f"EV/ct=${st['pnl']/st['n']:+.4f}  WR={100*st['wins']/st['n']:.1f}%")


if __name__ == "__main__":
    for obs in ("lognormal_fair_dev", "strike_ladder_literal"):
        run(obs)
