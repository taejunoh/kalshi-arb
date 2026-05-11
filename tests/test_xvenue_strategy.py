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


def test_compute_edge_buy_yes_when_polymarket_above_kalshi():
    s = XVenuePolymarketStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                  mapping=MagicMock())
    # Polymarket says 0.50, Kalshi mid 0.30 -> buy Kalshi YES
    edge = s._compute_edge(
        polymarket_yes=0.50,
        kalshi_yes_ask=0.32, kalshi_yes_bid=0.28,
        kalshi_no_ask=0.72, kalshi_no_bid=0.68,
    )
    assert edge is not None
    assert edge["side"] == "yes"
    assert abs(edge["deviation"] - 0.20) < 1e-6  # 0.50 - 0.30 mid
    assert edge["entry_price"] == 0.32


def test_compute_edge_buy_no_when_polymarket_below_kalshi():
    s = XVenuePolymarketStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                  mapping=MagicMock())
    edge = s._compute_edge(
        polymarket_yes=0.30,
        kalshi_yes_ask=0.62, kalshi_yes_bid=0.58,
        kalshi_no_ask=0.42, kalshi_no_bid=0.38,
    )
    assert edge is not None
    assert edge["side"] == "no"
    assert edge["deviation"] < 0
    assert edge["entry_price"] == 0.42  # no_ask


def test_compute_edge_skips_below_min_deviation():
    s = XVenuePolymarketStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                  mapping=MagicMock())
    edge = s._compute_edge(
        polymarket_yes=0.32,  # Kalshi mid = 0.30, dev = 0.02 < 0.10
        kalshi_yes_ask=0.32, kalshi_yes_bid=0.28,
        kalshi_no_ask=0.72, kalshi_no_bid=0.68,
    )
    assert edge is None


def test_compute_edge_skips_when_ev_negative():
    cfg = _cfg()
    cfg.XVENUE_MIN_DEVIATION = 0.05  # lower threshold so dev passes
    s = XVenuePolymarketStrategy(cfg, MagicMock(), MagicMock(), MagicMock(),
                                  mapping=MagicMock())
    # Mid 0.20, ask 0.25, polymarket 0.27 -> dev 0.07 >= 0.05 but
    # EV = 0.27 - 0.25 - 0.07 = -0.05 -> skip
    edge = s._compute_edge(
        polymarket_yes=0.27,
        kalshi_yes_ask=0.25, kalshi_yes_bid=0.15,
        kalshi_no_ask=0.85, kalshi_no_bid=0.75,
    )
    assert edge is None


def test_size_for_edge_scales_and_caps():
    s = XVenuePolymarketStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                  mapping=MagicMock())
    # base 20, clamp(0.10/0.10, 0.5, 3.0) = 1.0 -> 20
    assert s._size_for_edge(0.10) == 20.0
    # base x clamp(0.30/0.10, 0.5, 3.0) = 3.0 -> 60, capped at 30
    assert s._size_for_edge(0.30) == 30.0
    # base x clamp(0.05/0.10, 0.5, 3.0) = 0.5 -> 10
    assert s._size_for_edge(0.05) == 10.0
