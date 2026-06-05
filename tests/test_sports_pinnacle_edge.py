"""Unit tests for Sports Pinnacle Edge strategy."""
from unittest.mock import MagicMock

from strategies.sports_pinnacle_edge import SportsPinnacleEdgeStrategy


def _make_strategy(yes_depth: float = 100.0, no_depth: float = 100.0,
                   pinnacle_yes_prob: float = None,
                   bankroll: float = 2000.0) -> SportsPinnacleEdgeStrategy:
    config = MagicMock()
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.ODDS_API_KEY = ""
    config.ODDS_API_REGION = "eu"
    # ④ REAL_FEE_GATE (flag OFF / live default): gate_fee_pct returns EXACTLY the
    # legacy inline min(fee, X, 1-X)/X the strategy used before commit 37b4f4c.
    # Stub it on the mock so evaluate()'s fee math stays byte-identical to prod.
    config.gate_fee_pct = lambda price: (
        0.0 if price <= 0 or price >= 1
        else min(0.07, price, 1.0 - price) / price
    )
    order_mgr = MagicMock()
    order_mgr.bankroll = bankroll
    order_mgr.sizing_bankroll = lambda s: order_mgr.bankroll
    client = MagicMock()
    client.get_order_book_depth = MagicMock(return_value=(yes_depth, no_depth))
    s = SportsPinnacleEdgeStrategy(config, order_mgr, client)
    s._last_pinnacle_yes_prob = pinnacle_yes_prob
    return s


def test_fires_yes_when_pinnacle_implies_large_yes_edge():
    """Pinnacle says 60% YES, Kalshi asks 0.45 → yes_edge ~ 0.60 - 0.45*(1+0.156) = 0.08 > 0.05."""
    strat = _make_strategy(pinnacle_yes_prob=0.60)
    signal = strat.evaluate(kalshi_yes=0.45, kalshi_no=0.60, fair_prob=0.60, ticker="T")
    assert signal is not None
    assert signal["side"] == "yes"
    assert signal["price"] == 0.45
    assert signal["edge"] >= 0.05


def test_fires_no_when_pinnacle_implies_large_no_edge():
    """Pinnacle says 30% YES (so 70% NO), Kalshi no_ask 0.55 → no_edge ~ 0.70 - 0.55*1.127 = 0.08."""
    strat = _make_strategy(pinnacle_yes_prob=0.30)
    signal = strat.evaluate(kalshi_yes=0.50, kalshi_no=0.55, fair_prob=0.30, ticker="T")
    assert signal is not None
    assert signal["side"] == "no"
    assert signal["price"] == 0.55


def test_returns_none_when_no_pinnacle():
    """Missing Pinnacle prob → skip (no fallback to consensus)."""
    strat = _make_strategy(pinnacle_yes_prob=None)
    signal = strat.evaluate(kalshi_yes=0.45, kalshi_no=0.60, fair_prob=0.60, ticker="T")
    assert signal is None


def test_returns_none_when_edge_too_small():
    """Pinnacle 55%, Kalshi 0.52 → yes_edge ~ 0.55 - 0.52*1.135 = -0.04 < 0.05."""
    strat = _make_strategy(pinnacle_yes_prob=0.55)
    signal = strat.evaluate(kalshi_yes=0.52, kalshi_no=0.50, fair_prob=0.55, ticker="T")
    assert signal is None


def test_chooses_side_with_larger_edge():
    """Both sides have edge: pick the larger."""
    # Pinnacle 75% YES → yes_edge = 0.75 - 0.50*1.14 = 0.18; no_edge = 0.25 - 0.52*... small
    strat = _make_strategy(pinnacle_yes_prob=0.75)
    signal = strat.evaluate(kalshi_yes=0.50, kalshi_no=0.52, fair_prob=0.75, ticker="T")
    assert signal["side"] == "yes"


def test_rejects_when_insufficient_depth():
    """Even with edge, shallow book blocks trade."""
    strat = _make_strategy(yes_depth=0.5, no_depth=0.5, pinnacle_yes_prob=0.60, bankroll=2000.0)
    # expected size = min(3, 2000*0.02) = 3 USDC; depth 0.5 < 3 → None
    signal = strat.evaluate(kalshi_yes=0.45, kalshi_no=0.60, fair_prob=0.60, ticker="T-LAL")
    assert signal is None


def test_size_is_2pct_bankroll_capped_at_3():
    """Small bankroll: size scales linearly; large bankroll: capped at $3."""
    # Bankroll $50 → 2% = $1
    strat = _make_strategy(bankroll=50.0, pinnacle_yes_prob=0.60)
    # Need depth at least $1 for this size
    strat._client.get_order_book_depth = MagicMock(return_value=(5.0, 5.0))
    assert strat._get_trade_size({"side": "yes", "price": 0.45}) == 1.0

    # Bankroll $10000 → 2% = $200 but capped at $3
    strat2 = _make_strategy(bankroll=10000.0, pinnacle_yes_prob=0.60)
    assert strat2._get_trade_size({"side": "yes", "price": 0.45}) == 3.0


def test_strategy_name_and_file():
    strat = _make_strategy(pinnacle_yes_prob=0.60)
    assert strat.STRATEGY_NAME == "sports_pinnacle_edge"
    assert strat.TRADED_TICKERS_FILE == "data/sports_pinnacle_edge_tickers.json"
