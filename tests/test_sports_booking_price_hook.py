"""`_get_booking_price` — honest PAPER booking with ZERO live-order change.

Four LIVE + PAPER_TWIN strategies inherit `SportsTraderStrategy._get_limit_price`
(limit at adjusted fair value) while emitting a signal whose `price` is the
observed ask. Their paper twins were therefore booking instant fills at a price
that never existed on the book. The fix books the twin at the ask via the
booking-only `_get_booking_price` hook.

The load-bearing assertions here are the LIVE LIMIT REGRESSION LOCKS: for each
of the four strategies `_get_limit_price` must still return the fair-value
limit, because that value is the real Kalshi order price AND the input to the
MIN_ENTRY_PRICE / MAX_ENTRY_PRICE / ENTRY_PRICE_DEAD_ZONE gates. If one of
these ever flips to the ask, real-money behavior changed.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.order_manager import LIMIT_TTL_STRATEGIES, PAPER_TWIN_STRATEGIES
from strategies.sports_mlb_pitcher_edge_v1 import SportsMlbPitcherEdgeV1Strategy
from strategies.sports_mlb_pitcher_edge_v2_1 import SportsMlbPitcherEdgeV21Strategy
from strategies.sports_tennis_live_fade_v1 import SportsTennisLiveFadeV1Strategy
from strategies.sports_tennis_underdog_v1 import SportsTennisUnderdogV1Strategy
from strategies.sports_trader import SportsTraderStrategy


def _bare(cls):
    """Strategy instance without __init__ (no network / no DB)."""
    s = cls.__new__(cls)
    s.config = MagicMock(KALSHI_FEE_PER_CONTRACT=0.07)
    s._order_manager = MagicMock(bankroll=10000)
    s._last_pinnacle_yes_prob = None
    s._last_pinnacle_age_s = None
    s._traded_tickers = {}
    return s


# --------------------------------------------------------------------------
# Per-strategy fixtures: each returns (strategy, signal, fair_prob, ask).
# The signal is produced by the strategy's own evaluate() so the tests prove
# `signal["price"]` really is the observed ask for that strategy, rather than
# assuming it from a sibling.
# --------------------------------------------------------------------------

TICKER = "KX-T"


def _sig_tennis_live_fade_yes():
    s = _bare(SportsTennisLiveFadeV1Strategy)
    s._last_pinnacle_yes_prob = 0.74
    s._last_pinnacle_age_s = 100
    # ya / na are yes_ask_dollars / no_ask_dollars (sports_trader ~L1727).
    sig = s.evaluate(kalshi_yes=0.86, kalshi_no=0.16, fair_prob=0.74,
                     ticker="KXATPMATCH-X")
    return s, sig, 0.74, 0.86


def _sig_tennis_live_fade_no():
    s = _bare(SportsTennisLiveFadeV1Strategy)
    s._last_pinnacle_yes_prob = 0.26          # pinnacle NO prob = 0.74
    s._last_pinnacle_age_s = 100
    sig = s.evaluate(kalshi_yes=0.16, kalshi_no=0.86, fair_prob=0.26,
                     ticker="KXATPMATCH-X")
    return s, sig, 0.26, 0.86


def _sig_tennis_underdog():
    s = _bare(SportsTennisUnderdogV1Strategy)
    s._last_pinnacle_yes_prob = 0.30          # >= ask - 0.05, passes the veto
    sig = s.evaluate(kalshi_yes=0.27, kalshi_no=0.73, fair_prob=0.33,
                     ticker="KXATPMATCH-X")
    return s, sig, 0.33, 0.27


_PIT_INFO = {
    "era_diff": 3.0, "better_team": "HOU",
    "away_era": 2.10, "home_era": 5.10,
    "away_code": "HOU", "home_code": "SEA",
    "away_pitcher": "A", "home_pitcher": "B",
    "away_ip": 60.0, "home_ip": 55.0,
}


def _sig_mlb_v1():
    s = _bare(SportsMlbPitcherEdgeV1Strategy)
    s._load_pitcher_cache = lambda: None      # no sqlite in unit tests
    s._pitcher_cache = {TICKER: dict(_PIT_INFO)}
    sig = s.evaluate(kalshi_yes=0.62, kalshi_no=0.38, fair_prob=0.55,
                     ticker=TICKER)
    return s, sig, 0.55, 0.62


def _sig_mlb_v2_1():
    s = _bare(SportsMlbPitcherEdgeV21Strategy)
    s._load_pit_cache = lambda: None
    s._pit_cache = {"away": {TICKER: dict(_PIT_INFO)}, "home": {}}
    s._check_books_deep = lambda ticker: True
    sig = s.evaluate(kalshi_yes=0.62, kalshi_no=0.38, fair_prob=0.55,
                     ticker=TICKER)
    return s, sig, 0.55, 0.62


CASES = [
    ("tennis_live_fade_v1[yes]", _sig_tennis_live_fade_yes, "yes"),
    ("tennis_live_fade_v1[no]", _sig_tennis_live_fade_no, "no"),
    ("tennis_underdog_v1", _sig_tennis_underdog, "yes"),
    ("mlb_pitcher_edge_v1", _sig_mlb_v1, "yes"),
    ("mlb_pitcher_edge_v2_1", _sig_mlb_v2_1, "yes"),
]


@pytest.mark.parametrize("name,factory,side", CASES, ids=[c[0] for c in CASES])
def test_signal_price_is_the_observed_ask(name, factory, side):
    s, sig, fair, ask = factory()
    assert sig is not None, f"{name}: fixture must produce a firing signal"
    assert sig["side"] == side
    assert sig["price"] == ask


@pytest.mark.parametrize("name,factory,side", CASES, ids=[c[0] for c in CASES])
def test_live_limit_price_unchanged(name, factory, side):
    """REGRESSION LOCK — real-money behavior must not move.

    The live limit stays at adjusted fair value, NOT the ask. This value is
    what reaches Kalshi and what the entry-price gates judge.
    """
    s, sig, fair, ask = factory()
    expected = round(fair, 2) if side == "yes" else round(1 - fair, 2)
    limit = s._get_limit_price(sig, fair_prob=fair)
    assert limit == expected
    assert limit != ask, f"{name}: limit price must not have become the ask"


@pytest.mark.parametrize("name,factory,side", CASES, ids=[c[0] for c in CASES])
def test_booking_price_is_the_observed_ask(name, factory, side):
    s, sig, fair, ask = factory()
    limit = s._get_limit_price(sig, fair_prob=fair)
    assert s._get_booking_price(sig, fair, limit_price=limit) == ask


@pytest.mark.parametrize("name,factory,side", CASES, ids=[c[0] for c in CASES])
def test_booking_and_limit_actually_diverge(name, factory, side):
    """Non-vacuity: if these were equal the fix would be untested."""
    s, sig, fair, ask = factory()
    limit = s._get_limit_price(sig, fair_prob=fair)
    assert s._get_booking_price(sig, fair, limit) != limit


@pytest.mark.parametrize("strategy", [
    "sports_tennis_live_fade_v1",
    "sports_tennis_underdog_v1",
    "sports_mlb_pitcher_edge_v1",
    "sports_mlb_pitcher_edge_v2_1",
])
def test_all_four_are_live_twin_routed(strategy):
    """The fix only matters for strategies that book a paper twin alongside a
    live order. If one leaves these sets, revisit the override."""
    assert strategy in PAPER_TWIN_STRATEGIES
    assert strategy in LIMIT_TTL_STRATEGIES


def test_base_hook_is_identity():
    """Every other strategy must be untouched: default booking == limit."""
    base = SportsTraderStrategy.__new__(SportsTraderStrategy)
    sig = {"side": "yes", "price": 0.86}
    assert base._get_booking_price(sig, 0.74, limit_price=0.74) == 0.74


def test_paper_only_parent_v2_not_overridden():
    """`sports_mlb_pitcher_edge_v2` runs as its own PAPER-ONLY strategy. The
    override is deliberately on v2_1 so the parent's ledger is not re-based
    out of scope — see the comment in sports_mlb_pitcher_edge_v2_1."""
    from strategies.sports_mlb_pitcher_edge_v2 import SportsMlbPitcherEdgeV2Strategy
    assert "_get_booking_price" not in vars(SportsMlbPitcherEdgeV2Strategy)
    assert "_get_booking_price" in vars(SportsMlbPitcherEdgeV21Strategy)
    assert "sports_mlb_pitcher_edge_v2" not in PAPER_TWIN_STRATEGIES


def test_dispatch_forwards_booking_price_without_touching_order_price(tmp_path):
    s = SportsTraderStrategy.__new__(SportsTraderStrategy)
    s.STRATEGY_NAME = "sports_tennis_live_fade_v1"
    s.TRADED_TICKERS_FILE = str(tmp_path / "t.json")
    s._traded_tickers = {}
    s._order_manager = MagicMock()
    s._order_manager.market_order = AsyncMock(return_value={"status": "skipped"})
    asyncio.run(s._dispatch_entry(
        ticker=TICKER, side="yes", size=4.0, limit_price=0.74,
        booking_price=0.86, minutes_to_start=None, matched_game={},
        sport="ATP", pinnacle_prob=0.74,
    ))
    kw = s._order_manager.market_order.await_args.kwargs
    assert kw["price"] == 0.74
    assert kw["booking_price"] == 0.86


def test_dispatch_without_booking_price_is_backward_compatible(tmp_path):
    s = SportsTraderStrategy.__new__(SportsTraderStrategy)
    s.STRATEGY_NAME = "sports_longshot_v31"
    s.TRADED_TICKERS_FILE = str(tmp_path / "t.json")
    s._traded_tickers = {}
    s._order_manager = MagicMock()
    s._order_manager.market_order = AsyncMock(return_value={"status": "skipped"})
    asyncio.run(s._dispatch_entry(
        ticker=TICKER, side="yes", size=3.0, limit_price=0.30,
        minutes_to_start=None, matched_game={}, sport="NBA",
        pinnacle_prob=0.31,
    ))
    kw = s._order_manager.market_order.await_args.kwargs
    assert kw["price"] == 0.30
    assert kw["booking_price"] is None
