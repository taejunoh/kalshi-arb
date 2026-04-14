"""REGION_OVERRIDE per-strategy test — lets V3 pin to us while V3.1/V4 use eu."""
from unittest.mock import MagicMock

from strategies.sports_trader import SportsTraderStrategy


def _make_base():
    config = MagicMock()
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.ODDS_API_KEY = ""
    config.ODDS_API_REGION = "eu"
    return SportsTraderStrategy(config, MagicMock(), MagicMock())


def test_default_uses_config_region():
    """With no REGION_OVERRIDE, uses config.ODDS_API_REGION."""
    s = _make_base()
    assert s._get_odds_region() == "eu"


def test_region_override_wins_over_config():
    """Subclass can pin its own region via class attribute."""
    class Pinned(SportsTraderStrategy):
        REGION_OVERRIDE = "us"
    config = MagicMock()
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.ODDS_API_KEY = ""
    config.ODDS_API_REGION = "eu"
    s = Pinned(config, MagicMock(), MagicMock())
    assert s._get_odds_region() == "us"


def test_explicit_none_falls_back_to_config():
    """REGION_OVERRIDE = None is the same as not setting it."""
    class Explicit(SportsTraderStrategy):
        REGION_OVERRIDE = None
    config = MagicMock()
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.ODDS_API_KEY = ""
    config.ODDS_API_REGION = "uk"
    s = Explicit(config, MagicMock(), MagicMock())
    assert s._get_odds_region() == "uk"
