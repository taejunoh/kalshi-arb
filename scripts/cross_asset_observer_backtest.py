"""A1 backtest — cross-asset lead-lag observers (5/26 Phase A1).

Targets:
- btc_eth_lead_lag — predicts direction based on lead-asset move
- cross_asset_corr_break — breaks in BTC-ETH correlation

Each settled signal: predicted_outcome + actual_outcome + features.
Compute per-contract PnL after $0.07/ct fee.
Aggregate by predicted_direction, magnitude buckets, correlation buckets, price band.
"""
import json
import sqlite3
from collections import defaultdict

DB = "/home/ubuntu/kalshi-arb/data/bot.db"
FEE = 0.07


def pnl_per_contract(pred, ya, na, actual):
    if pred == "yes":
        price = ya
        win = actual == "yes"
    else:
        price = na
        win = actual == "no"
    if price is None or price <= 0 or price >= 1:
        return None
    return (1.0 - price - FEE) if win else (-price - FEE)


def band(price):
    if price is None: return "X"
    if price < 0.10: return "A_<0.10"
    if price < 0.25: return "B_0.10-0.25"
    if price < 0.45: return "C_0.25-0.45"
    if price < 0.60: return "D_0.45-0.60"
    if price < 0.75: return "E_0.60-0.75"
    if price < 0.90: return "F_0.75-0.90"
    return "G_>=0.90"


def mag(delta):
    if delta is None: return "X"
    abs_d = abs(delta)
    if abs_d < 0.001: return "A_<0.1%"
    if abs_d < 0.002: return "B_0.1-0.2%"
    if abs_d < 0.003: return "C_0.2-0.3%"
    if abs_d < 0.005: return "D_0.3-0.5%"
    return "E_>=0.5%"


def corr_band(c):
    if c is None: return "X"
    if c < 0.3: return "A_<0.3"
    if c < 0.5: return "B_0.3-0.5"
    if c < 0.7: return "C_0.5-0.7"
    if c < 0.85: return "D_0.7-0.85"
    return "E_>=0.85"


def show(label, d, min_n=10):
    print(f"\n--- {label} (min n={min_n}) ---")
    rows = []
    for k in sorted(d.keys()):
        st = d[k]
        if st["n"] < min_n:
            continue
        ev = st["pnl"] / st["n"]
        wr = 100 * st["wins"] / st["n"]
        rows.append((k, st["n"], st["pnl"], ev, wr))
    for k, n, pnl, ev, wr in rows:
        print(f"  {k:20} n={n:5d}  PnL=${pnl:+8.2f}  EV=${ev:+.4f}  WR={wr:.1f}%")


