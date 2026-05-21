"""Data-driven sports strategy discovery — grid search over observer data.

Built 2026-05-21 to ignore the current strategy portfolio and ask: given
the 30 days of odds_observations data (6.4M rows, 3,978 settled tickers),
which (sport × side × price_band × mts_band × phase × playoff) cells show
fee-adjusted profit and pass robustness checks?

Methodology:
  1. Join odds_observations × ticker_settlements on kalshi_ticker.
  2. Stream observations sorted by (ticker, timestamp).
  3. For each ticker, identify the FIRST observation where each strategy
     cell's gates fire (single fire per ticker per cell — backtest realism).
  4. Compute PnL using Kalshi fee formula at $20 stake.
  5. Aggregate per cell.
  6. Filter: n >= 30, drop-top-3 ROI > 0, fee-adjusted ROI > +5%.
  7. Output: rank by Wilson-lower-bound-adjusted ROI × log(n).

Strategy cell = (sport, phase, side, price_band, mts_band, playoff_flag).
Total cells in grid: ~3,584. Post-filter typically <100 survive.

Read-only. Run on EC2 against /home/ubuntu/kalshi-arb/data/bot.db.
"""
import argparse
import math
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_PATH = "data/bot.db"

# Price bands (8): focus on underdog → mid → favorite zones
PRICE_BANDS = [
    (0.05, 0.10), (0.10, 0.15), (0.15, 0.20), (0.20, 0.25),
    (0.25, 0.30), (0.30, 0.40), (0.40, 0.50), (0.50, 0.65),
]

# Minutes-to-start bands (4 pre + 1 live = 5)
MTS_BANDS = [
    ("live", None, None),       # phase=live
    ("pre_0-30", 0, 30),
    ("pre_30-180", 30, 180),
    ("pre_180-720", 180, 720),
    ("pre_720+", 720, 99999),
]

STAKE_USDC = 20.0
KALSHI_FEE_PER_CONTRACT = 0.07  # fraction of contract value


def calculate_pnl(side_price: float, won: bool, stake: float = STAKE_USDC) -> float:
    """Kalshi fee formula. Fee = $0.07 × ceil(contracts × min(price, 1-price)).
    Approximate per-trade fee = fee_rate * stake_paid * min(price, 1-price)/price."""
    if side_price <= 0:
        return 0.0
    contracts = stake / side_price
    # Per Kalshi maker docs, fee per contract is 7c × ceiling(contracts × p × (1-p))
    # Simplify: total fee ≈ 0.07 × contracts × min(price, 1-price)
    fee = 0.07 * contracts * min(side_price, 1.0 - side_price)
    if won:
        payout = contracts * 1.0  # contract pays $1 on win
        return round(payout - stake - fee, 2)
    return round(-stake - fee, 2)


def price_band_idx(price: float) -> int:
    for i, (lo, hi) in enumerate(PRICE_BANDS):
        if lo <= price < hi:
            return i
    return -1


def mts_bucket(mts, phase) -> int:
    """Return MTS bucket index (-1 = no match)."""
    if phase == "live":
        return 0  # "live" bucket
    if mts is None or mts < 0:
        return -1
    for i, (label, lo, hi) in enumerate(MTS_BANDS):
        if label == "live":
            continue
        if lo <= mts < hi:
            return i
    return -1


def wilson_lower(wins: int, n: int, z: float = 1.96) -> float:
    """Wilson lower bound on win-rate at 95% CI."""
    if n == 0:
        return 0.0
    p = wins / n
    denom = 1 + z*z/n
    centre = p + z*z/(2*n)
    margin = z * math.sqrt((p*(1-p) + z*z/(4*n)) / n)
    return (centre - margin) / denom


