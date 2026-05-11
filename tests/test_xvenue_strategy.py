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


import time


def _kalshi_book_for_ticker(yes_ask, no_ask, depth=100.0):
    """Mock get_market_prices + get_order_book_depth pair."""
    return (yes_ask, no_ask), (depth, depth)


@pytest.mark.asyncio
async def test_scan_fires_buy_yes_when_all_gates_pass():
    """Polymarket 0.50 > Kalshi mid 0.30 -> BUY YES at 0.32."""
    cfg = _cfg()
    now = datetime.now(timezone.utc)
    db = MagicMock()
    db.fetch_polymarket_quote = AsyncMock(return_value={
        "slug": "test", "yes_price": 0.50, "no_price": 0.50,
        "volume_24h": 50000.0,
        "fetched_at": now.isoformat(),
    })
    db.total_open_exposure = AsyncMock(return_value=0.0)

    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock(return_value={"status": "paper_filled"})
    om._db = db

    client = MagicMock()
    client.get_market_prices = MagicMock(return_value=(0.32, 0.68))
    client.get_order_book_depth = MagicMock(return_value=(100.0, 100.0))

    scanner = MagicMock()
    scanner.get_all_markets = MagicMock(return_value=[
        {"ticker": "KXTEST-A"},
    ])

    mapping = MagicMock()
    mapping.all_slugs = MagicMock(return_value=["test"])
    mapping.entries_for_slug = MagicMock(return_value=[{
        "kalshi_ticker_prefix": "KXTEST-",
        "outcome_match": "direct",
        "notes": "",
    }])
    mapping.reload = MagicMock(return_value=None)

    s = XVenuePolymarketStrategy(cfg, om, scanner, client, mapping=mapping)
    await s.scan_and_execute()

    om.market_order.assert_called_once()
    call_kwargs = om.market_order.call_args.kwargs
    assert call_kwargs["ticker"] == "KXTEST-A"
    assert call_kwargs["side"] == "yes"
    assert call_kwargs["strategy"] == "xvenue_polymarket"


@pytest.mark.asyncio
async def test_scan_skips_when_polymarket_quote_stale():
    cfg = _cfg()
    now = datetime.now(timezone.utc)
    db = MagicMock()
    db.fetch_polymarket_quote = AsyncMock(return_value={
        "slug": "test", "yes_price": 0.50, "no_price": 0.50,
        "volume_24h": 50000.0,
        "fetched_at": (now - timedelta(minutes=30)).isoformat(),  # > 15 min
    })
    db.total_open_exposure = AsyncMock(return_value=0.0)
    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock()
    om._db = db
    client = MagicMock()
    client.get_market_prices = MagicMock(return_value=(0.32, 0.68))
    client.get_order_book_depth = MagicMock(return_value=(100.0, 100.0))
    scanner = MagicMock()
    scanner.get_all_markets = MagicMock(return_value=[{"ticker": "KXTEST-A"}])
    mapping = MagicMock()
    mapping.all_slugs = MagicMock(return_value=["test"])
    mapping.entries_for_slug = MagicMock(return_value=[{
        "kalshi_ticker_prefix": "KXTEST-",
        "outcome_match": "direct", "notes": "",
    }])
    mapping.reload = MagicMock(return_value=None)
    s = XVenuePolymarketStrategy(cfg, om, scanner, client, mapping=mapping)
    await s.scan_and_execute()
    om.market_order.assert_not_called()


