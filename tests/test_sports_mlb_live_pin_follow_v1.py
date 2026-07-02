from unittest.mock import MagicMock

from strategies.sports_mlb_live_pin_follow_v1 import (
    SportsMlbLivePinFollowV1Strategy,
)


def _make_strategy(pin=0.70, pin_age=100):
    cfg = MagicMock(KALSHI_FEE_PER_CONTRACT=0.07, INITIAL_BANKROLL=10000)
    s = SportsMlbLivePinFollowV1Strategy.__new__(SportsMlbLivePinFollowV1Strategy)
    s.config = cfg
    s._order_manager = MagicMock(bankroll=10000)
    s._last_pinnacle_yes_prob = pin
    s._last_pinnacle_age_s = pin_age
    s._traded_tickers = {}
    return s


def test_fires_yes_when_pin_12pp_above_ask():
    s = _make_strategy(pin=0.70)
    sig = s.evaluate(kalshi_yes=0.55, kalshi_no=0.47, ticker="KXMLBGAME-X",
                     fair_prob=0.60)
    assert sig is not None
    assert sig["side"] == "yes"
    assert sig["price"] == 0.55


def test_fires_no_when_pin_favors_no_side():
    s = _make_strategy(pin=0.30)  # pin NO prob = 0.70
    sig = s.evaluate(kalshi_yes=0.47, kalshi_no=0.55, ticker="KXMLBGAME-X",
                     fair_prob=0.40)
    assert sig is not None
    assert sig["side"] == "no"
    assert sig["price"] == 0.55


def test_skips_gap_below_threshold():
    s = _make_strategy(pin=0.65)  # gap vs 0.55 ask = 0.10 < 0.12
    sig = s.evaluate(kalshi_yes=0.55, kalshi_no=0.47, ticker="KXMLBGAME-X",
                     fair_prob=0.60)
    assert sig is None


def test_skips_stale_pinnacle():
    s = _make_strategy(pin=0.70, pin_age=601)
    sig = s.evaluate(kalshi_yes=0.55, kalshi_no=0.47, ticker="KXMLBGAME-X",
                     fair_prob=0.60)
    assert sig is None


def test_skips_missing_pinnacle():
    s = _make_strategy(pin=None)
    sig = s.evaluate(kalshi_yes=0.55, kalshi_no=0.47, ticker="KXMLBGAME-X",
                     fair_prob=0.60)
    assert sig is None


def test_limit_price_is_signal_ask_not_fair():
    """Honest-booking override — the live_fade artifact regression guard.
    Booking must be at the ask the signal saw, never the fair-based price."""
    s = _make_strategy(pin=0.70)
    sig = s.evaluate(kalshi_yes=0.55, kalshi_no=0.47, ticker="KXMLBGAME-X",
                     fair_prob=0.60)
    assert s._get_limit_price(sig, fair_prob=0.60) == 0.55


def test_band_gate_declared_for_mlb_live_cell():
    """CELL_PRICE_BANDS carries the 0.50-0.75 replay band for (MLB, live)."""
    bands = SportsMlbLivePinFollowV1Strategy.CELL_PRICE_BANDS[("MLB", "live")]
    assert bands == [(0.50, 0.75)]
    assert SportsMlbLivePinFollowV1Strategy.ALLOWED_CELLS == frozenset(
        {("MLB", "live")}
    )
