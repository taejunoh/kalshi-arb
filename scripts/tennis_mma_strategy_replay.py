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

KALSHI_FEE = 0.07  # matches core.config default


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
