from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, AsyncMock

import pytest

from strategies.crypto_fair_value import CryptoFairValueStrategy


def _cfg():
    return MagicMock(
        CRYPTO_FAIR_MIN_DEVIATION=0.10,
        CRYPTO_FAIR_MIN_DEPTH_USDC=30.0,
        CRYPTO_FAIR_BASE_SIZE_USDC=20.0,
        CRYPTO_FAIR_MAX_POSITION_PER_TICKER=30.0,
        CRYPTO_FAIR_MAX_TICKER_TOTAL_EXPOSURE=80.0,
        CRYPTO_FAIR_RECENTLY_FIRED_SECS=60.0,
        CRYPTO_FAIR_ATM_WINDOW_N=5,
        CRYPTO_FAIR_MIN_TIME_TO_CLOSE_SECS=60.0,
        KALSHI_FEE_PER_CONTRACT=0.07,
    )


def test_universe_filter():
    s = CryptoFairValueStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                 price_feed=MagicMock())
    assert s._is_target_market({"ticker": "KXBTC-26MAY1017-T89749.99"}) is True
    assert s._is_target_market({"ticker": "KXETH-26MAY1017-T3059.99"}) is True
    assert s._is_target_market({"ticker": "KXXRP-26MAY1017-T1.7799"}) is True
    assert s._is_target_market({"ticker": "KXBTCD-26MAY1017-T89749.99"}) is True
    assert s._is_target_market({"ticker": "KXETHD-26MAY1017-T3059.99"}) is True
    assert s._is_target_market({"ticker": "KXSOL-26MAY1017-T100"}) is False
    assert s._is_target_market({"ticker": "KXFEDDECISION-26JUN-C25"}) is False


def test_extract_strike():
    s = CryptoFairValueStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                 price_feed=MagicMock())
    assert s._extract_strike({"ticker": "KXBTC-26MAY1017-T89749.99"}) == 89749.99
    assert s._extract_strike({"ticker": "KXETH-26MAY1017-T3059.99"}) == 3059.99
    assert s._extract_strike({"ticker": "KXBTC-26MAY1017-INVALID"}) is None


def test_extract_asset():
    s = CryptoFairValueStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                 price_feed=MagicMock())
    assert s._extract_asset({"ticker": "KXBTC-26MAY1017-T89749.99"}) == "BTC"
    assert s._extract_asset({"ticker": "KXETH-26MAY1017-T3059.99"}) == "ETH"
    assert s._extract_asset({"ticker": "KXXRP-26MAY1017-T1.7799"}) == "XRP"
    assert s._extract_asset({"ticker": "KXBTCD-26MAY1017-T89749.99"}) == "BTC"  # daily
    assert s._extract_asset({"ticker": "KXETHD-26MAY1017-T3059.99"}) == "ETH"


def test_atm_window_filter():
    """Filter ATM ±N strikes based on current spot."""
    s = CryptoFairValueStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                 price_feed=MagicMock())
    markets = [
        {"ticker": f"KXBTC-26MAY1017-T{strike}"}
        for strike in (89000.0, 89500.0, 89750.0, 90000.0, 90250.0, 90500.0, 91000.0)
    ]
    # Spot = 90000, N=2 → strikes 89500, 89750, 90000, 90250, 90500
    filtered = s._filter_atm(markets, spot=90000.0, asset="BTC", n=2)
    keep = sorted(s._extract_strike(m) for m in filtered)
    assert keep == [89500.0, 89750.0, 90000.0, 90250.0, 90500.0]


def test_time_to_close_secs():
    s = CryptoFairValueStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                 price_feed=MagicMock())
    now = datetime.now(timezone.utc)
    market = {
        "ticker": "KXBTC-26MAY1017-T89749.99",
        "close_time": (now + timedelta(seconds=3600)).isoformat(),
    }
    secs = s._time_to_close_secs(market, now=now)
    assert 3590 <= secs <= 3610  # ±10s tolerance


def test_time_to_close_returns_negative_when_past():
    s = CryptoFairValueStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                 price_feed=MagicMock())
    now = datetime.now(timezone.utc)
    market = {
        "ticker": "KXBTC-X-T1",
        "close_time": (now - timedelta(seconds=3600)).isoformat(),
    }
    assert s._time_to_close_secs(market, now=now) < 0
