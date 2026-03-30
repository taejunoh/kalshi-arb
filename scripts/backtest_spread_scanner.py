#!/usr/bin/env python3
"""Backtest the crypto spread scanner strategy using Binance historical data.

Simulates "above/below" markets every hour for the past N days.
For each hour: creates synthetic markets at price thresholds,
estimates probability with our sigmoid, and checks if price
actually ended above/below at the close.

Usage:
    python scripts/backtest_spread_scanner.py [--days 30] [--symbol BTCUSDT]
"""
import argparse
import math
import sys
from datetime import datetime, timezone, timedelta
from collections import defaultdict

import httpx


def fetch_klines(symbol: str, interval: str, days: int) -> list[dict]:
    """Fetch historical klines from Binance."""
    client = httpx.Client(timeout=30)
    all_candles = []
    end_time = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_time = end_time - (days * 24 * 3600 * 1000)

    while start_time < end_time:
        resp = client.get("https://api.binance.us/api/v3/klines", params={
            "symbol": symbol, "interval": interval,
            "startTime": start_time, "limit": 1000,
        })
        data = resp.json()
        if not data:
            break
        for c in data:
            all_candles.append({
                "open_time": c[0],
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "close_time": c[6],
            })
        start_time = data[-1][6] + 1  # next candle after last close_time

    return all_candles


def estimate_probability(current_price: float, threshold: float) -> float:
    """Our sigmoid probability estimation (matches spread_scanner.py)."""
    distance_pct = (current_price - threshold) / threshold
    prob = 1.0 / (1.0 + math.exp(-distance_pct * 200))
    return max(0.03, min(0.97, prob))


def simulate_market(entry_price: float, threshold: float, settle_price: float,
                    min_edge: float = 0.05) -> dict | None:
    """Simulate one above/below market trade.

    Returns trade result or None if no edge found.
    """
    fair_prob = estimate_probability(entry_price, threshold)

    # Skip extreme probabilities (< 15c or > 85c) — same filter as live strategy
    if fair_prob < 0.15 or fair_prob > 0.85:
        return None

    # Simulate realistic Kalshi market: price lags fair value by a small amount
    # In reality, Kalshi markets are ~5-10c behind spot-implied fair value
    import random
    lag = random.uniform(0.03, 0.08)  # 3-8c mispricing
    direction = random.choice([-1, 1])
    market_yes = max(0.05, min(0.95, fair_prob + direction * lag))
    market_no = max(0.05, min(0.95, (1 - fair_prob) - direction * lag))

    # Check for edge
    yes_edge = fair_prob - market_yes
    no_edge = (1 - fair_prob) - market_no

    side = None
    edge = 0
    entry_cost = 0

    if yes_edge > min_edge and yes_edge > no_edge:
        side = "yes"
        edge = yes_edge
        entry_cost = market_yes
    elif no_edge > min_edge:
        side = "no"
        edge = no_edge
        entry_cost = market_no
    else:
        return None

    # Determine outcome
    actual_above = settle_price >= threshold
    won = (side == "yes" and actual_above) or (side == "no" and not actual_above)

    # PnL calculation (per $1 invested)
    if won:
        pnl = (1.0 - entry_cost) / entry_cost - 0.07  # 7c fee per contract
    else:
        pnl = -1.0

    return {
        "side": side,
        "edge": edge,
        "fair_prob": fair_prob,
        "entry_cost": entry_cost,
        "threshold": threshold,
        "entry_price": entry_price,
        "settle_price": settle_price,
        "won": won,
        "pnl": pnl,
    }


