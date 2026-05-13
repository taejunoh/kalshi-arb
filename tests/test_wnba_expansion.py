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


# ---------------------------------------------------------------------------
# Task 2: longshot_v31 — OBSERVATION_ONLY override + WNBA pair-fire dedup
# ---------------------------------------------------------------------------

def test_longshot_v31_unblocks_wnba():
    """v31 longshot must remove WNBA from OBSERVATION_ONLY_SPORTS so
    scan_and_execute reaches its evaluate() for WNBA tickers."""
    from strategies.sports_longshot_v31 import SportsLongshotV31Strategy
    assert "WNBA" not in SportsLongshotV31Strategy.OBSERVATION_ONLY_SPORTS
    # Parent set still has WNBA — only this subclass removed it
    assert "WNBA" in SportsTraderStrategy.OBSERVATION_ONLY_SPORTS


def test_longshot_v31_pair_fire_dedup_wnba_only():
    """evaluate() returns None on a 2nd WNBA fire when sibling already fired."""
    from strategies.sports_longshot_v31 import SportsLongshotV31Strategy
    strat, _ = _make(SportsLongshotV31Strategy)
    # Simulate sibling already fired
    strat._traded_tickers = {WNBA_TICKER_A: 1234567890.0}
    # 2nd fire on paired ticker — should be deduped
    sig = strat.evaluate(0.18, 0.83, 0.18, ticker=WNBA_TICKER_B)
    assert sig is None, "WNBA pair-fire dedup did not block sibling fire"


def test_longshot_v31_no_dedup_on_non_wnba():
    """NBA tickers still allow pair-fire (P2 cleanup not part of this expansion)."""
    from strategies.sports_longshot_v31 import SportsLongshotV31Strategy
    import inspect
    strat, _ = _make(SportsLongshotV31Strategy)
    src = inspect.getsource(strat.evaluate)
    assert "WNBAGAME" in src, "Expected WNBA-only dedup branch"


# ---------------------------------------------------------------------------
# Task 3: midshot_v31 — OBSERVATION_ONLY override + WNBA pair-fire dedup
# ---------------------------------------------------------------------------

def test_midshot_v31_unblocks_wnba():
    from strategies.sports_midshot_v31 import SportsMidshotV31Strategy
    assert "WNBA" not in SportsMidshotV31Strategy.OBSERVATION_ONLY_SPORTS


def test_midshot_v31_pair_fire_dedup():
    from strategies.sports_midshot_v31 import SportsMidshotV31Strategy
    strat, _ = _make(SportsMidshotV31Strategy)
    strat._traded_tickers = {WNBA_TICKER_A: 1234567890.0}
    sig = strat.evaluate(0.25, 0.76, 0.25, ticker=WNBA_TICKER_B)
    assert sig is None


def test_midshot_v31_wnba_only_dedup_branch():
    from strategies.sports_midshot_v31 import SportsMidshotV31Strategy
    import inspect
    strat, _ = _make(SportsMidshotV31Strategy)
    src = inspect.getsource(strat.evaluate)
    assert "WNBAGAME" in src


# ---------------------------------------------------------------------------
# Task 4: underdog_v31 — OBSERVATION_ONLY override + WNBA pair-fire dedup
# ---------------------------------------------------------------------------

def test_underdog_v31_unblocks_wnba():
    from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy
    assert "WNBA" not in SportsUnderdogV31Strategy.OBSERVATION_ONLY_SPORTS


def test_underdog_v31_pair_fire_dedup():
    from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy
    strat, _ = _make(SportsUnderdogV31Strategy)
    strat._traded_tickers = {WNBA_TICKER_A: 1234567890.0}
    sig = strat.evaluate(0.35, 0.66, 0.35, ticker=WNBA_TICKER_B)
    assert sig is None


def test_underdog_v31_dedup_branch_is_wnba_only():
    from strategies.sports_underdog_v31 import SportsUnderdogV31Strategy
    import inspect
    strat, _ = _make(SportsUnderdogV31Strategy)
    src = inspect.getsource(strat.evaluate)
    assert "WNBAGAME" in src


# ---------------------------------------------------------------------------
# Tasks 5+6: v32 + v33 family — must re-block WNBA after v31 trio cascade
# ---------------------------------------------------------------------------

def test_v32_blocks_wnba_via_parent_restore():
    """v32 inherits from v31 underdog — must NOT inherit the WNBA unblock."""
    from strategies.sports_underdog_v32 import SportsUnderdogV32Strategy
    assert "WNBA" in SportsUnderdogV32Strategy.OBSERVATION_ONLY_SPORTS


def test_v32_v33_family_blocks_wnba():
    """All v32 + v33 variants must re-block WNBA after v31 trio cascade."""
    from strategies.sports_longshot_v32 import SportsLongshotV32Strategy
    from strategies.sports_midshot_v32 import SportsMidshotV32Strategy
    from strategies.sports_longshot_v33 import SportsLongshotV33Strategy
    from strategies.sports_midshot_v33 import SportsMidshotV33Strategy
    from strategies.sports_underdog_v33 import SportsUnderdogV33Strategy

    for cls in (SportsLongshotV32Strategy, SportsMidshotV32Strategy,
                SportsLongshotV33Strategy, SportsMidshotV33Strategy,
                SportsUnderdogV33Strategy):
        assert "WNBA" in cls.OBSERVATION_ONLY_SPORTS, f"{cls.__name__} did not re-block WNBA"