@pytest.mark.asyncio
async def test_scan_skips_when_polymarket_volume_too_low():
    cfg = _cfg()
    now = datetime.now(timezone.utc)
    db = MagicMock()
    db.fetch_polymarket_quote = AsyncMock(return_value={
        "slug": "test", "yes_price": 0.50, "no_price": 0.50,
        "volume_24h": 1000.0,  # < 5000 floor
        "fetched_at": now.isoformat(),
    })
    db.total_open_exposure = AsyncMock(return_value=0.0)
    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock()
    om._db = db
    client = MagicMock()
    client.get_market_prices = MagicMock(return_value=(0.32, 0.68))
    client.get_order_book_depth = MagicMock(return_value=(100.0, 100.0))
    scanner = MagicMock()
    scanner.get_all_markets = MagicMock(return_value=[{"ticker": "KXTEST-A"}])
    mapping = MagicMock()
    mapping.all_slugs = MagicMock(return_value=["test"])
    mapping.entries_for_slug = MagicMock(return_value=[{
        "kalshi_ticker_prefix": "KXTEST-",
        "outcome_match": "direct", "notes": "",
    }])
    mapping.reload = MagicMock(return_value=None)
    s = XVenuePolymarketStrategy(cfg, om, scanner, client, mapping=mapping)
    await s.scan_and_execute()
    om.market_order.assert_not_called()


@pytest.mark.asyncio
async def test_scan_skips_when_recently_fired():
    cfg = _cfg()
    now = datetime.now(timezone.utc)
    db = MagicMock()
    db.fetch_polymarket_quote = AsyncMock(return_value={
        "slug": "test", "yes_price": 0.50, "no_price": 0.50,
        "volume_24h": 50000.0,
        "fetched_at": now.isoformat(),
    })
    db.total_open_exposure = AsyncMock(return_value=0.0)
    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock()
    om._db = db
    client = MagicMock()
    client.get_market_prices = MagicMock(return_value=(0.32, 0.68))
    client.get_order_book_depth = MagicMock(return_value=(100.0, 100.0))
    scanner = MagicMock()
    scanner.get_all_markets = MagicMock(return_value=[{"ticker": "KXTEST-A"}])
    mapping = MagicMock()
    mapping.all_slugs = MagicMock(return_value=["test"])
    mapping.entries_for_slug = MagicMock(return_value=[{
        "kalshi_ticker_prefix": "KXTEST-",
        "outcome_match": "direct", "notes": "",
    }])
    mapping.reload = MagicMock(return_value=None)
    s = XVenuePolymarketStrategy(cfg, om, scanner, client, mapping=mapping)
    s._recently_fired["KXTEST-A"] = time.time() - 60.0  # fired 60s ago, cap=300s
    await s.scan_and_execute()
    om.market_order.assert_not_called()


@pytest.mark.asyncio
async def test_scan_applies_reversed_outcome_match():
    """Polymarket Trump=YES 0.42 with reversed match -> Kalshi Dem-side
    interpretation = 1 - 0.42 = 0.58.  If Kalshi DEM mid is 0.30, dev = 0.28
    -> BUY YES on KX-DEM."""
    cfg = _cfg()
    now = datetime.now(timezone.utc)
    db = MagicMock()
    db.fetch_polymarket_quote = AsyncMock(return_value={
        "slug": "trump-win", "yes_price": 0.42, "no_price": 0.58,
        "volume_24h": 50000.0,
        "fetched_at": now.isoformat(),
    })
    db.total_open_exposure = AsyncMock(return_value=0.0)
    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock(return_value={"status": "paper_filled"})
    om._db = db
    client = MagicMock()
    client.get_market_prices = MagicMock(return_value=(0.32, 0.68))
    client.get_order_book_depth = MagicMock(return_value=(100.0, 100.0))
    scanner = MagicMock()
    scanner.get_all_markets = MagicMock(return_value=[
        {"ticker": "KXPRES-2028-DEM"},
    ])
    mapping = MagicMock()
    mapping.all_slugs = MagicMock(return_value=["trump-win"])
    mapping.entries_for_slug = MagicMock(return_value=[{
        "kalshi_ticker_prefix": "KXPRES-2028-DEM",
        "outcome_match": "reversed",  # Trump=YES on Poly -> Dem=YES on Kalshi flipped
        "notes": "",
    }])
    mapping.reload = MagicMock(return_value=None)
    s = XVenuePolymarketStrategy(cfg, om, scanner, client, mapping=mapping)
    await s.scan_and_execute()
    om.market_order.assert_called_once()
    assert om.market_order.call_args.kwargs["side"] == "yes"
