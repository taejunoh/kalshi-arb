"""Live-strategy replay engine for promotion evaluation.

For each observer-recorded entry, simulates whether a given live strategy
would have fired on it, then computes simulated PnL using the settled outcome.

Strategy gates mirror the actual live-strategy class attributes verified
2026-05-02 against:
  - sports_longshot      (V3 baseline, US, no PRE_GAME_ONLY, no DEAD_ZONE)
  - sports_longshot_v31  (eu, PRE_GAME_ONLY, DEAD_ZONE 0.10-0.20)
  - sports_midshot       (V3 baseline, US)
  - sports_midshot_v31   (eu, PRE_GAME_ONLY, MIN_ENTRY_PRICE 0.20, dead_zone 0.25-0.30)
  - sports_underdog      (V3 baseline, US)
  - sports_underdog_v31  (eu, PRE_GAME_ONLY, MIN_ENTRY_PRICE 0.20)
  - sports_underdog_v32  (v31 + edge-scaled sizing — same gate as v31 for replay)
  - sports_v4_edge       (eu, MIN_EDGE 0.03, MIN_SIDE_PROB 0.50, ALLOWED_CELLS)

NOT included:
  - sports_underdog_v35, sports_midshot_v34: have ALLOWED_CELLS hardcoded to
    NBA/MLB/NHL — would never fire on new sports without explicit promotion.
  - sports_pinnacle_edge: archived per memory.

Each strategy returns either:
  {"side": "yes"|"no", "price": float, "size_usdc": float}  if fired
  None  if filter rejected
"""
from __future__ import annotations

from typing import Optional

KALSHI_FEE_PCT = 0.07


def _payoff(price: float) -> float:
    """Fee-adjusted payoff multiplier."""
    if price <= 0:
        return 0.0
    fee_pct = min(KALSHI_FEE_PCT, price, 1.0 - price) / price
    cost = price * (1 + fee_pct)
    return 1.0 / cost if cost > 0 else 0.0


def _clip_price_band(price: float, lo: float, hi: float) -> bool:
    return lo <= price <= hi


def _in_dead_zone(price: float, dz: tuple[float, float] | None) -> bool:
    if dz is None:
        return False
    lo, hi = dz
    return lo <= price < hi


# ----------------------------------------------------------------------
# Strategy gates — each takes a normalized entry dict and returns the
# fired signal {side, price, size} or None.
#
# Entry dict keys (provided by promotion_eval._load_entries):
#   yes_ask, no_ask, eu_prob, us_prob, pin_prob, n_books_eu,
#   has_pinnacle, is_yes_home, is_yes_draw, minutes_to_start
# ----------------------------------------------------------------------

def replay_longshot(e: dict, *, dead_zone: tuple | None = None,
                    min_entry_price: float = 0.05,
                    pre_game_only: bool = False,
                    min_payoff: float = 3.0,
                    price_min: float = 0.05, price_max: float = 0.25,
                    base_size: float = 4.0) -> Optional[dict]:
    """Longshot: cheap (<25¢) + payoff ≥ 3.0x. Tries YES then NO."""
    if pre_game_only and (e.get("minutes_to_start") or 0) <= 0:
        return None
    yes_ask, no_ask = e["yes_ask"], e.get("no_ask", 1 - e["yes_ask"])
    best = None
    best_payoff = 0.0
    for side, price in (("yes", yes_ask), ("no", no_ask)):
        if not _clip_price_band(price, max(price_min, min_entry_price), price_max):
            continue
        if _in_dead_zone(price, dead_zone):
            continue
        p = _payoff(price)
        if p >= min_payoff and p > best_payoff:
            best = {"side": side, "price": price, "size_usdc": base_size, "payoff": p}
            best_payoff = p
    return best


