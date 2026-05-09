from unittest.mock import MagicMock

import pytest

from strategies.ladder_approval import LadderApprovalStrategy


def _cfg():
    return MagicMock(
        LADDER_MIN_ARB_EDGE_USDC=0.02, LADDER_MIN_FADE_INVERSION=0.05,
        LADDER_MIN_DEPTH_USDC=20.0, LADDER_MAX_POSITION_PER_GROUP=50.0,
        KALSHI_FEE_PER_CONTRACT=0.07,
    )


def test_parse_integer_suffix():
    s = LadderApprovalStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    assert s._parse_suffix("47") == 47.0
    assert s._parse_suffix("3") == 3.0
    assert s._parse_suffix("BELOW") is None


def test_direction_for_below_series():
    s = LadderApprovalStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    assert s._direction_for("KXTRUMPAPPROVALYEAR") == "asc"
    assert s._direction_for("KXTRUMPAPPROVALBELOW") == "desc"
    assert s._direction_for("KX538APPROVE") == "asc"
    assert s._direction_for("UNKNOWN") == "asc"  # default


def test_universe_filter():
    s = LadderApprovalStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    assert s._is_target_market({"ticker": "KXTRUMPAPPROVALYEAR-26-47"}) is True
    assert s._is_target_market({"ticker": "KXTRUMPAPPROVALBELOW-26MAY-37"}) is True
    assert s._is_target_market({"ticker": "KX538APPROVE-26W19-47"}) is True
    assert s._is_target_market({"ticker": "KXBTC-26MAY09-1500-B70250"}) is False
