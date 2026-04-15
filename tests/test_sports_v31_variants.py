"""Smoke tests for V3.1 strategy variants — verify name, file, and region pinning."""
from unittest.mock import MagicMock

from strategies.sports_longshot import SportsLongshotStrategy
from strategies.sports_midshot import SportsMidshotStrategy
from strategies.sports_underdog import SportsUnderdogStrategy
from strategies.sports_longshot_v31 import SportsLongshotV31Strategy
from strategies.sports_midshot_v31 import SportsMidshotV31Strategy
from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy


def _make(klass):
    config = MagicMock()
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.ODDS_API_KEY = ""
    config.ODDS_API_REGION = "eu"
    return klass(config, MagicMock(), MagicMock())


def test_v3_pinned_to_us():
    assert _make(SportsLongshotStrategy)._get_odds_region() == "us"
    assert _make(SportsMidshotStrategy)._get_odds_region() == "us"
    assert _make(SportsUnderdogStrategy)._get_odds_region() == "us"


def test_v4_all_use_eu_region():
    """All V4 strategies must use EU region, not inherit midshot's us override.
    (sports_liquidity_filtered retired 2026-04-15 — dup of sports_midshot_v31.)"""
    from strategies.sports_pinnacle_edge import SportsPinnacleEdgeStrategy
    from strategies.sports_v4_edge import SportsV4EdgeStrategy
    from strategies.sports_consolidated import SportsConsolidatedStrategy
    for cls in (SportsPinnacleEdgeStrategy, SportsV4EdgeStrategy,
                SportsConsolidatedStrategy):
        assert _make(cls)._get_odds_region() == "eu", f"{cls.__name__} not on eu"


def test_v31_pinned_to_eu():
    assert _make(SportsLongshotV31Strategy)._get_odds_region() == "eu"
    assert _make(SportsMidshotV31Strategy)._get_odds_region() == "eu"
    assert _make(SportsUnderdogV31Strategy)._get_odds_region() == "eu"


def test_v31_has_distinct_names_and_files():
    for v3_cls, v31_cls in [
        (SportsLongshotStrategy, SportsLongshotV31Strategy),
        (SportsMidshotStrategy, SportsMidshotV31Strategy),
        (SportsUnderdogStrategy, SportsUnderdogV31Strategy),
    ]:
        v3 = _make(v3_cls)
        v31 = _make(v31_cls)
        assert v3.STRATEGY_NAME != v31.STRATEGY_NAME
        assert v3.TRADED_TICKERS_FILE != v31.TRADED_TICKERS_FILE
        assert v31.STRATEGY_NAME.endswith("_v31")


def test_v31_inherits_evaluate_logic():
    """V3.1 must produce the same signal as V3 on the same inputs (only region differs)."""
    v3 = _make(SportsLongshotStrategy)
    v31 = _make(SportsLongshotV31Strategy)
    args = {"kalshi_yes": 0.15, "kalshi_no": 0.88, "fair_prob": 0.20, "ticker": "T"}
    assert v3.evaluate(**args) == v31.evaluate(**args)