def discover(min_n: int, since_iso: str, max_cells_output: int, loose: bool = False):
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row

    # Load all settled tickers + their result
    print(f"Loading settled tickers...", file=sys.stderr)
    settled = {}
    for r in c.execute(
        "SELECT kalshi_ticker, result FROM ticker_settlements "
        "WHERE result IS NOT NULL AND result IN ('yes','no')"
    ):
        settled[r["kalshi_ticker"]] = r["result"]
    print(f"  {len(settled)} settled tickers", file=sys.stderr)

    # Stream observations, sorted by ticker, then timestamp.
    # For each ticker + (side, price_band, mts_band) we want the FIRST
    # matching obs (single backtest fire per ticker per cell).
    print(f"Streaming observations since {since_iso}...", file=sys.stderr)

    # cell key: (sport, phase, side, price_band_idx, mts_band_idx, playoff_flag)
    # value: list of (price, won) tuples
    cells: dict[tuple, list] = defaultdict(list)

    # Per (ticker, cell_key_partial) — track if cell has fired for this ticker
    # to enforce "first fire only" per ticker
    cur = c.execute(
        "SELECT kalshi_ticker, timestamp, sport, phase, minutes_to_start, "
        "       kalshi_yes_ask, kalshi_no_ask, is_playoff "
        "FROM odds_observations "
        "WHERE timestamp >= ? "
        "  AND sport IS NOT NULL "
        "ORDER BY kalshi_ticker, timestamp",
        (since_iso,),
    )

    current_ticker = None
    fired_in_ticker: set = set()
    rows_scanned = 0
    obs_used = 0

    for r in cur:
        rows_scanned += 1
        ticker = r["kalshi_ticker"]
        if ticker != current_ticker:
            current_ticker = ticker
            fired_in_ticker = set()
            if ticker not in settled:
                # Skip unsettled tickers — can't compute PnL
                continue
        if ticker not in settled:
            continue
        result = settled[ticker]
        sport = r["sport"]
        phase = r["phase"]
        mts = r["minutes_to_start"]
        playoff = r["is_playoff"]  # 0, 1, or None
        playoff_flag = "playoff" if playoff == 1 else ("reg" if playoff == 0 else "unk")

        mts_idx = mts_bucket(mts, phase)
        if mts_idx < 0:
            continue

        # Try both sides
        for side, price_col in (("yes", r["kalshi_yes_ask"]), ("no", r["kalshi_no_ask"])):
            if price_col is None or price_col <= 0 or price_col >= 1:
                continue
            pb_idx = price_band_idx(price_col)
            if pb_idx < 0:
                continue
            cell_partial = (side, pb_idx, mts_idx)
            if cell_partial in fired_in_ticker:
                continue  # already fired this cell on this ticker (first-fire rule)
            fired_in_ticker.add(cell_partial)
            won = (result == side)
            cell_key = (sport, phase, side, pb_idx, mts_idx, playoff_flag)
            cells[cell_key].append((price_col, won, ticker))
            obs_used += 1

    print(f"  scanned {rows_scanned} rows, fired {obs_used} hypothetical entries "
          f"across {len(cells)} cells", file=sys.stderr)

    # Aggregate per cell
    results = []
    for key, fires in cells.items():
        n = len(fires)
        if n < min_n:
            continue
        sport, phase, side, pb_idx, mts_idx, playoff_flag = key
        wins = sum(1 for f in fires if f[1])
        pnls = [calculate_pnl(f[0], f[1]) for f in fires]
        total_pnl = sum(pnls)
        # Drop-top-3 robustness
        pnls_sorted = sorted(pnls, reverse=True)
        dropped3 = sum(pnls_sorted[3:]) if n > 3 else 0.0
        # ROI vs total stake
        total_stake = n * STAKE_USDC
        roi = total_pnl / total_stake * 100 if total_stake else 0
        roi_d3 = dropped3 / max(total_stake - 3*STAKE_USDC, 1) * 100
        wr = wins / n * 100
        mean_price = sum(f[0] for f in fires) / n
        # Breakeven WR @ mean price (no fee): wr = price
        be_wr_no_fee = mean_price * 100
        # Fee-adjusted BE-WR: more conservative
        wlb = wilson_lower(wins, n) * 100  # Wilson lower-bound WR %
        edge_pp = wr - be_wr_no_fee  # WR margin above naive BE
        # Composite score: roi × log(n) penalized by drop-top-3 (must stay positive)
        if roi_d3 <= 0:
            score = -1e9
        else:
            score = roi * math.log(n) * (1.0 if roi_d3 > 0 else 0.0)
        pb_lo, pb_hi = PRICE_BANDS[pb_idx]
        mts_label = MTS_BANDS[mts_idx][0]
        results.append({
            "sport": sport, "phase": phase, "side": side,
            "price_band": f"{pb_lo:.2f}-{pb_hi:.2f}",
            "mts_band": mts_label, "playoff": playoff_flag,
            "n": n, "wins": wins, "wr": wr,
            "mean_price": mean_price, "edge_pp": edge_pp,
            "wlb": wlb, "roi": roi, "roi_d3": roi_d3,
            "pnl": total_pnl, "stake": total_stake, "score": score,
        })

    # Optionally filter; otherwise show all
    if loose:
        survivors = list(results)
        survivors.sort(key=lambda x: x["roi"], reverse=True)
        print(f"  {len(results)} cells with n >= {min_n} (loose mode — no ROI filter)",
              file=sys.stderr)
    else:
        survivors = [r for r in results if r["roi_d3"] > 0 and r["roi"] > 5]
        print(f"  {len(results)} cells with n >= {min_n}; "
              f"{len(survivors)} pass filter (roi_d3>0, roi>5%)", file=sys.stderr)
        survivors.sort(key=lambda x: x["score"], reverse=True)

    # Print top cells
    print()
    print(f"=== Top {min(max_cells_output, len(survivors))} Strategy Candidates ===")
    print(f"{'rank':>4} {'sport':5} {'phase':4} {'side':4} {'price':9} {'mts':12} {'po':6} "
          f"{'n':>5} {'WR':>5} {'BE':>5} {'edge':>5} {'WLB':>5} "
          f"{'ROI':>6} {'ROI_d3':>7} {'pnl@$20':>9}")
    print("-" * 135)
    for i, r in enumerate(survivors[:max_cells_output], 1):
        print(f"{i:>4} {r['sport']:5} {r['phase']:4} {r['side']:4} "
              f"{r['price_band']:9} {r['mts_band']:12} {r['playoff']:6} "
              f"{r['n']:>5} {r['wr']:>4.0f}% {r['mean_price']*100:>4.0f}% "
              f"{r['edge_pp']:>+4.1f} {r['wlb']:>4.0f}% "
              f"{r['roi']:>+5.1f}% {r['roi_d3']:>+6.1f}% {r['pnl']:>+9.2f}")

    print()
    print(f"Total cells tested: {len(results) + 0}")
    print(f"Cells passing filter: {len(survivors)}")
    print(f"\nColumns:")
    print(f"  WR     observed win-rate")
    print(f"  BE     mean signal price (≈ breakeven WR with no fees)")
    print(f"  edge   WR - BE (positive = beating fair value)")
    print(f"  WLB    Wilson-lower-bound win-rate at 95% CI")
    print(f"  ROI    pooled fee-net ROI on $20 stake")
    print(f"  ROI_d3 drop-top-3 ROI (robustness: removes 3 best fires)")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--min-n", type=int, default=30,
                   help="Minimum cell sample size (default 30).")
    p.add_argument("--since", type=str, default="2026-04-21",
                   help="ISO start date (default 2026-04-21 — full obs window).")
    p.add_argument("--max-output", type=int, default=50,
                   help="Max cells to print (default 50).")
    p.add_argument("--loose", action="store_true",
                   help="No ROI filter — print all cells with n >= min_n, "
                        "sorted by ROI desc. Use for exploring universe.")
    args = p.parse_args()
    discover(args.min_n, args.since, args.max_output, args.loose)


if __name__ == "__main__":
    main()