def replay_midshot(e: dict, *, min_entry_price: float = 0.05,
                   pre_game_only: bool = False,
                   min_payoff: float = 2.5,
                   price_min: float = 0.05, price_max: float = 0.35,
                   dead_zone: tuple | None = None,
                   base_size: float = 4.0) -> Optional[dict]:
    """Midshot: 5-35¢ + payoff ≥ 2.5x. Tries YES then NO."""
    if pre_game_only and (e.get("minutes_to_start") or 0) <= 0:
        return None
    yes_ask, no_ask = e["yes_ask"], e.get("no_ask", 1 - e["yes_ask"])
    best = None
    best_payoff = 0.0
    for side, price in (("yes", yes_ask), ("no", no_ask)):
        if not _clip_price_band(price, max(price_min, min_entry_price), price_max):
            continue
        if _in_dead_zone(price, dead_zone):
            continue
        p = _payoff(price)
        if p >= min_payoff and p > best_payoff:
            best = {"side": side, "price": price, "size_usdc": base_size, "payoff": p}
            best_payoff = p
    return best


def replay_underdog(e: dict, *, min_entry_price: float = 0.05,
                    pre_game_only: bool = False,
                    favorite_threshold: float = 0.60,
                    min_payoff: float = 2.0,
                    price_min: float = 0.05, price_max: float = 0.40,
                    base_size: float = 4.0) -> Optional[dict]:
    """Underdog: fade consensus favorite (>60%). Bets the SHORT side at 5-40¢."""
    if pre_game_only and (e.get("minutes_to_start") or 0) <= 0:
        return None
    fair_prob = e.get("eu_prob") if e.get("eu_prob") is not None else e.get("us_prob")
    if fair_prob is None:
        return None
    yes_ask, no_ask = e["yes_ask"], e.get("no_ask", 1 - e["yes_ask"])
    # YES team is favorite → bet NO (the underdog)
    if fair_prob > favorite_threshold and _clip_price_band(no_ask, max(price_min, min_entry_price), price_max):
        p = _payoff(no_ask)
        if p >= min_payoff:
            return {"side": "no", "price": no_ask, "size_usdc": base_size, "payoff": p}
    # NO team is favorite → bet YES (the underdog)
    if fair_prob < (1 - favorite_threshold) and _clip_price_band(yes_ask, max(price_min, min_entry_price), price_max):
        p = _payoff(yes_ask)
        if p >= min_payoff:
            return {"side": "yes", "price": yes_ask, "size_usdc": base_size, "payoff": p}
    return None


def replay_v4_edge(e: dict, *, min_edge: float = 0.03,
                   price_min: float = 0.05, price_max: float = 0.35,
                   payoff_min: float = 2.5,
                   min_side_prob: float = 0.50,
                   base_size: float = 4.0) -> Optional[dict]:
    """V4 edge: price band + payoff + edge gate + side_prob gate."""
    yes_ask, no_ask = e["yes_ask"], e.get("no_ask", 1 - e["yes_ask"])
    fair_prob = e.get("pin_prob") if e.get("pin_prob") is not None else e.get("eu_prob")
    if fair_prob is None:
        return None
    best = None
    best_edge = -1.0
    for side, price, side_prob in (
        ("yes", yes_ask, fair_prob),
        ("no", no_ask, 1 - fair_prob),
    ):
        if not _clip_price_band(price, price_min, price_max):
            continue
        if side_prob < min_side_prob:
            continue
        p = _payoff(price)
        if p < payoff_min:
            continue
        fee_pct = min(KALSHI_FEE_PCT, price, 1 - price) / price if price > 0 else 0
        cost = price * (1 + fee_pct)
        edge = side_prob - cost
        if edge < min_edge:
            continue
        if edge > best_edge:
            best = {"side": side, "price": price, "size_usdc": base_size, "edge": edge}
            best_edge = edge
    return best


# ----------------------------------------------------------------------
# PnL simulation — apply Kalshi fee at entry for the win/lose math, NOT at
# exit (matches live trade accounting and the existing weekly_live_phase
# backtest pattern).
# ----------------------------------------------------------------------

def sim_pnl(side: str, price: float, size_usdc: float, result: str) -> float:
    """Realized PnL on a $size_usdc bet. result ∈ {'yes', 'no'}.
    Fee = 0.07 * min(price, 1 - price) per contract.
    """
    contracts = size_usdc / price if price > 0 else 0
    fee = KALSHI_FEE_PCT * min(price, 1 - price) * contracts
    won = (side == "yes" and result == "yes") or (side == "no" and result == "no")
    if won:
        return contracts * (1 - price) - fee
    return -size_usdc - fee


