"""Test that entry snapshots receive parallel us + eu consensus + pinnacle age."""
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def _reset_odds_caches():
    from strategies import sports_trader as st
    st._ODDS_CACHE.clear()
    # Reset tracker so age starts at 0
    st._PINNACLE_AGE.__init__()
    yield
    st._ODDS_CACHE.clear()
    st._PINNACLE_AGE.__init__()


@pytest.mark.asyncio
async def test_dual_region_consensus_returns_both_probs(_reset_odds_caches):
    """Helper returns (us_prob, eu_prob, pinnacle_prob, age_s) tuple."""
    from strategies.sports_trader import SportsTraderStrategy, _ODDS_CACHE
    # Seed the cache directly so _fetch_odds doesn't hit the network
    game_us = {"id": "g1", "home_team": "H", "away_team": "A",
               "bookmakers": [
                   {"key": "fanduel", "markets": [{"key": "h2h", "outcomes": [
                       {"name": "H", "price": 1.9}, {"name": "A", "price": 2.0}]}]},
                   {"key": "draftkings", "markets": [{"key": "h2h", "outcomes": [
                       {"name": "H", "price": 1.95}, {"name": "A", "price": 1.95}]}]},
               ]}
    game_eu = {"id": "g1", "home_team": "H", "away_team": "A",
               "bookmakers": [
                   {"key": "pinnacle", "markets": [{"key": "h2h", "outcomes": [
                       {"name": "H", "price": 1.85}, {"name": "A", "price": 2.05}]}]},
                   {"key": "marathonbet", "markets": [{"key": "h2h", "outcomes": [
                       {"name": "H", "price": 1.86}, {"name": "A", "price": 2.04}]}]},
               ]}
    import time
    _ODDS_CACHE[("us", "basketball_nba")] = (time.time(), [game_us], {"used": 0, "remaining": 1, "last": 0})
    _ODDS_CACHE[("eu", "basketball_nba")] = (time.time(), [game_eu], {"used": 0, "remaining": 1, "last": 0})

    # Build a minimal strategy instance
    config = MagicMock()
    config.ODDS_API_KEY = "k"
    config.ODDS_API_REGION = "us"
    strat = SportsTraderStrategy.__new__(SportsTraderStrategy)
    strat.config = config
    strat.REGION_OVERRIDE = "eu"  # simulate V31
    strat._odds_api_key = "k"
    strat.odds_api_usage = {}

    us_prob, eu_prob, pin_prob, age_s = await strat._dual_region_consensus("basketball_nba", game_eu)

    assert us_prob is not None
    assert eu_prob is not None
    assert pin_prob is not None
    assert age_s is not None  # hash observed during _fetch_odds cache hit
    # Rough sanity — probabilities in [0, 1]
    for p in (us_prob, eu_prob, pin_prob):
        assert 0.0 <= p <= 1.0


def test_consensus_probability_includes_n_books():
    """Consensus dict carries n_books = count of books contributing both legs.
    Used by MIN_N_BOOKS gate to skip thin-coverage signals (NHL min 2, MLS min 3)."""
    from strategies.sports_trader import SportsTraderStrategy
    config = MagicMock()
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    om = MagicMock()
    client = MagicMock()
    strat = SportsTraderStrategy(config, client, om)
    game_2way = {
        "home_team": "H", "away_team": "A",
        "bookmakers": [
            {"key": "b1", "markets": [{"key": "h2h", "outcomes": [
                {"name": "H", "price": 1.9}, {"name": "A", "price": 2.0}]}]},
            {"key": "b2", "markets": [{"key": "h2h", "outcomes": [
                {"name": "H", "price": 1.95}, {"name": "A", "price": 1.95}]}]},
            {"key": "b3", "markets": [{"key": "h2h", "outcomes": [
                {"name": "H", "price": 1.92}, {"name": "A", "price": 1.98}]}]},
        ],
    }
    cons = strat._consensus_probability(game_2way)
    assert cons["n_books"] == 3
    # 3-way (soccer) — same n_books semantics; draw_prob also present
    game_3way = {
        "home_team": "H", "away_team": "A",
        "bookmakers": [
            {"key": "b1", "markets": [{"key": "h2h", "outcomes": [
                {"name": "H", "price": 2.0}, {"name": "A", "price": 4.0},
                {"name": "Draw", "price": 3.0}]}]},
            {"key": "b2", "markets": [{"key": "h2h", "outcomes": [
                {"name": "H", "price": 2.05}, {"name": "A", "price": 3.95},
                {"name": "Draw", "price": 3.05}]}]},
        ],
    }
    cons3 = strat._consensus_probability(game_3way)
    assert cons3["n_books"] == 2
    assert "draw_prob" in cons3


def test_min_n_books_attribute_default_none():
    """Default MIN_N_BOOKS=None so existing strategies keep legacy behaviour."""
    from strategies.sports_trader import SportsTraderStrategy
    assert SportsTraderStrategy.MIN_N_BOOKS is None
