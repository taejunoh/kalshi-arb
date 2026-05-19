"""Stateless orderbook walker — phantom-quote defense for ladder Phase 0.

Kalshi's /markets/{ticker}/orderbook endpoint returns BID-side levels only
(yes_dollars = YES bids, no_dollars = NO bids). To walk the ASK side for
buy simulation, we derive synthetic asks from the opposite-direction bids
via complementarity: yes_ask_price = 1.0 - no_bid_price, qty unchanged.
The same trick gives NO asks from YES bids.

The walker accumulates contracts level-by-level until target_notional_usdc
is reached (or book exhausted) and reports the resulting average fill
price plus the gap vs top-of-book. A wide top-vs-walked gap is the
phantom-quote signature (top level has 1-contract resting order; real
fill happens at much worse price).
"""
from dataclasses import dataclass
from typing import Literal, Optional


@dataclass(frozen=True)
class WalkResult:
    avg_fill_price: float           # walked average (≥ top_ask)
    max_fillable_usdc: float         # notional actually fillable
    levels_consumed: int             # how many ladder levels were walked
    top_ask: float                   # top-of-book ask price
    top_vs_walked_gap_cents: float   # (avg_fill_price - top_ask) * 100


def walk_orderbook(
    client,
    ticker: str,
    side: Literal["yes", "no"],
    target_notional_usdc: float,
) -> Optional[WalkResult]:
    """Walk Kalshi orderbook ASKS for `ticker` `side` up to target notional.

    Returns None if the orderbook is empty on the relevant side or the
    REST call errors. Returns a WalkResult even if max_fillable_usdc <
    target_notional_usdc (caller filters).
    """
    try:
        book = client.get_orderbook_levels(ticker)
    except Exception:
        return None
    # ASK side derived from opposite-direction BID side
    bid_side_key = "no" if side == "yes" else "yes"
    bid_levels = book.get(bid_side_key, [])
    if not bid_levels:
        return None
    # Convert bid levels → synthetic ask levels via complementarity
    # bid_levels are (price, qty); ask_price = 1 - bid_price
    # Sort: highest bid first = lowest ask first (best for buyer)
    sorted_bids = sorted(bid_levels, key=lambda lv: -lv[0])
    ask_levels = [(round(1.0 - p, 4), q) for p, q in sorted_bids]
    top_ask = ask_levels[0][0]
    # Walk: accumulate cost until target_notional reached
    cum_contracts = 0.0
    cum_cost = 0.0
    levels_consumed = 0
    for ask_price, qty in ask_levels:
        levels_consumed += 1
        # Each contract costs ask_price USD. Take up to target_notional - cum_cost.
        remaining_budget = target_notional_usdc - cum_cost
        if remaining_budget <= 0:
            break
        max_contracts_at_level = remaining_budget / ask_price
        contracts_taken = min(qty, max_contracts_at_level)
        cum_contracts += contracts_taken
        cum_cost += contracts_taken * ask_price
        if cum_cost >= target_notional_usdc - 1e-9:
            break
    if cum_contracts == 0:
        return None
    avg_fill_price = cum_cost / cum_contracts
    max_fillable_usdc = cum_cost
    top_vs_walked_gap_cents = round((avg_fill_price - top_ask) * 100.0, 2)
    return WalkResult(
        avg_fill_price=round(avg_fill_price, 6),
        max_fillable_usdc=round(max_fillable_usdc, 4),
        levels_consumed=levels_consumed,
        top_ask=top_ask,
        top_vs_walked_gap_cents=top_vs_walked_gap_cents,
    )
