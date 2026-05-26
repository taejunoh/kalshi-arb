"""A2 backtest — spot-Kalshi lag hypothesis (5/26 Phase A2).

Hypothesis: when spot moves sharply (>1% in 5min), Kalshi crypto markets
may lag in repricing, creating arb opportunity.

Approach: For each lognormal_fair_dev signal, compute spot 5-min delta from
spot_observations. Bucket by |delta|. If higher-magnitude buckets show
better EV, lag-hypothesis holds. If flat or worse, hypothesis fails.

Note: lognormal_fair_dev already captures spot via the lognormal model — this
test is whether spot velocity (raw) adds signal beyond what lognormal IV
captures.
"""
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta

DB = "/home/ubuntu/kalshi-arb/data/bot.db"
FEE = 0.07


def parse_ts(s):
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


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


def mag_bucket(delta_pct):
    a = abs(delta_pct)
    if a < 0.001: return "A_<0.1%"
    if a < 0.003: return "B_0.1-0.3%"
    if a < 0.006: return "C_0.3-0.6%"
    if a < 0.012: return "D_0.6-1.2%"
    if a < 0.025: return "E_1.2-2.5%"
    return "F_>=2.5%"


def main():
    c = sqlite3.connect(DB)
    c.execute("PRAGMA busy_timeout=10000")

    # Pre-load coinbase BTC + ETH spot timeseries
    btc_ts = []  # list of (datetime, price)
    eth_ts = []
    for venue, symbol in [("coinbase", "BTC-USD"), ("coinbase", "ETH-USD")]:
        cur = c.execute(
            "SELECT fetched_at, last FROM spot_observations "
            "WHERE venue=? AND symbol=? ORDER BY fetched_at",
            (venue, symbol),
        )
        target = btc_ts if symbol == "BTC-USD" else eth_ts
        for ts_str, last in cur:
            if last is not None:
                target.append((parse_ts(ts_str), last))

    print(f"BTC obs={len(btc_ts)} ETH obs={len(eth_ts)}")
    if btc_ts:
        print(f"BTC range: {btc_ts[0][0].isoformat()} to {btc_ts[-1][0].isoformat()}")

    def spot_at(ts, asset):
        """Linear interpolation. Returns None if outside range."""
        arr = btc_ts if asset == "BTC" else eth_ts
        if not arr: return None
        if ts < arr[0][0] or ts > arr[-1][0]:
            return None
        # Binary search
        lo, hi = 0, len(arr) - 1
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if arr[mid][0] <= ts:
                lo = mid
            else:
                hi = mid
        if arr[lo][0] == ts:
            return arr[lo][1]
        if hi == lo:
            return arr[lo][1]
        # Interpolate
        t0, p0 = arr[lo]
        t1, p1 = arr[hi]
        frac = (ts - t0).total_seconds() / max((t1 - t0).total_seconds(), 1)
        return p0 + (p1 - p0) * frac

    # Process lognormal_fair_dev signals (only kxBTC and kxETH where spot maps cleanly)
    cur = c.execute(
        """SELECT ts, predicted_outcome, actual_outcome, features_json, market_id
           FROM crypto_observer_signals
           WHERE observer = 'lognormal_fair_dev'
             AND actual_outcome IS NOT NULL
             AND ts > '2026-05-25T14:10:00'""",  # spot data starts here
    )

    by_mag = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})
    by_mag_pred = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})

    processed = 0
    skipped_no_spot = 0
    skipped_no_features = 0

    for ts_str, pred, actual, fj, mid in cur:
        try:
            f = json.loads(fj)
        except Exception:
            skipped_no_features += 1
            continue
        ya, na = f.get("ya"), f.get("na")
        if ya is None or na is None:
            skipped_no_features += 1
            continue

        # Determine asset from market_id
        if mid.startswith("KXBTC"):
            asset = "BTC"
        elif mid.startswith("KXETH"):
            asset = "ETH"
        else:
            continue  # KXXRP etc — no spot data

        sig_ts = parse_ts(ts_str)
        s_now = spot_at(sig_ts, asset)
        s_5m = spot_at(sig_ts - timedelta(minutes=5), asset)
        if s_now is None or s_5m is None:
            skipped_no_spot += 1
            continue

        delta_pct = (s_now - s_5m) / s_5m

        p = pnl_per_contract(pred, ya, na, actual)
        if p is None:
            continue

        bucket = mag_bucket(delta_pct)
        by_mag[bucket]["n"] += 1
        by_mag[bucket]["pnl"] += p
        if p > 0:
            by_mag[bucket]["wins"] += 1

        # Subtleer: split by direction-aligned vs opposite
        # If spot moved UP and signal predicts YES → "aligned"; else "opposite"
        if delta_pct > 0:
            spot_dir = "up"
        elif delta_pct < 0:
            spot_dir = "down"
        else:
            spot_dir = "flat"

        # Trade direction implied by pred:
        # If pred=yes, betting price goes/stays UP relative to strike
        # If pred=no, betting price stays/goes DOWN
        if pred == "yes":
            trade_dir = "up"
        else:
            trade_dir = "down"
        align = "ALIGN" if spot_dir == trade_dir else "OPPOSITE"
        key = f"{bucket}_{align}"
        by_mag_pred[key]["n"] += 1
        by_mag_pred[key]["pnl"] += p
        if p > 0:
            by_mag_pred[key]["wins"] += 1

        processed += 1

    c.close()

    print(f"\nProcessed: {processed}")
    print(f"Skipped no-spot: {skipped_no_spot}")
    print(f"Skipped no-features: {skipped_no_features}")

    print("\n=== EV by |spot 5min delta| bucket (any pred) ===")
    for k in sorted(by_mag.keys()):
        st = by_mag[k]
        if st["n"] < 10: continue
        print(f"  {k:14} n={st['n']:6d}  PnL=${st['pnl']:+9.2f}  "
              f"EV=${st['pnl']/st['n']:+.4f}  WR={100*st['wins']/st['n']:.1f}%")

    print("\n=== EV by bucket × alignment (does spot direction match pred?) ===")
    for k in sorted(by_mag_pred.keys()):
        st = by_mag_pred[k]
        if st["n"] < 20: continue
        print(f"  {k:22} n={st['n']:6d}  PnL=${st['pnl']:+9.2f}  "
              f"EV=${st['pnl']/st['n']:+.4f}  WR={100*st['wins']/st['n']:.1f}%")


if __name__ == "__main__":
    main()
