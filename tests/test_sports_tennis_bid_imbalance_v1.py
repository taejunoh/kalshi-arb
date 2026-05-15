from unittest.mock import MagicMock

from strategies.sports_tennis_bid_imbalance_v1 import SportsTennisBidImbalanceV1Strategy


def _make_strategy(yes_bid_size=100.0, no_bid_size=50.0, mts=300.0):
    cfg = MagicMock(KALSHI_FEE_PER_CONTRACT=0.07, INITIAL_BANKROLL=10000)
    s = SportsTennisBidImbalanceV1Strategy.__new__(SportsTennisBidImbalanceV1Strategy)
    s.config = cfg
    s._order_manager = MagicMock(bankroll=10000)
    s._client = MagicMock()
    s._client.get_top_of_book_sizes = MagicMock(
        return_value=(yes_bid_size, 100.0, no_bid_size, 100.0)
    )
    s._last_pinnacle_yes_prob = 0.70
    s._last_pinnacle_age_s = 100
    s._last_mts = mts
    s._traded_tickers = {}
    return s


def test_fires_buy_yes_when_imbalance_above_65pct_and_yes_band():
    s = _make_strategy(yes_bid_size=200.0, no_bid_size=50.0)
    sig = s.evaluate(kalshi_yes=0.60, kalshi_no=0.40, fair_prob=0.65,
                     ticker="KXATPMATCH-X")
    assert sig is not None
    assert sig["side"] == "yes"


def test_fires_buy_no_when_imbalance_below_35pct_and_no_band():
    s = _make_strategy(yes_bid_size=20.0, no_bid_size=200.0)
    s._last_pinnacle_yes_prob = 0.25
    sig = s.evaluate(kalshi_yes=0.30, kalshi_no=0.70, fair_prob=0.25,
                     ticker="KXATPMATCH-X")
    assert sig is not None
    assert sig["side"] == "no"


def test_skips_when_imbalance_neutral():
    s = _make_strategy(yes_bid_size=100.0, no_bid_size=100.0)
    sig = s.evaluate(kalshi_yes=0.60, kalshi_no=0.40, fair_prob=0.55,
                     ticker="KXATPMATCH-X")
    assert sig is None


def test_skips_when_outside_intraday_window():
    s = _make_strategy(mts=2000.0)
    sig = s.evaluate(kalshi_yes=0.60, kalshi_no=0.40, fair_prob=0.65,
                     ticker="KXATPMATCH-X")
    assert sig is None


def test_skips_when_pinnacle_disagrees_with_imbalance_direction():
    s = _make_strategy(yes_bid_size=200.0, no_bid_size=50.0)
    s._last_pinnacle_yes_prob = 0.40
    sig = s.evaluate(kalshi_yes=0.60, kalshi_no=0.40, fair_prob=0.40,
                     ticker="KXATPMATCH-X")
    assert sig is None


def test_skips_when_both_bid_sizes_zero():
    s = _make_strategy(yes_bid_size=0.0, no_bid_size=0.0)
    sig = s.evaluate(kalshi_yes=0.60, kalshi_no=0.40, fair_prob=0.65,
                     ticker="KXATPMATCH-X")
    assert sig is None


def test_blocks_mlb_and_other_team_sports():
    """A3 must block MLB/NBA/NHL/soccer/UFC — only ATP/WTA allowed."""
    blocked = SportsTennisBidImbalanceV1Strategy.SPORT_BLOCKLIST
    assert "MLB" in blocked
    assert "NBA" in blocked
    assert "NHL" in blocked
    assert "WNBA" in blocked
    assert "UFC" in blocked
    assert "MLS" in blocked
    assert "EPL" in blocked
    assert "WTA" not in blocked  # WTA is a target for A3
    assert "ATP" not in blocked  # ATP is a target for A3