def run_backtest(symbol: str, days: int, interval_hours: int = 1):
    """Run the full backtest."""
    print(f"Fetching {days} days of {symbol} data...")
    candles = fetch_klines(symbol, "1h", days)
    print(f"Got {len(candles)} hourly candles")

    if len(candles) < 2:
        print("Not enough data")
        return

    # Simulate: at each candle open, create markets at various thresholds
    # Then check at candle close if price ended above/below
    trades = []
    settle_window = interval_hours  # hours until market settles

    for i in range(len(candles) - settle_window):
        entry_candle = candles[i]
        settle_candle = candles[i + settle_window]
        entry_price = entry_candle["open"]
        settle_price = settle_candle["close"]

        # Create synthetic thresholds around current price
        # (like Kalshi's above/below markets at various strike prices)
        for pct_offset in [-5, -3, -2, -1, -0.5, 0.5, 1, 2, 3, 5]:
            threshold = entry_price * (1 + pct_offset / 100)
            result = simulate_market(entry_price, threshold, settle_price)
            if result:
                result["entry_time"] = datetime.fromtimestamp(
                    entry_candle["open_time"] / 1000, tz=timezone.utc
                )
                trades.append(result)

    # Analyze results
    if not trades:
        print("No trades generated (min_edge too high?)")
        return

    wins = sum(1 for t in trades if t["won"])
    losses = len(trades) - wins
    total_pnl = sum(t["pnl"] for t in trades)
    avg_pnl = total_pnl / len(trades)
    win_rate = wins / len(trades) * 100

    print(f"\n{'='*60}")
    print(f"BACKTEST RESULTS: {symbol} | {days} days | {interval_hours}h settle")
    print(f"{'='*60}")
    print(f"Total trades:    {len(trades)}")
    print(f"Wins:            {wins} ({win_rate:.1f}%)")
    print(f"Losses:          {losses}")
    print(f"Total PnL:       {total_pnl:+.2f}x (per $1 per trade)")
    print(f"Avg PnL/trade:   {avg_pnl:+.4f}x")
    print(f"")

    # Breakdown by edge size
    print(f"BY EDGE SIZE:")
    for edge_min, edge_max, label in [(0.10, 0.20, "10-20%"), (0.20, 0.40, "20-40%"), (0.40, 1.0, "40%+")]:
        bucket = [t for t in trades if edge_min <= t["edge"] < edge_max]
        if bucket:
            bw = sum(1 for t in bucket if t["won"])
            bp = sum(t["pnl"] for t in bucket)
            print(f"  {label:8s}  {len(bucket):4d} trades  {bw}/{len(bucket)} wins ({bw/len(bucket)*100:.0f}%)  PnL: {bp:+.2f}x")

    # Breakdown by direction
    print(f"\nBY DIRECTION:")
    for side in ["yes", "no"]:
        bucket = [t for t in trades if t["side"] == side]
        if bucket:
            bw = sum(1 for t in bucket if t["won"])
            bp = sum(t["pnl"] for t in bucket)
            print(f"  {side.upper():4s}  {len(bucket):4d} trades  {bw}/{len(bucket)} wins ({bw/len(bucket)*100:.0f}%)  PnL: {bp:+.2f}x")

    # Breakdown by threshold distance
    print(f"\nBY THRESHOLD DISTANCE:")
    for dist_label, dist_range in [("< 1%", (0, 0.01)), ("1-3%", (0.01, 0.03)), ("3-5%", (0.03, 0.05))]:
        bucket = [t for t in trades if dist_range[0] <= abs(t["entry_price"] - t["threshold"]) / t["entry_price"] < dist_range[1]]
        if bucket:
            bw = sum(1 for t in bucket if t["won"])
            bp = sum(t["pnl"] for t in bucket)
            print(f"  {dist_label:8s}  {len(bucket):4d} trades  {bw}/{len(bucket)} wins ({bw/len(bucket)*100:.0f}%)  PnL: {bp:+.2f}x")

    # Monthly breakdown
    print(f"\nBY WEEK:")
    by_week = defaultdict(list)
    for t in trades:
        week = t["entry_time"].strftime("%Y-W%U")
        by_week[week].append(t)
    for week in sorted(by_week.keys()):
        wt = by_week[week]
        ww = sum(1 for t in wt if t["won"])
        wp = sum(t["pnl"] for t in wt)
        print(f"  {week}  {len(wt):4d} trades  {ww}/{len(wt)} wins ({ww/len(wt)*100:.0f}%)  PnL: {wp:+.2f}x")

    # Show worst and best trades
    trades_sorted = sorted(trades, key=lambda t: t["pnl"])
    print(f"\nWORST 5 TRADES:")
    for t in trades_sorted[:5]:
        print(f"  {t['entry_time'].strftime('%m/%d %H:00')}  {t['side']:3s}  entry=${t['entry_price']:.0f}  thresh=${t['threshold']:.0f}  settle=${t['settle_price']:.0f}  edge={t['edge']:.2f}  pnl={t['pnl']:+.2f}x")

    print(f"\nBEST 5 TRADES:")
    for t in trades_sorted[-5:]:
        print(f"  {t['entry_time'].strftime('%m/%d %H:00')}  {t['side']:3s}  entry=${t['entry_price']:.0f}  thresh=${t['threshold']:.0f}  settle=${t['settle_price']:.0f}  edge={t['edge']:.2f}  pnl={t['pnl']:+.2f}x")

    # Simulate $1000 bankroll
    print(f"\n{'='*60}")
    print(f"$1,000 BANKROLL SIMULATION (5% per trade)")
    bankroll = 1000.0
    peak = 1000.0
    max_dd = 0.0
    for t in trades:
        size = bankroll * 0.05
        if bankroll < 10:
            break
        bankroll += size * t["pnl"]
        peak = max(peak, bankroll)
        dd = (peak - bankroll) / peak
        max_dd = max(max_dd, dd)

    print(f"Final bankroll:  ${bankroll:.2f}")
    print(f"Return:          {(bankroll/1000-1)*100:+.1f}%")
    print(f"Max drawdown:    {max_dd*100:.1f}%")
    print(f"{'='*60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--settle-hours", type=int, default=1)
    args = parser.parse_args()
    run_backtest(args.symbol, args.days, args.settle_hours)
