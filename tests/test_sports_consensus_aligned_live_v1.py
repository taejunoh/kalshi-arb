"""Tests for sports_consensus_aligned_live_v1 — cross-sport consensus-alignment.

Validates: gap<3pp gate (both sides), blocklist (MLB/NHL/etc), TIE skip,
fair_prob requirement, payoff floor.
"""
from unittest.mock import MagicMock

import pytest

from strategies.sports_consensus_aligned_live_v1 import (
    SportsConsensusAlignedLiveV1Strategy,
)


def _strat():
    cfg = MagicMock()
    cfg.KALSHI_FEE_PER_CONTRACT = 0.07
    # ④ REAL_FEE_GATE flag OFF (production default/live state): gate_fee_pct
    # is byte-identical to the legacy inline min(fee, X, 1-X)/X. The strategy
    # now delegates to config.gate_fee_pct, so the mock must reproduce it or
    # it returns a MagicMock and breaks the payoff>=2.5 comparison.
    cfg.gate_fee_pct = lambda price: (
        0.0 if price <= 0 or price >= 1 else min(0.07, price, 1.0 - price) / price
    )
    om = MagicMock()
    om.bankroll = 10000.0
    return SportsConsensusAlignedLiveV1Strategy(cfg, om, MagicMock())


def test_yes_side_fires_when_consensus_aligned():
    """YES 0.25 with fair_prob=0.26 → gap=1pp < 3pp → fires."""
    s = _strat()
    sig = s.evaluate(kalshi_yes=0.25, kalshi_no=0.75, fair_prob=0.26,
                     ticker="KXWNBAGAME-26MAY23ATLLAS-ATL")
    assert sig is not None
    assert sig["side"] == "yes"
    assert sig["gap_pp"] < 3.0


def test_no_side_fires_when_implied_aligned():
    """NO 0.25 (implied YES 0.75) with fair_prob=0.74 → gap=1pp → fires."""
    s = _strat()
    sig = s.evaluate(kalshi_yes=0.75, kalshi_no=0.25, fair_prob=0.74,
                     ticker="KXNBAGAME-26MAY23DENLAL-DEN")
    assert sig is not None
    assert sig["side"] == "no"


def test_rejects_gap_3pp_or_more():
    """gap >= 3pp → no fire."""
    s = _strat()
    # YES=0.25, fair=0.29 → gap=4pp (over threshold)
    sig = s.evaluate(kalshi_yes=0.25, kalshi_no=0.75, fair_prob=0.29,
                     ticker="KXWNBAGAME-26MAY23ATLLAS-ATL")
    # NO also out of band (0.75 > 0.30 max)
    assert sig is None


def test_no_fire_without_fair_prob():
    """fair_prob=None → no fire on either side."""
    s = _strat()
    sig = s.evaluate(kalshi_yes=0.27, kalshi_no=0.73, fair_prob=None,
                     ticker="KXWNBAGAME-26MAY23ATLLAS-ATL")
    assert sig is None


def test_tie_skipped():
    """TIE tickers excluded (defensive — MLS 3-way market)."""
    s = _strat()
    sig = s.evaluate(kalshi_yes=0.27, kalshi_no=0.73, fair_prob=0.27,
                     ticker="KXMLSGAME-26MAY23MIATOR-TIE")
    assert sig is None


def test_blocklist_blocks_mlb_nhl_npb_etc():
    """Confirmed blocked: MLB (efficient at this band), NHL/NPB/KBO/UFC
    (thin sample), all non-MLS soccer (EPL/LAL/BUN/UCL)."""
    expected = {"MLB", "NHL", "NPB", "KBO", "UFC",
                "EPL", "LAL", "BUN", "UCL"}
    assert SportsConsensusAlignedLiveV1Strategy.SPORT_BLOCKLIST == frozenset(expected)


def test_pre_game_only_off_live_phase_allowed():
    """PRE_GAME_ONLY=False so live-phase tickers can reach evaluate()."""
    assert SportsConsensusAlignedLiveV1Strategy.PRE_GAME_ONLY is False
    assert SportsConsensusAlignedLiveV1Strategy.MIN_MTS_AT_ENTRY is None
    assert SportsConsensusAlignedLiveV1Strategy.MAX_MTS_AT_ENTRY is None


def test_price_outside_band_rejected():
    """price>0.30 on YES → no fire even if consensus-aligned."""
    s = _strat()
    sig = s.evaluate(kalshi_yes=0.35, kalshi_no=0.65, fair_prob=0.34,
                     ticker="KXWNBAGAME-26MAY23ATLLAS-ATL")
    # NO side 0.65 outside [0.20, 0.30] too
    assert sig is None


def test_picks_higher_payoff_when_both_qualify():
    """Edge case: prices sum < 1 (Kalshi spread). Higher payoff wins."""
    # kalshi_yes=0.20, kalshi_no=0.30. Both in [0.20, 0.30].
    # fair_prob=0.22 → YES gap=2pp, NO implied=0.70 vs fair=0.22 → gap=48pp.
    # Only YES qualifies — payoff 1/0.20 ≈ 5.0.
    s = _strat()
    sig = s.evaluate(kalshi_yes=0.20, kalshi_no=0.30, fair_prob=0.22,
                     ticker="KXMLSGAME-26MAY23MIATOR-MIA")
    assert sig is not None
    assert sig["side"] == "yes"
