"""Execution-realism modeling — closes the sim/LIVE gap.

Backtests historically assumed instant-fill at the signal-time price.
The late_close_tail incident (2026-05-21) showed this can cause 44×
divergence vs actual LIVE behavior:
  sim Config C  +$23.63/day
  paper actual  -$71 / 25h

Two effects this library models:

  1. **TTL miss** — LIMIT orders sit on the book for at most TTL seconds.
     If price moves through the limit before then, fill happens at the
     limit; if it stays above (for a BUY) the whole time, no fill.
     Sim that assumes instant-fill misses both the "no fill" path AND
     the slip you'd pay if you converted to a marketable order.

  2. **Wipe zone** — at tail prices (>=0.85 or <=0.15), Kalshi's flat
     $0.07/ct fee can be 50-100% of expected payoff. ANY slip in this
     zone destroys EV. A strategy that backtests +EV at avg ya=0.90
     usually loses money LIVE because breakeven WR is ~97%.

Usage in backtests:

    from core.execution_realism import realistic_pnl, is_wipe_zone, \\
        simulate_ttl_fill

    # Cheap path - apply fee + flag wipe-zone risk
    pnl = realistic_pnl(price=0.40, predicted="yes", actual="yes")

    # Realistic path - query DB for actual price during TTL window
    fill = simulate_ttl_fill(db_conn, ticker, signal_ts, signal_price,
                              side="yes", ttl_secs=180)
    if fill["filled"]:
        pnl = realistic_pnl(fill["fill_price"], predicted, actual)
"""
from __future__ import annotations
from datetime import datetime, timedelta
from typing import Optional
import json
import os
import sqlite3

KALSHI_FEE = 0.07
WIPE_ZONE_HIGH = 0.85
WIPE_ZONE_LOW = 0.15

# Empirical calibration (loaded from data/slip_calibration.json if available;
# falls back to the prior 0.02 heuristic otherwise).
_CALIBRATION_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "slip_calibration.json",
)
_CALIBRATION_CACHE: Optional[dict] = None


def _load_calibration() -> Optional[dict]:
    """Return cached calibration dict or load on first access.
    Returns None if file missing — caller falls back to heuristic."""
    global _CALIBRATION_CACHE
    if _CALIBRATION_CACHE is not None:
        return _CALIBRATION_CACHE
    if not os.path.exists(_CALIBRATION_PATH):
        return None
    try:
        with open(_CALIBRATION_PATH) as f:
            _CALIBRATION_CACHE = json.load(f)
        return _CALIBRATION_CACHE
    except (OSError, json.JSONDecodeError):
        return None


def calibrated_slip(price: float) -> float:
    """Return the empirically-calibrated slip magnitude for the price band.
    Uses p75 of |slip| (conservative — covers 75% of actual fills).

    Fallback policy when no calibration data:
      - Wipe zone (price>=0.85 or <=0.15): 0.02 (legacy heuristic)
      - Mid band: 0.0 (no slip claim without data)
    """
    cal = _load_calibration()
    if not cal or "bands" not in cal:
        return 0.02 if is_wipe_zone(price) else 0.0
    bands = [
        ("A_lt15", 0.0, 0.15),
        ("B_15_30", 0.15, 0.30),
        ("C_30_45", 0.30, 0.45),
        ("D_45_55", 0.45, 0.55),
        ("E_55_70", 0.55, 0.70),
        ("F_70_85", 0.70, 0.85),
        ("G_85_plus", 0.85, 1.0),
    ]
    for name, lo, hi in bands:
        if lo <= price < hi:
            stats = cal["bands"].get(name, {})
            if stats.get("skipped"):
                return 0.02 if is_wipe_zone(price) else 0.0
            return stats.get("p75_abs", 0.02)
    return 0.02 if is_wipe_zone(price) else 0.0


def realistic_pnl(price: float, predicted: str, actual: str,
                  fee: float = KALSHI_FEE) -> Optional[float]:
    """Per-$1 stake PnL after Kalshi flat fee.

    Returns None if price is degenerate (0 or 1 - would imply zero EV
    or infinite leverage). Caller should treat None as "skip this row".
    """
    if price is None or price <= 0 or price >= 1:
        return None
    won = (predicted == actual)
    return (1.0 - price - fee) if won else (-price - fee)


def is_wipe_zone(price: float) -> bool:
    """True if the entry price puts the bet in the slip+fee wipe zone.

    At ya=0.90: payoff if win = $0.10, fee = $0.07 -> after-fee = $0.03.
    A single tick of slip (price moves to 0.91) wipes out 33% of alpha.
    """
    if price is None:
        return False
    return price >= WIPE_ZONE_HIGH or price <= WIPE_ZONE_LOW


def continuous_wipe_risk(price: float) -> float:
    """Return risk multiplier (0.0-1.0) for continuous wipe-zone exposure.

    Binary is_wipe_zone() flags only ≥0.85 or ≤0.15. But risk grows
    smoothly toward tails. This returns:
      0.0 in safe band (0.20-0.80)
      Linear ramp to 1.0 at extreme (0.05 or 0.95)
      Capped at 1.0 outside

    Useful for adjusting confidence intervals or risk-weighted EV.
    """
    if price is None:
        return 0.0
    if 0.20 <= price <= 0.80:
        return 0.0
    if price < 0.20:
        # 0.20 → 0.0, 0.05 → 1.0, linear
        return min(1.0, max(0.0, (0.20 - price) / 0.15))
    # price > 0.80
    return min(1.0, max(0.0, (price - 0.80) / 0.15))


