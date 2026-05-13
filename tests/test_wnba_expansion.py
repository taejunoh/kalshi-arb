"""WNBA strategy expansion — verify v31 trio + v36_wnba + v37_wnba fire on
WNBA tickers; v32 + v33 paper + v37_soccer remain blocked; v34/v35 naturally
blocked by ALLOWED_CELLS; pair-fire dedup works.

Built 2026-05-13 alongside the WNBA 5-strategy expansion. See spec
docs/superpowers/specs/2026-05-13-wnba-strategy-expansion-design.md."""
from unittest.mock import MagicMock

import pytest

from strategies.sports_trader import SportsTraderStrategy, SPORT_GAME_TOKENS


# WNBA test tickers — pair of paired sides for one game
WNBA_TICKER_A = "KXWNBAGAME-26MAY14NYWSH-NY"
WNBA_TICKER_B = "KXWNBAGAME-26MAY14NYWSH-WSH"
WNBA_GAME_KEY = "KXWNBAGAME-26MAY14NYWSH"


def _make_config():
    cfg = MagicMock()
    cfg.KALSHI_FEE_PER_CONTRACT = 0.07
    cfg.SPORTS_SCAN_INTERVAL = 300
    cfg.ODDS_API_REGION = "eu"
    cfg.LIVE_STRATEGIES = []
    cfg.is_paper_mode = False
    cfg.INITIAL_BANKROLL = 1000.0
    cfg.ODDS_API_KEY = "test-key"
    return cfg


def _make_order_manager():
    om = MagicMock()
    om.bankroll = 1000.0
    om._notifier = None
    om._db = MagicMock()
    om._quota_last_alert_bucket = None
    return om


def _make_kalshi_client():
    return MagicMock()


def _make(klass):
    """Instantiate a strategy class with mocked deps. Returns (strat, om)."""
    om = _make_order_manager()
    strat = klass(_make_config(), om, _make_kalshi_client())
    return strat, om