def sim_cost(price: float, size_usdc: float) -> float:
    """Total cost of a $size_usdc bet (stake + fee). Used as ROI denominator
    so losses cap at -100% per the standard betting convention."""
    if price <= 0:
        return size_usdc
    contracts = size_usdc / price
    fee = KALSHI_FEE_PCT * min(price, 1 - price) * contracts
    return size_usdc + fee


def replay_v36_wnba(e: dict, *, lo: float = 0.35, hi: float = 0.55,
                    fair_min: float = 0.55, base_size: float = 8.0) -> Optional[dict]:
    """v36_wnba chalk-favorite at 35-55c with fair_prob > 0.55."""
    ya, na = e["yes_ask"], e.get("no_ask", 1 - e["yes_ask"])
    eu = e.get("eu_prob")
    if eu is None:
        return None
    # YES favorite path
    if eu > fair_min and lo <= ya <= hi:
        return {"side": "yes", "price": ya, "size_usdc": base_size}
    # NO favorite path
    if eu < (1 - fair_min) and lo <= na <= hi:
        return {"side": "no", "price": na, "size_usdc": base_size}
    return None


def replay_v37_wnba(e: dict, *, lo: float = 0.20, hi: float = 0.29,
                    payoff_min: float = 2.0, base_size: float = 8.0) -> Optional[dict]:
    """v37_wnba TEAM 20-29c band, payoff >= 2.0x. Identical mechanism to v37_soccer."""
    ya, na = e["yes_ask"], e.get("no_ask", 1 - e["yes_ask"])
    fee = 0.07
    best = None
    best_payoff = 0.0
    for side, price in (("yes", ya), ("no", na)):
        if not (lo <= price <= hi):
            continue
        fee_pct = min(fee, price, 1 - price) / price if price > 0 else 0
        payoff = 1.0 / (price * (1 + fee_pct))
        if payoff < payoff_min:
            continue
        if payoff > best_payoff:
            best = {"side": side, "price": price, "size_usdc": base_size, "payoff": payoff}
            best_payoff = payoff
    return best


# ----------------------------------------------------------------------
# Strategy registry — exposed for promotion_eval.
# Each entry: (display_name, callable_with_entry_dict)
# Configurations match the actual live class attribute snapshots.
# ----------------------------------------------------------------------

STRATEGIES = {
    "longshot_v31": lambda e: replay_longshot(
        e, dead_zone=(0.10, 0.20), pre_game_only=True, base_size=12.0,
    ),
    "midshot_v31": lambda e: replay_midshot(
        e, min_entry_price=0.20, dead_zone=(0.25, 0.30),
        pre_game_only=True, base_size=16.0,
    ),
    "underdog_v31": lambda e: replay_underdog(
        e, min_entry_price=0.20, pre_game_only=True, base_size=20.0,
    ),
    "underdog_v32": lambda e: _wrap_v32_size(
        replay_underdog(e, min_entry_price=0.20, pre_game_only=True, base_size=20.0)
    ),
    "v4_edge": lambda e: replay_v4_edge(e),
    "v36_wnba": replay_v36_wnba,
    "v37_wnba": replay_v37_wnba,
    # Legacy V3 baselines (US region) — useful for comparing eu vs us
    "longshot_v3": lambda e: replay_longshot(e, base_size=4.0),
    "midshot_v3": lambda e: replay_midshot(e, base_size=4.0),
    "underdog_v3": lambda e: replay_underdog(e, base_size=4.0),
}


def _wrap_v32_size(signal: Optional[dict]) -> Optional[dict]:
    """v32 multiplies base size by clamp(payoff/2.0, 0.5, 2.0)."""
    if signal is None:
        return None
    payoff = signal.get("payoff", 2.0)
    multiplier = min(2.0, max(0.5, payoff / 2.0))
    signal["size_usdc"] = signal["size_usdc"] * multiplier
    return signal