def run(observer):
    c = sqlite3.connect(DB)
    c.execute("PRAGMA busy_timeout=5000")
    cur = c.execute(
        """SELECT predicted_outcome, actual_outcome, features_json, market_id
           FROM crypto_observer_signals
           WHERE observer = ?
             AND actual_outcome IS NOT NULL""",
        (observer,),
    )

    pooled = {"n": 0, "pnl": 0.0, "wins": 0}
    pooled_flip = {"n": 0, "pnl": 0.0, "wins": 0}

    by_band = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})
    by_band_flip = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})

    by_dir = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})
    by_mag_eth_30s = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})
    by_mag_btc_30s = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})
    by_corr_5m = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})
    by_corr_15m = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})
    by_lead = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})
    by_series = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})

    rejected = 0

    for pred, actual, fj, mid in cur:
        try:
            f = json.loads(fj)
        except Exception:
            rejected += 1
            continue
        ya, na = f.get("ya"), f.get("na")
        p = pnl_per_contract(pred, ya, na, actual)
        if p is None:
            rejected += 1
            continue

        # Pooled original
        pooled["n"] += 1
        pooled["pnl"] += p
        if p > 0:
            pooled["wins"] += 1

        # Pooled flipped
        flip_pred = "no" if pred == "yes" else "yes"
        fp = pnl_per_contract(flip_pred, ya, na, actual)
        if fp is not None:
            pooled_flip["n"] += 1
            pooled_flip["pnl"] += fp
            if fp > 0:
                pooled_flip["wins"] += 1

            flip_price = ya if flip_pred == "yes" else na
            fb = band(flip_price)
            by_band_flip[fb]["n"] += 1
            by_band_flip[fb]["pnl"] += fp
            if fp > 0:
                by_band_flip[fb]["wins"] += 1

        # Original side band
        price = ya if pred == "yes" else na
        b = band(price)
        by_band[b]["n"] += 1
        by_band[b]["pnl"] += p
        if p > 0:
            by_band[b]["wins"] += 1

        # Direction (predicted_direction in features)
        pdir = f.get("predicted_direction", "?")
        by_dir[pdir]["n"] += 1
        by_dir[pdir]["pnl"] += p
        if p > 0:
            by_dir[pdir]["wins"] += 1

        # Magnitudes
        mb = mag(f.get("delta_btc_30s_pct"))
        by_mag_btc_30s[mb]["n"] += 1
        by_mag_btc_30s[mb]["pnl"] += p
        if p > 0:
            by_mag_btc_30s[mb]["wins"] += 1

        me = mag(f.get("delta_eth_30s_pct"))
        by_mag_eth_30s[me]["n"] += 1
        by_mag_eth_30s[me]["pnl"] += p
        if p > 0:
            by_mag_eth_30s[me]["wins"] += 1

        # Correlations
        c5 = corr_band(f.get("corr_5min"))
        by_corr_5m[c5]["n"] += 1
        by_corr_5m[c5]["pnl"] += p
        if p > 0:
            by_corr_5m[c5]["wins"] += 1

        c15 = corr_band(f.get("corr_15min"))
        by_corr_15m[c15]["n"] += 1
        by_corr_15m[c15]["pnl"] += p
        if p > 0:
            by_corr_15m[c15]["wins"] += 1

        # Lead asset (for btc_eth_lead_lag)
        lead = f.get("lead_asset", "?")
        by_lead[lead]["n"] += 1
        by_lead[lead]["pnl"] += p
        if p > 0:
            by_lead[lead]["wins"] += 1

        # Series prefix
        series = mid.split("-")[0]
        by_series[series]["n"] += 1
        by_series[series]["pnl"] += p
        if p > 0:
            by_series[series]["wins"] += 1

    c.close()

    print(f"\n{'='*60}")
    print(f"  {observer}")
    print(f"{'='*60}")
    print(f"Rejected (bad features): {rejected}")

    n = pooled["n"]
    if n == 0:
        print("NO DATA")
        return
    print(f"\nPOOLED (original): n={n} PnL=${pooled['pnl']:+.2f} "
          f"EV=${pooled['pnl']/n:+.4f} WR={100*pooled['wins']/n:.1f}%")

    nf = pooled_flip["n"]
    if nf:
        print(f"POOLED (flipped):  n={nf} PnL=${pooled_flip['pnl']:+.2f} "
              f"EV=${pooled_flip['pnl']/nf:+.4f} WR={100*pooled_flip['wins']/nf:.1f}%")

    show("By price band (ORIGINAL)", by_band)
    show("By price band (FLIPPED)", by_band_flip)
    show("By predicted_direction (ORIGINAL)", by_dir, min_n=5)
    show("By BTC 30s magnitude (ORIGINAL)", by_mag_btc_30s, min_n=5)
    show("By ETH 30s magnitude (ORIGINAL)", by_mag_eth_30s, min_n=5)
    show("By corr_5min (ORIGINAL)", by_corr_5m, min_n=5)
    show("By corr_15min (ORIGINAL)", by_corr_15m, min_n=5)
    show("By lead_asset (ORIGINAL)", by_lead, min_n=5)
    show("By series (ORIGINAL)", by_series, min_n=5)


if __name__ == "__main__":
    for obs in ("btc_eth_lead_lag", "cross_asset_corr_break"):
        run(obs)
