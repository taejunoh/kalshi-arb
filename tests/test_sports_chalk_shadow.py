"""Tests for SportsChalkShadowStrategy — paper-only live-game chalk buyer."""
from unittest.mock import MagicMock

from strategies.sports_chalk_shadow import SportsChalkShadowStrategy


def _make():
    cfg = MagicMock()
    cfg.KALSHI_FEE_PER_CONTRACT = 0.07
    cfg.ODDS_API_KEY = ""
    cfg.ODDS_API_REGION = "eu"
    order_mgr = MagicMock()
    order_mgr.bankroll = 8000.0
    return SportsChalkShadowStrategy(cfg, order_mgr, MagicMock())


def test_identifiers_and_gates():
    s = SportsChalkShadowStrategy
    assert s.STRATEGY_NAME == "sports_chalk_shadow"
    assert s.TRADED_TICKERS_FILE == "data/sports_chalk_shadow_tickers.json"
    assert s.PRICE_MIN == 0.60
    assert s.PRICE_MAX == 0.95
    # Depth gate intentionally disabled for hypothesis testing — depth is
    # still recorded via clv_snapshots for post-hoc filtering.
    assert s.MIN_DEPTH_USDC == 0.0
    # Live-only across all three sports
    assert s.ALLOWED_CELLS == frozenset({
        ("MLB", "live"), ("NBA", "live"), ("NHL", "live"),
    })


def test_evaluate_yes_in_chalk_band():
    s = _make()
    sig = s.evaluate(kalshi_yes=0.78, kalshi_no=0.25, fair_prob=0.75)
    assert sig is not None
    assert sig["side"] == "yes"
    assert sig["price"] == 0.78
    assert sig["strategy"] == "chalk_shadow"


def test_evaluate_no_in_chalk_band_picks_no():
    """When YES is underdog (e.g., 0.22) and NO is chalk (0.78), pick NO."""
    s = _make()
    sig = s.evaluate(kalshi_yes=0.22, kalshi_no=0.78, fair_prob=0.22)
    assert sig is not None
    assert sig["side"] == "no"
    assert sig["price"] == 0.78


def test_evaluate_rejects_below_min():
    """60¢ threshold: 0.59 YES should not fire."""
    s = _make()
    assert s.evaluate(kalshi_yes=0.59, kalshi_no=0.42, fair_prob=0.55) is None


def test_evaluate_rejects_above_max():
    """95¢ threshold: 0.96 YES should not fire (approaching 1.0 is degenerate)."""
    s = _make()
    assert s.evaluate(kalshi_yes=0.96, kalshi_no=0.05, fair_prob=0.95) is None


def test_evaluate_rejects_both_neutral():
    """Both sides in 40-60¢ neutral → no chalk signal."""
    s = _make()
    assert s.evaluate(kalshi_yes=0.52, kalshi_no=0.49, fair_prob=0.50) is None


def test_evaluate_boundary_inclusive():
    """0.60 exactly should fire (PRICE_MIN <= price <= PRICE_MAX)."""
    s = _make()
    sig = s.evaluate(kalshi_yes=0.60, kalshi_no=0.41, fair_prob=0.58)
    assert sig is not None and sig["side"] == "yes"
    # 0.95 exactly also fires
    sig2 = s.evaluate(kalshi_yes=0.95, kalshi_no=0.06, fair_prob=0.94)
    assert sig2 is not None and sig2["side"] == "yes"


def test_evaluate_both_high_picks_higher():
    """Defensive: if both somehow qualify (high-vig synthetic case), pick
    the higher-priced side as the more-chalk signal."""
    s = _make()
    sig = s.evaluate(kalshi_yes=0.70, kalshi_no=0.80, fair_prob=0.50)
    assert sig is not None
    assert sig["side"] == "no"
    assert sig["price"] == 0.80


def test_trade_size_uses_4_or_2pct():
    s = _make()
    s._order_manager.bankroll = 1000.0
    assert s._get_trade_size({}) == 4.0  # capped at $4
    s._order_manager.bankroll = 100.0
    # 2% of 100 = 2, which is less than 4 → 2
    assert s._get_trade_size({}) == 2.0


def test_limit_price_uses_signal_price():
    s = _make()
    sig = {"side": "yes", "price": 0.77, "strategy": "chalk_shadow"}
    assert s._get_limit_price(sig, fair_prob=0.75) == 0.77


def test_not_in_live_strategies_by_default():
    """Verify chalk_shadow isn't in LIMIT_TTL_STRATEGIES or PAPER_TWIN_STRATEGIES —
    it's paper-only and uses standard market-order paper fills."""
    from core.order_manager import (
        LIMIT_TTL_STRATEGIES,
        PAPER_TWIN_STRATEGIES,
    )
    assert "sports_chalk_shadow" not in LIMIT_TTL_STRATEGIES
    assert "sports_chalk_shadow" not in PAPER_TWIN_STRATEGIES
