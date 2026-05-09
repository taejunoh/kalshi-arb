from unittest.mock import MagicMock

import pytest

from strategies.ladder_fed import LadderFedStrategy


def _cfg():
    return MagicMock(
        LADDER_MIN_ARB_EDGE_USDC=0.02, LADDER_MIN_FADE_INVERSION=0.05,
        LADDER_MIN_DEPTH_USDC=30.0, LADDER_MAX_POSITION_PER_GROUP=50.0,
        KALSHI_FEE_PER_CONTRACT=0.07,
    )


def test_parse_c_h_suffix():
    s = LadderFedStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    assert s._parse_suffix("C25") == -25.0
    assert s._parse_suffix("C50") == -50.0
    assert s._parse_suffix("H0") == 0.0
    assert s._parse_suffix("H25") == 25.0
    assert s._parse_suffix("BAD") is None


def test_event_key_is_fomc_meeting_date():
    s = LadderFedStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    # KXFEDDECISION-26JUN-C25 → 26JUN
    assert s._extract_event_key({"ticker": "KXFEDDECISION-26JUN-C25"}) == "26JUN"


def test_universe_filter():
    s = LadderFedStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    assert s._is_target_market({"ticker": "KXFEDDECISION-26JUN-C25"}) is True
    assert s._is_target_market({"ticker": "KXFEDMENTION-26APR-YIEL"}) is False
    assert s._is_target_market({"ticker": "KXBTC-26MAY09-1500-B70250"}) is False
