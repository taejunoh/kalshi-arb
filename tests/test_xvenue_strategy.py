from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, AsyncMock

import pytest

from strategies.xvenue_polymarket import XVenuePolymarketStrategy


def _cfg():
    return MagicMock(
        XVENUE_MIN_DEVIATION=0.10,
        XVENUE_MIN_DEPTH_USDC=30.0,
        XVENUE_BASE_SIZE_USDC=20.0,
        XVENUE_MAX_POSITION_PER_TICKER=30.0,
        XVENUE_MAX_TICKER_TOTAL_EXPOSURE=80.0,
        XVENUE_QUOTE_STALE_MINS=15.0,
        XVENUE_MIN_POLYMARKET_VOLUME=5000.0,
        XVENUE_RECENTLY_FIRED_SECS=300.0,
        KALSHI_FEE_PER_CONTRACT=0.07,
    )


def test_kalshi_markets_for_prefix_filters_correctly():
    scanner = MagicMock()
    scanner.get_all_markets = MagicMock(return_value=[
        {"ticker": "KXPRES-2028-T_DEM"},
        {"ticker": "KXPRES-2028-T_GOP"},
        {"ticker": "KXFEDDECISION-26JUN-C25"},
        {"ticker": "KXBTC-26MAY1017-T89749.99"},
    ])
    mapping = MagicMock()
    s = XVenuePolymarketStrategy(_cfg(), MagicMock(), scanner, MagicMock(),
                                  mapping=mapping)
    out = s._kalshi_markets_for_prefix("KXPRES-2028-")
    tickers = sorted(m["ticker"] for m in out)
    assert tickers == ["KXPRES-2028-T_DEM", "KXPRES-2028-T_GOP"]


def test_kalshi_markets_for_prefix_returns_empty_when_no_match():
    scanner = MagicMock()
    scanner.get_all_markets = MagicMock(return_value=[
        {"ticker": "KXBTC-X-T1"},
    ])
    s = XVenuePolymarketStrategy(_cfg(), MagicMock(), scanner, MagicMock(),
                                  mapping=MagicMock())
    assert s._kalshi_markets_for_prefix("KXSOMETHING-") == []


def test_apply_outcome_match_direct():
    s = XVenuePolymarketStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                  mapping=MagicMock())
    assert s._apply_outcome_match(0.42, "direct") == 0.42


def test_apply_outcome_match_reversed():
    s = XVenuePolymarketStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                  mapping=MagicMock())
    # Trump=YES at 0.42 on Polymarket -> Dem-side semantics = 1 - 0.42 = 0.58
    assert abs(s._apply_outcome_match(0.42, "reversed") - 0.58) < 1e-6


def test_is_quote_fresh_when_recent():
    s = XVenuePolymarketStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                  mapping=MagicMock())
    now = datetime.now(timezone.utc)
    quote_row = {"fetched_at": (now - timedelta(minutes=5)).isoformat()}
    assert s._is_quote_fresh(quote_row, now=now) is True


def test_is_quote_fresh_rejects_stale():
    s = XVenuePolymarketStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                  mapping=MagicMock())
    now = datetime.now(timezone.utc)
    quote_row = {"fetched_at": (now - timedelta(minutes=30)).isoformat()}
    assert s._is_quote_fresh(quote_row, now=now) is False


def test_is_quote_volume_ok():
    s = XVenuePolymarketStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                  mapping=MagicMock())
    assert s._is_quote_volume_ok({"volume_24h": 10000.0}) is True
    assert s._is_quote_volume_ok({"volume_24h": 1000.0}) is False
    assert s._is_quote_volume_ok({"volume_24h": None}) is False