def breakeven_wr(price: float, fee: float = KALSHI_FEE) -> Optional[float]:
    """Win rate required to break even at given entry price after fee."""
    if price is None or price <= 0 or price >= 1:
        return None
    win_payoff = 1.0 - price - fee
    loss_payoff = -price - fee
    denom = win_payoff - loss_payoff
    if denom <= 0:
        return None
    wr = -loss_payoff / denom
    # Mathematically possible WR exceeds 1.0 when win_payoff is negative
    # (price + fee > 1) — at that point no win-rate can make money.
    if wr > 1.0:
        return None
    return wr


def simulate_ttl_fill(db_conn: sqlite3.Connection, ticker: str,
                      signal_ts: str, limit_price: float, side: str,
                      ttl_secs: int = 180) -> dict:
    """Simulate a LIMIT order's fill against the actual price tape.

    Reads odds_observations in [signal_ts, signal_ts+ttl] and decides:
      - filled=True if observed ask reached limit_price within TTL.
      - filled=False if price stayed unfavorable for entire TTL.

    side='yes' = buying yes contracts (watch kalshi_yes_ask).
    side='no'  = buying no contracts  (watch kalshi_no_ask).

    Conservative model: fill happens AT limit_price (no improvement).
    Real Kalshi maker flow could also fill - this errs on pessimism,
    the safe direction for sim trust.
    """
    price_col = "kalshi_yes_ask" if side == "yes" else "kalshi_no_ask"
    # Compute end_ts in Python — SQLite datetime() returns space-separated
    # format which breaks string comparison vs T-separated stored timestamps.
    try:
        # Tolerate trailing 'Z' or '+00:00' on signal_ts
        clean_ts = signal_ts.replace("Z", "").split("+")[0].split(".")[0]
        start = datetime.fromisoformat(clean_ts)
        end_ts = (start + timedelta(seconds=ttl_secs)).isoformat()
    except (ValueError, AttributeError):
        return {"filled": False, "fill_price": None,
                "miss_reason": f"unparseable signal_ts: {signal_ts}",
                "obs_count": 0}
    rows = db_conn.execute(
        f"""SELECT timestamp, {price_col} AS ask
            FROM odds_observations
            WHERE kalshi_ticker = ?
              AND timestamp >= ?
              AND timestamp <= ?
              AND {price_col} IS NOT NULL
            ORDER BY timestamp""",
        (ticker, signal_ts, end_ts),
    ).fetchall()
    if not rows:
        return {"filled": False, "fill_price": None,
                "miss_reason": "no observations in TTL window",
                "obs_count": 0}
    best_ask = min(r[1] for r in rows if r[1] is not None)
    if best_ask <= limit_price:
        return {"filled": True, "fill_price": limit_price,
                "miss_reason": None, "obs_count": len(rows)}
    return {"filled": False, "fill_price": None,
            "miss_reason": f"best ask {best_ask:.3f} > limit {limit_price:.3f}",
            "obs_count": len(rows)}


def slip_adjusted_pnl(price: float, predicted: str, actual: str,
                      ticker: Optional[str] = None,
                      signal_ts: Optional[str] = None,
                      db_conn: Optional[sqlite3.Connection] = None,
                      ttl_secs: int = 180,
                      wipe_zone_extra_slip: Optional[float] = None,
                      fee: float = KALSHI_FEE,
                      use_calibration: bool = True) -> Optional[float]:
    """Slip-adjusted PnL with TTL miss + slip penalty modeled.

    Three paths in priority order:
      A. ticker + db_conn -> actual TTL scan against price tape (most realistic).
         Returns None on miss (LIMIT would not have filled).
      B. use_calibration=True -> per-band empirical slip from
         data/slip_calibration.json (computed by scripts/calibrate_slip.py).
      C. Else heuristic -> 2pp in wipe zone only; mid-band gets flat fee.

    Returns None if degenerate price or order missed.
    """
    if price is None or price <= 0 or price >= 1:
        return None

    # A. Actual TTL scan
    if ticker is not None and db_conn is not None and signal_ts is not None:
        fill = simulate_ttl_fill(db_conn, ticker, signal_ts, price,
                                 side=predicted, ttl_secs=ttl_secs)
        if not fill["filled"]:
            return None
        return realistic_pnl(fill["fill_price"], predicted, actual, fee)

    # B / C. Heuristic — calibrated when available, falls back to old 2pp.
    if use_calibration:
        slip = calibrated_slip(price)
    else:
        slip = wipe_zone_extra_slip if wipe_zone_extra_slip is not None else 0.02
        if not is_wipe_zone(price):
            slip = 0.0
    effective_price = price + slip
    if effective_price >= 1.0:
        return None
    return realistic_pnl(effective_price, predicted, actual, fee)
