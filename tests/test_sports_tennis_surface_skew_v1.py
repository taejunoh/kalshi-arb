from unittest.mock import MagicMock

from strategies.sports_tennis_surface_skew_v1 import SportsTennisSurfaceSkewV1Strategy


def _make_strategy(slug="tennis_atp_french_open"):
    cfg = MagicMock(KALSHI_FEE_PER_CONTRACT=0.07, INITIAL_BANKROLL=10000)
    s = SportsTennisSurfaceSkewV1Strategy.__new__(SportsTennisSurfaceSkewV1Strategy)
    s.config = cfg
    s._order_manager = MagicMock(bankroll=10000)
    s._last_pinnacle_yes_prob = 0.65
    s._last_pinnacle_age_s = 100
    s._last_tournament_slug = slug
    s._traded_tickers = {}
    return s


def test_fires_buy_yes_when_pinnacle_higher_on_clay():
    s = _make_strategy()
    sig = s.evaluate(kalshi_yes=0.58, kalshi_no=0.42, fair_prob=0.58,
                     ticker="KXATPMATCH-X")
    assert sig is not None
    assert sig["side"] == "yes"


def test_fires_buy_no_when_pinnacle_lower_on_grass():
    s = _make_strategy(slug="tennis_atp_wimbledon")
    s._last_pinnacle_yes_prob = 0.40
    sig = s.evaluate(kalshi_yes=0.50, kalshi_no=0.50, fair_prob=0.50,
                     ticker="KXATPMATCH-X")
    assert sig is not None
    assert sig["side"] == "no"


def test_skips_hard_court():
    s = _make_strategy(slug="tennis_atp_aus_open_singles")
    sig = s.evaluate(kalshi_yes=0.58, kalshi_no=0.42, fair_prob=0.58,
                     ticker="KXATPMATCH-X")
    assert sig is None


def test_skips_skew_below_4pp():
    s = _make_strategy()
    s._last_pinnacle_yes_prob = 0.60
    sig = s.evaluate(kalshi_yes=0.58, kalshi_no=0.42, fair_prob=0.58,
                     ticker="KXATPMATCH-X")
    assert sig is None


def test_skips_outside_price_band():
    s = _make_strategy()
    s._last_pinnacle_yes_prob = 0.90
    sig = s.evaluate(kalshi_yes=0.85, kalshi_no=0.15, fair_prob=0.85,
                     ticker="KXATPMATCH-X")
    assert sig is None


def test_skips_unknown_slug():
    s = _make_strategy(slug="tennis_random_unknown_event")
    sig = s.evaluate(kalshi_yes=0.58, kalshi_no=0.42, fair_prob=0.58,
                     ticker="KXATPMATCH-X")
    assert sig is None
