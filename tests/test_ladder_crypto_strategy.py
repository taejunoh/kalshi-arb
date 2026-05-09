from unittest.mock import MagicMock

import pytest

from strategies.ladder_crypto import LadderCryptoStrategy


def _cfg():
    return MagicMock(
        LADDER_MIN_ARB_EDGE_USDC=0.02, LADDER_MIN_FADE_INVERSION=0.05,
        LADDER_MIN_DEPTH_USDC=30.0, LADDER_MAX_POSITION_PER_GROUP=50.0,
        LADDER_STALE_ORDERBOOK_SEC=10.0,
        KALSHI_FEE_PER_CONTRACT=0.07,
    )


def test_parse_b_suffix():
    s = LadderCryptoStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    assert s._parse_suffix("B70250") == 70250.0
    assert s._parse_suffix("B1310") == 1310.0
    assert s._parse_suffix("BAD") is None
    assert s._parse_suffix("") is None


def test_event_key_includes_close_window():
    s = LadderCryptoStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    # KXBTC-26MAY09-1500-B70250 → 26MAY09-1500
    assert s._extract_event_key(
        {"ticker": "KXBTC-26MAY09-1500-B70250"}
    ) == "26MAY09-1500"


def test_atm_window_filter_keeps_only_within_n():
    """Leg whose threshold is within ATM ± window survives; far OTM/ITM dropped."""
    s = LadderCryptoStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    legs = [
        {"ticker": f"KXBTC-26MAY09-1500-B{p}",
         "yes_ask": 0.5, "yes_bid": 0.48, "no_ask": 0.52, "no_bid": 0.50,
         "yes_depth": 100.0, "no_depth": 100.0}
        for p in (70000, 70100, 70200, 70300, 70400, 70500, 70600)
    ]
    # ATM = 70300. Window = ±2 buckets ($100 each) → keep 70100..70500
    filtered = s._atm_window_filter(legs, atm_price=70300.0, window_n=2,
                                     bucket_size=100.0)
    keep = sorted(int(m["ticker"].rsplit("-B", 1)[-1]) for m in filtered)
    assert keep == [70100, 70200, 70300, 70400, 70500]


def test_universe_filter():
    s = LadderCryptoStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock())
    assert s._is_target_market({"ticker": "KXBTC-26MAY09-1500-B70250"}) is True
    assert s._is_target_market({"ticker": "KXETH-26MAY09-1500-B1310"}) is True
    assert s._is_target_market({"ticker": "KXSOL-26MAY09-1500-B100"}) is False
