"""C1 Stablecoin flow → spot direction backtest.

Run this on 6/09 (14d after 5/26 collector deploy) to validate +EV signal
before building any observer/strategy.

Method:
  1. For each rolling window (1h, 4h), compute net stablecoin flow:
     net_mint_usd = sum(mint $) - sum(burn $) within window
  2. At each "signal event" (window-end timestamp), get current BTC + ETH spot
     from spot_observations.
  3. At T0 + horizon (6h, 12h, 24h), get realized spot.
  4. Compute return = (price_T1 - price_T0) / price_T0
  5. Group by magnitude band × asset × horizon, report mean return + sign
     consistency (does net_mint > $200M correlate with positive forward return?).

A signal is +EV if:
  - Mean return in correct-direction predictions > +0.3% (covers Kalshi fee)
  - Sign-consistency (direction matches) > 55%
  - n >= 30 events per (band × horizon)

If none of (1h, 4h) × (6h, 12h, 24h) × (BTC, ETH) cohorts meet threshold,
abandon C1 and focus elsewhere.

Output: tab-delimited CSV-style stats to stdout. Also writes JSON summary
to /tmp/stablecoin_flow_backtest_<datetime>.json for cron-friendly reading.
"""
import os
import json
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from collections import defaultdict

DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")

# Rolling windows: aggregate flow over these durations
FLOW_WINDOWS_H = [1, 4]
# Forward horizons: measure spot move T0 → T0+horizon
HORIZONS_H = [6, 12, 24]
# Magnitude bands ($M absolute, applies to net flow over the window)
MAG_BANDS = [(50, 100), (100, 250), (250, 500), (500, 10_000)]
# Minimum n per cohort to report stats
MIN_N = 5
# Minimum return threshold (signed) to consider "directional hit"
HIT_THRESHOLD = 0.003


def fetch_flow_events(c):
    """Yields (block_ts unix, direction, amount_usd) sorted by block_ts."""
    cur = c.execute("""
        SELECT block_ts, direction, amount_usd
        FROM stablecoin_mint_observations
        ORDER BY block_ts
    """)
    for row in cur:
        ts_str, direction, amount = row
        # ISO 8601 → unix
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        yield int(dt.timestamp()), direction, amount


SPOT_SYMBOL_MAP = {
    "BTC": ("coinbase", "BTC-USD"),
    "ETH": ("coinbase", "ETH-USD"),
}


def fetch_spot_series(c, asset: str):
    """Returns sorted list of (unix_ts, price) for asset (BTC/ETH).
    Uses coinbase last-price as ground truth.
    """
    if asset not in SPOT_SYMBOL_MAP:
        return []
    venue, symbol = SPOT_SYMBOL_MAP[asset]
    cur = c.execute("""
        SELECT fetched_at, last FROM spot_observations
        WHERE venue = ? AND symbol = ? AND last IS NOT NULL
        ORDER BY fetched_at
    """, (venue, symbol))
    rows = []
    for ts_str, last in cur:
        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            rows.append((int(dt.timestamp()), float(last)))
        except Exception:
            continue
    return rows


def lookup_spot(series, ts: int) -> float | None:
    """Linear scan for closest spot price at/before ts. Returns None if no data."""
    if not series:
        return None
    # binary search
    lo, hi = 0, len(series) - 1
    if ts < series[0][0]:
        return None
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if series[mid][0] <= ts:
            lo = mid
        else:
            hi = mid - 1
    return series[lo][1]


def compute_rolling_flows(events: list, window_h: int):
    """At each event timestamp, compute net flow (mint - burn) over preceding
    window_h hours. Yields (window_end_ts, net_flow_usd, n_events).
    """
    window_s = window_h * 3600
    # Use sliding window
    head = 0
    running_net = 0.0
    running_n = 0
    for tail in range(len(events)):
        # Add event at tail
        ts, direction, amount = events[tail]
        sign = 1.0 if direction == "mint" else -1.0
        running_net += sign * amount
        running_n += 1
        # Drop events outside window
        while head <= tail and events[head][0] < ts - window_s:
            h_ts, h_dir, h_amt = events[head]
            h_sign = 1.0 if h_dir == "mint" else -1.0
            running_net -= h_sign * h_amt
            running_n -= 1
            head += 1
        yield ts, running_net, running_n


