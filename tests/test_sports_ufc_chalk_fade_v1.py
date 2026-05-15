from unittest.mock import MagicMock

from strategies.sports_ufc_chalk_fade_v1 import SportsUfcChalkFadeV1Strategy


def _make_strategy():
    cfg = MagicMock(KALSHI_FEE_PER_CONTRACT=0.07, INITIAL_BANKROLL=10000)
    s = SportsUfcChalkFadeV1Strategy.__new__(SportsUfcChalkFadeV1Strategy)
    s.config = cfg
    s._order_manager = MagicMock(bankroll=10000)
    s._last_pinnacle_yes_prob = 0.82
    s._last_pinnacle_age_s = 100
    s._traded_tickers = {}
    return s


def test_fires_buy_no_when_both_kalshi_and_pinnacle_chalk():
    s = _make_strategy()
    sig = s.evaluate(kalshi_yes=0.80, kalshi_no=0.20, fair_prob=0.82,
                     ticker="KXUFCFIGHT-X")
    assert sig is not None
    assert sig["side"] == "no"
    assert sig["price"] == 0.20


def test_skips_below_chalk_threshold():
    s = _make_strategy()
    sig = s.evaluate(kalshi_yes=0.75, kalshi_no=0.25, fair_prob=0.75,
                     ticker="KXUFCFIGHT-X")
    assert sig is None


def test_skips_when_pinnacle_disagrees():
    s = _make_strategy()
    s._last_pinnacle_yes_prob = 0.70
    sig = s.evaluate(kalshi_yes=0.80, kalshi_no=0.20, fair_prob=0.70,
                     ticker="KXUFCFIGHT-X")
    assert sig is None


def test_skips_when_pinnacle_stale():
    s = _make_strategy()
    s._last_pinnacle_age_s = 1000
    sig = s.evaluate(kalshi_yes=0.80, kalshi_no=0.20, fair_prob=0.82,
                     ticker="KXUFCFIGHT-X")
    assert sig is None


def test_skips_when_pinnacle_missing():
    s = _make_strategy()
    s._last_pinnacle_yes_prob = None
    sig = s.evaluate(kalshi_yes=0.80, kalshi_no=0.20, fair_prob=0.50,
                     ticker="KXUFCFIGHT-X")
    assert sig is None
