"""Secondary slicing of top sports strategy candidates (Step A follow-up).

After sports_strategy_discovery.py surfaced ~10 cells with positive
drop-top-3 ROI, this script re-slices each candidate by additional
features to identify which sub-cohort carries the alpha:

  depth_band:    yes_ask_size or no_ask_size (4 buckets)
  consensus_gap: |kalshi_ask - eu_consensus| (3 buckets)
  day_of_week:   0=Mon ... 6=Sun
  has_pinnacle:  bool

For each candidate × sub-cell with n >= 5, prints WR / ROI / d3 ROI.

Read-only. Run on EC2 against /home/ubuntu/kalshi-arb/data/bot.db.
"""
import math
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_PATH = "data/bot.db"
STAKE_USDC = 20.0
KALSHI_FEE_PER_CONTRACT = 0.07


# Top candidates from sports_strategy_discovery.py loose run, filtered
# to those with positive drop-top-3 ROI in primary grid.
# Tuple: (sport, phase, side, p_lo, p_hi, mts_lo, mts_hi, playoff_filter)
# playoff_filter: 'reg' / 'playoff' / 'any'
CANDIDATES = [
    ("MLS",  "pre",  "yes", 0.20, 0.25, 180, 720, "any"),    # #1
    ("EPL",  "pre",  "yes", 0.25, 0.30, 720, 99999, "any"),  # #2
    ("WNBA", "live", "yes", 0.20, 0.25, None, None, "reg"),  # #3
    ("WNBA", "live", "no",  0.20, 0.25, None, None, "reg"),  # #4
    ("MLS",  "pre",  "yes", 0.20, 0.25, 30,  180, "any"),    # #5
    ("WNBA", "live", "no",  0.25, 0.30, None, None, "reg"),  # #6
    ("WNBA", "live", "yes", 0.25, 0.30, None, None, "reg"),  # #7
    ("MLS",  "live", "no",  0.40, 0.50, None, None, "any"),  # #8
    ("MLS",  "pre",  "no",  0.40, 0.50, 720, 99999, "any"),  # #21 (NEW)
    ("NBA",  "pre",  "no",  0.40, 0.50, 0,   30,  "playoff"),# #23 (NEW)
    ("NBA",  "pre",  "no",  0.40, 0.50, 180, 720, "playoff"),# #32 (borderline)
]


def calculate_pnl(price: float, won: bool, stake: float = STAKE_USDC) -> float:
    if price <= 0: return 0.0
    contracts = stake / price
    fee = 0.07 * contracts * min(price, 1.0 - price)
    if won: return round(contracts - stake - fee, 2)
    return round(-stake - fee, 2)


def depth_bucket(d):
    if d is None or d <= 0: return "?"
    if d < 50: return "<50"
    if d < 500: return "50-500"
    if d < 2000: return "500-2k"
    return "2k+"


def gap_bucket(gap_pp):
    """Gap = (kalshi_ask - consensus) in percentage points, positive = Kalshi
    higher than consensus (i.e. Kalshi 'thinks' more likely than EU books).
    For underdog buys YES at 25c when EU consensus says 20c, gap = +5pp
    (Kalshi worse-priced from buyer view → no edge). Negative gap = Kalshi
    cheaper than consensus = edge for buyer. So track ABS magnitude AND sign."""
    if gap_pp is None: return "?"
    if abs(gap_pp) < 3: return "<3pp"
    if abs(gap_pp) < 6: return "3-6pp"
    if gap_pp < 0: return "kalshi_lt6+"  # Kalshi cheaper → buyer edge
    return "kalshi_gt6+"  # Kalshi richer → no edge / contrarian


def dow_label(ts_iso):
    try:
        d = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
        return ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][d.weekday()]
    except Exception:
        return "?"