def main():
    c = sqlite3.connect(DB)
    c.execute("PRAGMA query_only = 1")

    events = list(fetch_flow_events(c))
    if not events:
        print("No stablecoin events yet — collector hasn't run.")
        return

    print(f"Stablecoin events loaded: {len(events)}")
    first_ts = datetime.fromtimestamp(events[0][0], tz=timezone.utc).isoformat(timespec="seconds")
    last_ts = datetime.fromtimestamp(events[-1][0], tz=timezone.utc).isoformat(timespec="seconds")
    days_covered = (events[-1][0] - events[0][0]) / 86400.0
    print(f"Coverage: {first_ts} → {last_ts}  ({days_covered:.1f}d)")

    if days_covered < 7:
        print(f"\n⚠️  Less than 7d of data — abort (current: {days_covered:.1f}d)")
        return

    spot_data = {}
    for asset in ("BTC", "ETH"):
        s = fetch_spot_series(c, asset)
        spot_data[asset] = s
        if s:
            print(f"spot_{asset}: {len(s)} ticks")

    summary = []
    for win_h in FLOW_WINDOWS_H:
        flows = list(compute_rolling_flows(events, win_h))
        # Sample sparingly: take 1 reading per 30min to avoid sliding-window
        # overcounting from many events in short time
        sampled = []
        last_taken = -1
        for ts, net, n in flows:
            if ts - last_taken >= 1800:
                sampled.append((ts, net, n))
                last_taken = ts
        print(f"\n=== Flow window {win_h}h: {len(flows)} flow-events / {len(sampled)} sampled ===")

        for asset in ("BTC", "ETH"):
            series = spot_data[asset]
            if not series:
                print(f"  {asset}: no spot data")
                continue

            for hz_h in HORIZONS_H:
                hz_s = hz_h * 3600
                for (lo_M, hi_M) in MAG_BANDS:
                    lo, hi = lo_M * 1e6, hi_M * 1e6
                    # Bucket: positive net (mint-dominant) and negative (burn-dominant)
                    for side_label, side_filter in (
                        ("MINT_DOM", lambda nv: nv >= lo and nv < hi),
                        ("BURN_DOM", lambda nv: -nv >= lo and -nv < hi),
                    ):
                        sub = []
                        for ts, net, _ in sampled:
                            if not side_filter(net):
                                continue
                            p0 = lookup_spot(series, ts)
                            p1 = lookup_spot(series, ts + hz_s)
                            if p0 is None or p1 is None or p0 <= 0:
                                continue
                            ret = (p1 - p0) / p0
                            # MINT_DOM → bullish; BURN_DOM → bearish
                            # signed_ret aligns the prediction direction
                            signed_ret = ret if side_label == "MINT_DOM" else -ret
                            sub.append((ts, ret, signed_ret))
                        if len(sub) < MIN_N:
                            continue
                        rets = [r[2] for r in sub]
                        mean_ret = sum(rets) / len(rets)
                        hits = sum(1 for r in rets if r > HIT_THRESHOLD)
                        misses = sum(1 for r in rets if r < -HIT_THRESHOLD)
                        flat = len(rets) - hits - misses
                        hit_rate = hits / len(rets)
                        record = {
                            "flow_window_h": win_h, "asset": asset,
                            "horizon_h": hz_h,
                            "magnitude_band_M": f"{lo_M}-{hi_M}",
                            "side": side_label,
                            "n": len(rets),
                            "mean_signed_return_pct": round(100 * mean_ret, 3),
                            "hit_rate": round(hit_rate, 3),
                            "hits": hits, "misses": misses, "flat": flat,
                        }
                        summary.append(record)
                        print(f"  {asset} hz={hz_h:>2}h mag=${lo_M}-{hi_M}M {side_label:<8} "
                              f"n={len(rets):>3} signed_ret={100*mean_ret:+5.2f}% "
                              f"hits={hits}/{misses}/{flat} hit_rate={100*hit_rate:.0f}%")

    # Identify +EV candidates
    candidates = [s for s in summary
                  if s["mean_signed_return_pct"] > 0.3
                  and s["hit_rate"] >= 0.55
                  and s["n"] >= 30]
    print(f"\n=== +EV candidate cohorts (n≥30, signed_ret>+0.3%, hit_rate≥55%): {len(candidates)} ===")
    for s in candidates:
        print(f"  {s}")

    out_path = f"/tmp/stablecoin_flow_backtest_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.json"
    with open(out_path, "w") as f:
        json.dump({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "events_n": len(events),
            "days_covered": days_covered,
            "summary": summary,
            "candidates": candidates,
        }, f, indent=2)
    print(f"\nDetailed JSON: {out_path}")

    if not candidates:
        print("\n→ NO +EV candidates. Recommend deprecate C1, focus elsewhere.")
        sys.exit(0)
    else:
        print(f"\n→ {len(candidates)} +EV candidate(s) found. Next step: build observer.")
        sys.exit(0)


if __name__ == "__main__":
    main()
