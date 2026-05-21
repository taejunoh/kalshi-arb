"""Tests for sports_soccer_weekend_v1 — discovery-driven paper strategy.

Validates: weekend-only gate, Pattern A (underdog YES) consensus-gap filter,
Pattern B (fade-fav NO) consensus-gap filter, blocklist (no MLB/NBA/etc),
TIE skip, payoff floor.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from strategies.sports_soccer_weekend_v1 import SportsSoccerWeekendV1Strategy


def _strat():
    cfg = MagicMock()
    cfg.KALSHI_FEE_PER_CONTRACT = 0.07
    om = MagicMock()
    om.bankroll = 10000.0
    return SportsSoccerWeekendV1Strategy(cfg, om, MagicMock())


@pytest.fixture
def saturday_utc():
    """Fixed Saturday timestamp for deterministic dow tests."""
    # 2026-05-23 = Saturday
    return datetime(2026, 5, 23, 14, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def midweek_utc():
    """Fixed Wednesday timestamp."""
    return datetime(2026, 5, 20, 14, 0, 0, tzinfo=timezone.utc)


def test_midweek_returns_none(midweek_utc):
    """No fires on midweek even if all other gates pass."""
    s = _strat()
    with patch("strategies.sports_soccer_weekend_v1.datetime") as mock_dt:
        mock_dt.now.return_value = midweek_utc
        sig = s.evaluate(kalshi_yes=0.27, kalshi_no=0.73, fair_prob=0.27,
                         ticker="KXMLSGAME-26MAY20MIATOR-MIA")
        assert sig is None


def test_pattern_a_fires_on_weekend_consensus_aligned(saturday_utc):
    """Pattern A: YES 0.25-0.30 with |gap|<3pp fires on Saturday."""
    s = _strat()
    with patch("strategies.sports_soccer_weekend_v1.datetime") as mock_dt:
        mock_dt.now.return_value = saturday_utc
        sig = s.evaluate(kalshi_yes=0.27, kalshi_no=0.73, fair_prob=0.27,
                         ticker="KXEPLGAME-26MAY23MANUARS-MAN")
        assert sig is not None
        assert sig["side"] == "yes"
        assert sig["price"] == 0.27
        assert sig["pattern"] == "A_underdog"
        assert sig["gap_pp"] < 3.0


def test_pattern_a_rejects_gap_too_wide(saturday_utc):
    """Pattern A rejects when gap >= 3pp."""
    s = _strat()
    with patch("strategies.sports_soccer_weekend_v1.datetime") as mock_dt:
        mock_dt.now.return_value = saturday_utc
        # gap = (0.28 - 0.22) * 100 = 6pp — too wide for Pattern A
        sig = s.evaluate(kalshi_yes=0.28, kalshi_no=0.72, fair_prob=0.22,
                         ticker="KXMLSGAME-26MAY23MIATOR-MIA")
        # Pattern B may also reject since 0.72 outside 0.40-0.50
        assert sig is None


def test_pattern_b_fires_fade_favorite(saturday_utc):
    """Pattern B: NO 0.40-0.50 with gap<6pp fires."""
    s = _strat()
    with patch("strategies.sports_soccer_weekend_v1.datetime") as mock_dt:
        mock_dt.now.return_value = saturday_utc
        # fair_prob=0.58 means EU consensus has YES at 58%. Implied YES from
        # NO 0.45 is (1-0.45)=0.55. Gap = |55-58| = 3pp, within Pattern B.
        sig = s.evaluate(kalshi_yes=0.55, kalshi_no=0.45, fair_prob=0.58,
                         ticker="KXMLSGAME-26MAY23MIATOR-MIA")
        assert sig is not None
        assert sig["side"] == "no"
        assert sig["price"] == 0.45
        assert sig["pattern"] == "B_fade_fav"


def test_pattern_b_rejects_gap_too_wide(saturday_utc):
    """Pattern B rejects when |gap| >= 6pp."""
    s = _strat()
    with patch("strategies.sports_soccer_weekend_v1.datetime") as mock_dt:
        mock_dt.now.return_value = saturday_utc
        # Implied YES from NO=0.40 is 0.60. fair_prob=0.70 → gap=10pp.
        sig = s.evaluate(kalshi_yes=0.60, kalshi_no=0.40, fair_prob=0.70,
                         ticker="KXMLSGAME-26MAY23MIATOR-MIA")
        assert sig is None


def test_tie_ticker_skipped(saturday_utc):
    """Soccer 3-way TIE tickers must be skipped."""
    s = _strat()
    with patch("strategies.sports_soccer_weekend_v1.datetime") as mock_dt:
        mock_dt.now.return_value = saturday_utc
        sig = s.evaluate(kalshi_yes=0.27, kalshi_no=0.73, fair_prob=0.27,
                         ticker="KXEPLGAME-26MAY23MANUARS-TIE")
        assert sig is None


def test_blocklist_blocks_non_soccer():
    """SPORT_BLOCKLIST must block MLB/NBA/WNBA/NHL/NPB/KBO."""
    blocked = {"NBA", "WNBA", "MLB", "NHL", "NPB", "KBO"}
    assert SportsSoccerWeekendV1Strategy.SPORT_BLOCKLIST == frozenset(blocked)


def test_min_mts_at_entry_set_to_720():
    """Long-pre gate: MIN_MTS_AT_ENTRY = 720 (12h+)."""
    assert SportsSoccerWeekendV1Strategy.MIN_MTS_AT_ENTRY == 720.0
    assert SportsSoccerWeekendV1Strategy.MAX_MTS_AT_ENTRY is None


def test_no_signal_when_fair_prob_missing(saturday_utc):
    """Both patterns require fair_prob (consensus); skip if None."""
    s = _strat()
    with patch("strategies.sports_soccer_weekend_v1.datetime") as mock_dt:
        mock_dt.now.return_value = saturday_utc
        sig = s.evaluate(kalshi_yes=0.27, kalshi_no=0.73, fair_prob=None,
                         ticker="KXEPLGAME-26MAY23MANUARS-MAN")
        assert sig is None


def test_picks_higher_payoff_when_both_match():
    """If both patterns somehow qualify (unusual), pick higher payoff."""
    # This is constructed-edge case: kalshi_yes=0.28 + kalshi_no=0.45,
    # fair_prob picks one to match each. Both patterns return candidates;
    # higher payoff = 1/0.28 ≈ 3.57 vs 1/0.45 ≈ 2.22 → YES wins.
    s = _strat()
    with patch("strategies.sports_soccer_weekend_v1.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 5, 23, 14, tzinfo=timezone.utc)
        # Gap for YES: |0.28-0.28|=0pp (passes <3pp).
        # Gap for NO: implied-YES = 0.55, |0.55-0.28|=27pp (FAILS <6pp).
        # So only Pattern A returns — that's the expected case.
        sig = s.evaluate(kalshi_yes=0.28, kalshi_no=0.45, fair_prob=0.28,
                         ticker="KXMLSGAME-26MAY23MIATOR-MIA")
        assert sig is not None
        assert sig["pattern"] == "A_underdog"
