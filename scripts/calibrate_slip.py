"""Empirical slip calibration — measure actual LIVE fill vs signal price.

Replaces the hardcoded 0.02 slip guess in core/execution_realism.py with
data-driven values. Reads LIVE trades from bot.db where both signal price
and realized_fill_price are populated, computes slip distribution per
price band, and writes calibration constants to data/slip_calibration.json.

The calibration is consumed by execution_realism.slip_adjusted_pnl() to
apply realistic slip in backtests — replacing the prior guess.

Usage:
  venv/bin/python scripts/calibrate_slip.py
  venv/bin/python scripts/calibrate_slip.py --days 90 --out custom_path.json

Per-band stats output:
  - n:        sample size in band
  - median:   median slip (50th percentile)
  - p75:      75th percentile slip (used as the calibrated value — conservative)
  - p95:      95th percentile (tail risk)
  - mean:     simple mean (verify median for asymmetry)
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


DB_DEFAULT = "/home/ubuntu/kalshi-arb/data/bot.db"
OUT_DEFAULT = "data/slip_calibration.json"

# Price bands aligned with execution_realism.is_wipe_zone() boundaries
PRICE_BANDS = [
    ("A_lt15", 0.0, 0.15),     # wipe zone low (NO underdog)
    ("B_15_30", 0.15, 0.30),
    ("C_30_45", 0.30, 0.45),
    ("D_45_55", 0.45, 0.55),
    ("E_55_70", 0.55, 0.70),
    ("F_70_85", 0.70, 0.85),
    ("G_85_plus", 0.85, 1.0),  # wipe zone high
]


def pull_slip_data(conn: sqlite3.Connection, days_back: int) -> list:
    """Return list of dicts: {signal_price, fill_price, slip, direction, strategy}."""
    rows = conn.execute(
        """SELECT price, realized_fill_price, direction, strategy
           FROM trades
           WHERE status IN ('filled', 'settled')
             AND price IS NOT NULL
             AND realized_fill_price IS NOT NULL
             AND timestamp >= strftime('%Y-%m-%dT%H:%M:%S', 'now', ?)""",
        (f"-{int(days_back)} days",),
    ).fetchall()
    out = []
    for price, fill, direction, strategy in rows:
        # For BUY YES at price P: slip = fill - P (positive = worse fill)
        # For BUY NO at price P (where price is no_ask): same direction
        slip = fill - price
        out.append({
            "signal_price": price, "fill_price": fill,
            "slip": slip, "direction": direction, "strategy": strategy,
        })
    return out


def band_of(price: float) -> str:
    for name, lo, hi in PRICE_BANDS:
        if lo <= price < hi:
            return name
    return PRICE_BANDS[-1][0]


def percentile(values: list, p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = max(0, min(len(s) - 1, int(p / 100 * len(s))))
    return s[idx]


def calibrate(data: list, exclude_outliers: float = 0.5) -> dict:
    """Compute slip stats per price band.

    `exclude_outliers`: drop |slip| > this (default 50pp — likely stop-loss
    exits or stale-limit fills, not normal slip). Reports both raw and
    outlier-excluded stats.
    """
    by_band: dict = {b[0]: [] for b in PRICE_BANDS}
    raw_count = 0
    for row in data:
        if abs(row["slip"]) > exclude_outliers:
            continue
        b = band_of(row["signal_price"])
        by_band[b].append(row["slip"])
        raw_count += 1

    result = {}
    for band, slips in by_band.items():
        if not slips:
            result[band] = {"n": 0, "skipped": True}
            continue
        result[band] = {
            "n": len(slips),
            "median": round(statistics.median(slips), 4),
            "p75_abs": round(percentile([abs(s) for s in slips], 75), 4),
            "p95_abs": round(percentile([abs(s) for s in slips], 95), 4),
            "mean": round(statistics.mean(slips), 4),
            "stdev": round(statistics.stdev(slips), 4) if len(slips) > 1 else 0.0,
        }
    return {
        "total_samples": raw_count,
        "outliers_excluded": len(data) - raw_count,
        "outlier_threshold_abs": exclude_outliers,
        "bands": result,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=DB_DEFAULT)
    parser.add_argument("--days", type=int, default=90,
                        help="lookback window (default 90d)")
    parser.add_argument("--out", default=OUT_DEFAULT,
                        help="output JSON file (relative to project root)")
    parser.add_argument("--outlier-threshold", type=float, default=0.5,
                        help="exclude |slip| > this (default 0.5 = 50pp)")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"DB not found: {args.db}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA query_only = 1")
    data = pull_slip_data(conn, args.days)
    if not data:
        print("No LIVE trades with fill data — calibration not possible.")
        sys.exit(0)

    calibration = calibrate(data, exclude_outliers=args.outlier_threshold)
    calibration["calibrated_at"] = datetime.now(timezone.utc).isoformat()
    calibration["lookback_days"] = args.days

    print(f"\n=== Slip Calibration ({args.days}d window) ===\n")
    print(f"Total samples: {len(data)}")
    print(f"Outliers excluded (|slip|>{args.outlier_threshold}): "
          f"{calibration['outliers_excluded']}")
    print()
    print(f"{'Band':<14}{'n':>6}{'median':>10}{'p75|slip|':>12}{'p95|slip|':>12}{'mean':>10}")
    print("-" * 64)
    for band in [b[0] for b in PRICE_BANDS]:
        s = calibration["bands"][band]
        if s.get("skipped"):
            print(f"{band:<14}{'0':>6}  (no data)")
            continue
        print(f"{band:<14}{s['n']:>6}{s['median']:>+10.4f}"
              f"{s['p75_abs']:>+12.4f}{s['p95_abs']:>+12.4f}{s['mean']:>+10.4f}")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(calibration, f, indent=2)
    print(f"\nWrote calibration → {args.out}")


if __name__ == "__main__":
    main()
