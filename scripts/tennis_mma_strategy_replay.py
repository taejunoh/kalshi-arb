"""Phase 0 replay for tennis/MMA strategy portfolio (A1/A2/A3/B1/C).

Reads `odds_observations` rows, simulates each strategy's gate logic
exactly as the live class does, and outputs per-strategy fire-by-fire
P&L with WR / ROI / Wilson CI. Run before paper-twin enablement to
guard against selection-bias traps (see
memory/bid_imbalance_v2_2026-05-15.md).

Usage:
    venv/bin/python scripts/tennis_mma_strategy_replay.py \\
        --db data/bot.db --start 2026-05-11 --end 2026-05-15 \\
        --strategy tennis_pinnacle_arb_v1
"""
from __future__ import annotations

import argparse
import logging
import math
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

# Add repo root to path for direct script execution.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("tennis_mma_strategy_replay")

KALSHI_FEE_PCT = 0.07  # matches core.config default


def load_observations(
    conn: sqlite3.Connection, *, sports: tuple[str, ...],
    start: str, end: str,
) -> list[dict]:
    """Return odds_observations rows for given sports within [start, end)."""
    placeholders = ",".join(["?"] * len(sports))
    sql = f"""
        SELECT * FROM odds_observations
        WHERE sport IN ({placeholders})
          AND timestamp >= ?
          AND timestamp < ?
        ORDER BY timestamp ASC
    """
    cur = conn.cursor()
    cur.execute(sql, (*sports, start, end))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n == 0:
        return (0.0, 0.0)
    phat = wins / n
    denom = 1.0 + z**2 / n
    center = (phat + z**2 / (2 * n)) / denom
    half = z * math.sqrt(phat * (1 - phat) / n + z**2 / (4 * n**2)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def resolve_settled_outcomes(
    conn: sqlite3.Connection, tickers: list[str]
) -> dict[str, float]:
    """Return {ticker: settle_value in [0,1]} for tickers that have settled.

    Kalshi binary markets settle to 1.0 (YES side wins) or 0.0 (NO side
    wins). Unknown / not-yet-settled tickers are omitted from the result.

    Reads from `ticker_settlements` (kalshi_ticker, result) — see
    `core/database.py` schema. `result` is the lowercase string "yes" or
    "no"; this maps to 1.0 / 0.0. Any other value is treated as unsettled
    and omitted.
    """
    if not tickers:
        return {}
    placeholders = ",".join(["?"] * len(tickers))
    sql = f"""
        SELECT kalshi_ticker, result
          FROM ticker_settlements
         WHERE kalshi_ticker IN ({placeholders})
           AND result IN ('yes', 'no')
    """
    cur = conn.cursor()
    cur.execute(sql, tickers)
    return {
        row[0]: (1.0 if row[1] == "yes" else 0.0)
        for row in cur.fetchall()
    }


def simulate_tennis_pinnacle_arb_v1(
    rows: list[dict], settled: dict[str, float]
) -> list[dict]:
    """A1 replay: |Kalshi-Pinnacle| >= 5pp AND eu drift <= 3pp.

    Mirrors strategies/sports_tennis_pinnacle_arb_v1.py evaluate() gates.
    """
    fires = []
    seen = set()
    for r in rows:
        ticker = r["kalshi_ticker"]
        if ticker in seen:
            continue
        if r.get("sport") not in {"ATP", "WTA"}:
            continue
        if not r.get("has_pinnacle"):
            continue
        if (r.get("pinnacle_age_s") or 1e9) > 600:
            continue
        mts = r.get("minutes_to_start")
        if mts is None or mts < 60 or mts > 4320:
            continue
        pin = r["pinnacle_yes_prob"]
        eu = r["eu_yes_prob"]
        if abs(eu - pin) > 0.03:
            continue
        ya = r["kalshi_yes_ask"]
        na = r["kalshi_no_ask"]
        gap_yes = pin - ya
        gap_no = (1.0 - pin) - na
        if gap_yes >= 0.05 and gap_yes >= gap_no:
            side, price, edge = "yes", ya, gap_yes
        elif gap_no >= 0.05:
            side, price, edge = "no", na, gap_no
        else:
            continue
        if ticker not in settled:
            continue
        fires.append({
            "strategy": "tennis_pinnacle_arb_v1", "ticker": ticker, "side": side,
            "price": price, "edge": edge, "settle": settled[ticker],
        })
        seen.add(ticker)
    return fires


def simulate_ufc_chalk_fade_v1(
    rows: list[dict], settled: dict[str, float]
) -> list[dict]:
    """B1 replay: kalshi_yes_ask >= 0.78 AND pinnacle_yes_prob >= 0.78."""
    fires = []
    seen = set()
    for r in rows:
        ticker = r["kalshi_ticker"]
        if ticker in seen:
            continue
        if r.get("sport") != "UFC":
            continue
        if not r.get("has_pinnacle"):
            continue
        if (r.get("pinnacle_age_s") or 1e9) > 600:
            continue
        mts = r.get("minutes_to_start")
        if mts is None or mts < 60 or mts > 4320:
            continue
        ya = r["kalshi_yes_ask"]
        pin = r["pinnacle_yes_prob"]
        if ya < 0.78 or pin < 0.78:
            continue
        if ticker not in settled:
            continue
        fires.append({
            "strategy": "ufc_chalk_fade_v1", "ticker": ticker, "side": "no",
            "price": r["kalshi_no_ask"], "edge": (ya + pin) / 2 - 0.78,
            "settle": settled[ticker],
        })
        seen.add(ticker)
    return fires


def simulate_tennis_surface_skew_v1(
    rows: list[dict], settled: dict[str, float], slug_meta: dict,
) -> list[dict]:
    """C replay: |Pinnacle - eu| >= 0.04 on clay or grass surface."""
    fires = []
    seen = set()
    for r in rows:
        ticker = r["kalshi_ticker"]
        if ticker in seen:
            continue
        if r.get("sport") not in {"ATP", "WTA"}:
            continue
        slug = r.get("tournament_slug")
        meta = slug_meta.get(slug)
        if meta is None or meta.get("surface") not in {"clay", "grass"}:
            continue
        if not r.get("has_pinnacle"):
            continue
        if (r.get("pinnacle_age_s") or 1e9) > 600:
            continue
        pin = r["pinnacle_yes_prob"]
        eu = r["eu_yes_prob"]
        skew = pin - eu
        if abs(skew) < 0.04:
            continue
        if skew > 0:
            side, price = "yes", r["kalshi_yes_ask"]
        else:
            side, price = "no", r["kalshi_no_ask"]
        if not (0.20 <= price <= 0.80):
            continue
        if ticker not in settled:
            continue
        fires.append({
            "strategy": "tennis_surface_skew_v1", "ticker": ticker, "side": side,
            "price": price, "edge": abs(skew), "settle": settled[ticker],
        })
        seen.add(ticker)
    return fires


def _compute_summary(fires: list[dict], strategy: str) -> dict:
    if not fires:
        return {"strategy": strategy, "n": 0, "wr": 0.0, "roi": 0.0,
                "wilson_lo": 0.0, "wilson_hi": 0.0}
    wins = 0
    pnl = 0.0
    cost_total = 0.0
    for f in fires:
        price = f["price"]
        settle = f["settle"]
        side = f["side"]
        is_win = (side == "yes" and settle == 1.0) or (side == "no" and settle == 0.0)
        if is_win:
            wins += 1
            pnl += (1.0 - price) - KALSHI_FEE_PCT * price
        else:
            pnl += -price - KALSHI_FEE_PCT * price
        cost_total += price
    n = len(fires)
    wr = wins / n
    roi = pnl / cost_total if cost_total > 0 else 0.0
    lo, hi = wilson_ci(wins, n)
    return {
        "strategy": strategy, "n": n, "wr": wr, "roi": roi,
        "wilson_lo": lo, "wilson_hi": hi,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/bot.db")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--strategy",
                        choices=["tennis_pinnacle_arb_v1", "ufc_chalk_fade_v1",
                                 "tennis_surface_skew_v1", "all"],
                        default="all")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    from strategies.sports_trader import TENNIS_SLUG_META

    if args.strategy in ("tennis_pinnacle_arb_v1", "all"):
        rows = load_observations(conn, sports=("ATP", "WTA"), start=args.start, end=args.end)
        settled = resolve_settled_outcomes(conn, [r["kalshi_ticker"] for r in rows])
        fires = simulate_tennis_pinnacle_arb_v1(rows, settled)
        print(_compute_summary(fires, "tennis_pinnacle_arb_v1"))

    if args.strategy in ("ufc_chalk_fade_v1", "all"):
        rows = load_observations(conn, sports=("UFC",), start=args.start, end=args.end)
        settled = resolve_settled_outcomes(conn, [r["kalshi_ticker"] for r in rows])
        fires = simulate_ufc_chalk_fade_v1(rows, settled)
        print(_compute_summary(fires, "ufc_chalk_fade_v1"))

    if args.strategy in ("tennis_surface_skew_v1", "all"):
        rows = load_observations(conn, sports=("ATP", "WTA"), start=args.start, end=args.end)
        settled = resolve_settled_outcomes(conn, [r["kalshi_ticker"] for r in rows])
        fires = simulate_tennis_surface_skew_v1(rows, settled, TENNIS_SLUG_META)
        print(_compute_summary(fires, "tennis_surface_skew_v1"))


if __name__ == "__main__":
    main()