def run_candidate(c, conn, settled):
    sport, phase, side, p_lo, p_hi, m_lo, m_hi, po_filter = c
    sql = (
        "SELECT kalshi_ticker, timestamp, minutes_to_start, is_playoff, "
        "       kalshi_yes_ask, kalshi_no_ask, "
        "       kalshi_yes_ask_size, kalshi_no_ask_size, "
        "       eu_yes_prob, pinnacle_yes_prob, has_pinnacle "
        "FROM odds_observations "
        "WHERE sport = ? AND phase = ? "
        "  AND timestamp >= '2026-04-21' "
        "ORDER BY kalshi_ticker, timestamp"
    )
    cur = conn.execute(sql, (sport, phase))

    fires_total = []
    fires_by_subcell = defaultdict(list)  # (depth_b, gap_b, dow) -> [(price, won)]

    current_ticker = None
    fired_for_ticker = False
    for r in cur:
        tk = r["kalshi_ticker"]
        if tk != current_ticker:
            current_ticker = tk
            fired_for_ticker = False
            if tk not in settled:
                continue
        if fired_for_ticker or tk not in settled:
            continue
        # Phase + mts filter
        mts = r["minutes_to_start"]
        if phase != "live":
            if mts is None or mts < 0:
                continue
            if m_lo is not None and not (m_lo <= mts < m_hi):
                continue
        # Side price filter
        if side == "yes":
            price = r["kalshi_yes_ask"]
        else:
            price = r["kalshi_no_ask"]
        if price is None or not (p_lo <= price < p_hi):
            continue
        # Playoff filter
        po = r["is_playoff"]
        if po_filter == "playoff" and po != 1: continue
        if po_filter == "reg" and po != 0: continue
        # Fire!
        won = (settled[tk] == side)
        fires_total.append((price, won))
        # Sub-buckets
        if side == "yes":
            depth = r["kalshi_yes_ask_size"]
        else:
            depth = r["kalshi_no_ask_size"]
        d_b = depth_bucket(depth)
        # Consensus gap
        eu = r["eu_yes_prob"]
        gap_pp = None
        if eu is not None:
            if side == "yes":
                gap_pp = (price - eu) * 100  # buyer wants kalshi < eu (negative)
            else:
                # NO side: comparison is (1-price) for yes-implied
                gap_pp = ((1 - price) - eu) * 100
        g_b = gap_bucket(gap_pp)
        dow = dow_label(r["timestamp"])
        fires_by_subcell[(d_b, g_b, dow)].append((price, won))
        fired_for_ticker = True

    return fires_total, fires_by_subcell


def summarize(fires):
    n = len(fires)
    if n == 0: return None
    wins = sum(1 for f in fires if f[1])
    wr = wins / n * 100
    pnls = [calculate_pnl(f[0], f[1]) for f in fires]
    total = sum(pnls)
    roi = total / (n * STAKE_USDC) * 100 if n else 0
    pnls_sorted = sorted(pnls, reverse=True)
    d3 = sum(pnls_sorted[3:]) if n > 3 else 0
    d3_roi = d3 / max((n - 3) * STAKE_USDC, 1) * 100 if n > 3 else 0
    return {"n": n, "wins": wins, "wr": wr, "roi": roi, "d3_roi": d3_roi, "pnl": total}


def main():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    settled = {}
    for r in c.execute(
        "SELECT kalshi_ticker, result FROM ticker_settlements "
        "WHERE result IN ('yes','no')"
    ):
        settled[r["kalshi_ticker"]] = r["result"]
    print(f"Settled tickers: {len(settled)}\n")

    for cand in CANDIDATES:
        sport, phase, side, p_lo, p_hi, m_lo, m_hi, po = cand
        label = (f"{sport} {phase} {side} {p_lo:.2f}-{p_hi:.2f} "
                 f"mts={m_lo}-{m_hi} po={po}")
        print(f"=== {label} ===")
        total, by_sub = run_candidate(cand, c, settled)
        agg = summarize(total)
        if not agg:
            print("  (no fires)\n")
            continue
        print(f"  Total: n={agg['n']} WR={agg['wr']:.0f}% "
              f"ROI={agg['roi']:+.1f}% d3={agg['d3_roi']:+.1f}% pnl=${agg['pnl']:+.0f}")

        # Aggregate by depth
        by_depth = defaultdict(list)
        by_gap = defaultdict(list)
        by_dow = defaultdict(list)
        for (d, g, dow), fires in by_sub.items():
            by_depth[d].extend(fires)
            by_gap[g].extend(fires)
            by_dow[dow].extend(fires)

        print(f"  --- by depth (side's ask_size) ---")
        for d_b in ["<50", "50-500", "500-2k", "2k+", "?"]:
            s = summarize(by_depth.get(d_b, []))
            if s and s["n"] >= 5:
                print(f"    depth {d_b:7}  n={s['n']:3} WR={s['wr']:3.0f}% "
                      f"ROI={s['roi']:+5.1f}% d3={s['d3_roi']:+5.1f}%")
        print(f"  --- by consensus gap (kalshi-eu) ---")
        for g_b in ["<3pp", "3-6pp", "kalshi_lt6+", "kalshi_gt6+", "?"]:
            s = summarize(by_gap.get(g_b, []))
            if s and s["n"] >= 5:
                print(f"    gap   {g_b:13}  n={s['n']:3} WR={s['wr']:3.0f}% "
                      f"ROI={s['roi']:+5.1f}% d3={s['d3_roi']:+5.1f}%")
        print(f"  --- by day of week ---")
        for dow in ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]:
            s = summarize(by_dow.get(dow, []))
            if s and s["n"] >= 5:
                print(f"    {dow}  n={s['n']:3} WR={s['wr']:3.0f}% "
                      f"ROI={s['roi']:+5.1f}% d3={s['d3_roi']:+5.1f}%")
        print()


if __name__ == "__main__":
    main()
