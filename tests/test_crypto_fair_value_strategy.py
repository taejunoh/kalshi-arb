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


@pytest.mark.asyncio
async def test_get_vol_uses_deribit_for_btc_when_fresh():
    cfg = _cfg()
    db = MagicMock()
    db.fetch_nearest_crypto_iv = AsyncMock(return_value={
        "asset": "BTC", "expiry_date": "2026-05-13",
        "atm_iv": 0.65,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    })
    pf = MagicMock()
    pf.get_volatility = MagicMock(return_value=0.80)  # would be used as fallback

    s = CryptoFairValueStrategy(cfg, MagicMock(), MagicMock(), MagicMock(),
                                 price_feed=pf)
    vol, source = await s._get_vol("BTC", db=db)
    assert vol == 0.65
    assert source == "deribit"


@pytest.mark.asyncio
async def test_get_vol_falls_back_to_realized_when_iv_stale():
    cfg = _cfg()
    db = MagicMock()
    stale = datetime.now(timezone.utc) - timedelta(hours=12)  # > 6h IV TTL
    db.fetch_nearest_crypto_iv = AsyncMock(return_value={
        "asset": "BTC", "expiry_date": "2026-05-13",
        "atm_iv": 0.65, "fetched_at": stale.isoformat(),
    })
    pf = MagicMock()
    pf.get_volatility = MagicMock(return_value=0.80)
    s = CryptoFairValueStrategy(cfg, MagicMock(), MagicMock(), MagicMock(),
                                 price_feed=pf)
    vol, source = await s._get_vol("BTC", db=db)
    assert vol == 0.80
    assert source == "realized"


@pytest.mark.asyncio
async def test_get_vol_uses_realized_for_xrp():
    """XRP is not a Deribit asset, always falls back to realized."""
    cfg = _cfg()
    db = MagicMock()
    db.fetch_nearest_crypto_iv = AsyncMock(return_value=None)
    pf = MagicMock()
    pf.get_volatility = MagicMock(return_value=0.95)
    s = CryptoFairValueStrategy(cfg, MagicMock(), MagicMock(), MagicMock(),
                                 price_feed=pf)
    vol, source = await s._get_vol("XRP", db=db)
    assert vol == 0.95
    assert source == "realized"


@pytest.mark.asyncio
async def test_get_vol_returns_none_when_both_missing():
    cfg = _cfg()
    db = MagicMock()
    db.fetch_nearest_crypto_iv = AsyncMock(return_value=None)
    pf = MagicMock()
    pf.get_volatility = MagicMock(return_value=None)
    s = CryptoFairValueStrategy(cfg, MagicMock(), MagicMock(), MagicMock(),
                                 price_feed=pf)
    out = await s._get_vol("BTC", db=db)
    assert out is None


def test_compute_edge_buy_yes_when_fair_above_implied():
    s = CryptoFairValueStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                 price_feed=MagicMock())
    edge = s._compute_edge(
        kalshi_yes_ask=0.30, kalshi_yes_bid=0.28,
        kalshi_no_ask=0.72, kalshi_no_bid=0.70,
        fair_prob=0.50,
    )
    assert edge is not None
    assert edge["side"] == "yes"
    assert abs(edge["deviation"] - 0.21) < 1e-6  # 0.50 - 0.29 (mid)
    assert edge["entry_price"] == 0.30


def test_compute_edge_buy_no_when_fair_below_implied():
    s = CryptoFairValueStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                 price_feed=MagicMock())
    edge = s._compute_edge(
        kalshi_yes_ask=0.70, kalshi_yes_bid=0.68,
        kalshi_no_ask=0.32, kalshi_no_bid=0.30,
        fair_prob=0.45,
    )
    assert edge is not None
    assert edge["side"] == "no"
    assert edge["deviation"] < 0
    assert edge["entry_price"] == 0.32  # no_ask


def test_compute_edge_skips_below_min_deviation():
    s = CryptoFairValueStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                 price_feed=MagicMock())
    edge = s._compute_edge(
        kalshi_yes_ask=0.30, kalshi_yes_bid=0.28,
        kalshi_no_ask=0.72, kalshi_no_bid=0.70,
        fair_prob=0.32,  # mid 0.29, dev 0.03 < 0.10
    )
    assert edge is None


def test_size_for_edge_scales_and_caps():
    s = CryptoFairValueStrategy(_cfg(), MagicMock(), MagicMock(), MagicMock(),
                                 price_feed=MagicMock())
    # base=20, clamp(0.10/0.10, 0.5, 3.0) = 1.0 → 20
    assert s._size_for_edge(0.10) == 20.0
    # base × clamp(0.30/0.10, 0.5, 3.0) = 3.0 → 60, capped at 30
    assert s._size_for_edge(0.30) == 30.0
    # base × clamp(0.05/0.10, 0.5, 3.0) = 0.5 → 10
    assert s._size_for_edge(0.05) == 10.0


import time
from unittest.mock import patch

from core.kalshi_orderbook_cache import OrderbookEntry


def _fresh_entry(yes_ask=0.30, no_ask=0.70, depth=100.0):
    return OrderbookEntry(
        yes_ask=yes_ask, yes_bid=yes_ask, no_ask=no_ask, no_bid=no_ask,
        yes_depth=depth, no_depth=depth, ts=time.time(),
    )


@pytest.mark.asyncio
async def test_handle_market_fires_buy_yes_when_all_gates_pass():
    """Spot 90000, strike 89500 (ITM call), 1h to close, BTC IV 0.50 →
    fair > 0.50; if Kalshi implies 0.30 → BUY YES."""
    cfg = _cfg()
    db = MagicMock()
    db.fetch_nearest_crypto_iv = AsyncMock(return_value={
        "asset": "BTC", "expiry_date": "2026-05-13",
        "atm_iv": 0.50,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    })
    db.total_open_exposure = AsyncMock(return_value=0.0)

    pf = MagicMock()
    pf.get_price = MagicMock(return_value=90000.0)
    pf.is_stale = MagicMock(return_value=False)
    pf.get_volatility = MagicMock(return_value=0.50)

    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock(return_value={"status": "paper_filled"})
    om._db = db

    cache = MagicMock()
    cache.get = MagicMock(return_value=_fresh_entry(yes_ask=0.30, no_ask=0.70, depth=100.0))

    s = CryptoFairValueStrategy(cfg, om, MagicMock(), MagicMock(), price_feed=pf)

    market = {
        "ticker": "KXBTC-26MAY1017-T89500",
        "close_time": (datetime.now(timezone.utc) + timedelta(seconds=3600)).isoformat(),
    }
    await s._handle_market(market, db=db, cache=cache, now=datetime.now(timezone.utc))

    om.market_order.assert_called_once()
    call_kwargs = om.market_order.call_args.kwargs
    assert call_kwargs["side"] == "yes"
    assert call_kwargs["strategy"] == "crypto_fair_value"


@pytest.mark.asyncio
async def test_handle_market_skips_when_recently_fired():
    """Same ticker fired 30 sec ago → skip (recently_fired guard)."""
    cfg = _cfg()
    db = MagicMock()
    db.fetch_nearest_crypto_iv = AsyncMock(return_value={
        "asset": "BTC", "expiry_date": "2026-05-13", "atm_iv": 0.50,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    })
    db.total_open_exposure = AsyncMock(return_value=0.0)
    pf = MagicMock()
    pf.get_price = MagicMock(return_value=90000.0)
    pf.is_stale = MagicMock(return_value=False)
    pf.get_volatility = MagicMock(return_value=0.50)
    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock()
    om._db = db
    cache = MagicMock()
    cache.get = MagicMock(return_value=_fresh_entry(yes_ask=0.30, no_ask=0.70))
    s = CryptoFairValueStrategy(cfg, om, MagicMock(), MagicMock(), price_feed=pf)
    s._recently_fired["KXBTC-26MAY1017-T89500"] = time.time() - 30  # 30s ago

    market = {
        "ticker": "KXBTC-26MAY1017-T89500",
        "close_time": (datetime.now(timezone.utc) + timedelta(seconds=3600)).isoformat(),
    }
    await s._handle_market(market, db=db, cache=cache, now=datetime.now(timezone.utc))
    om.market_order.assert_not_called()


@pytest.mark.asyncio
async def test_handle_market_skips_when_spot_stale():
    cfg = _cfg()
    pf = MagicMock()
    pf.is_stale = MagicMock(return_value=True)
    om = MagicMock()
    om.market_order = AsyncMock()
    om._db = MagicMock()
    cache = MagicMock()
    s = CryptoFairValueStrategy(cfg, om, MagicMock(), MagicMock(), price_feed=pf)
    market = {"ticker": "KXBTC-X-T100",
              "close_time": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
    await s._handle_market(market, db=MagicMock(), cache=cache,
                            now=datetime.now(timezone.utc))
    om.market_order.assert_not_called()


@pytest.mark.asyncio
async def test_handle_market_skips_when_exposure_cap_exceeded():
    cfg = _cfg()
    db = MagicMock()
    db.fetch_nearest_crypto_iv = AsyncMock(return_value={
        "asset": "BTC", "expiry_date": "2026-05-13", "atm_iv": 0.50,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    })
    db.total_open_exposure = AsyncMock(return_value=60.0)  # 60 + 30 > 80 cap
    pf = MagicMock()
    pf.get_price = MagicMock(return_value=90000.0)
    pf.is_stale = MagicMock(return_value=False)
    pf.get_volatility = MagicMock(return_value=0.50)
    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock()
    om._db = db
    cache = MagicMock()
    cache.get = MagicMock(return_value=_fresh_entry(yes_ask=0.30, no_ask=0.70))
    s = CryptoFairValueStrategy(cfg, om, MagicMock(), MagicMock(), price_feed=pf)
    market = {
        "ticker": "KXBTC-26MAY1017-T89500",
        "close_time": (datetime.now(timezone.utc) + timedelta(seconds=3600)).isoformat(),
    }
    await s._handle_market(market, db=db, cache=cache, now=datetime.now(timezone.utc))
    om.market_order.assert_not_called()


@pytest.mark.asyncio
async def test_handle_market_skips_when_depth_insufficient():
    cfg = _cfg()
    db = MagicMock()
    db.fetch_nearest_crypto_iv = AsyncMock(return_value={
        "asset": "BTC", "expiry_date": "2026-05-13", "atm_iv": 0.50,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    })
    db.total_open_exposure = AsyncMock(return_value=0.0)
    pf = MagicMock()
    pf.get_price = MagicMock(return_value=90000.0)
    pf.is_stale = MagicMock(return_value=False)
    pf.get_volatility = MagicMock(return_value=0.50)
    om = MagicMock()
    om.is_circuit_breaker_active = MagicMock(return_value=False)
    om.market_order = AsyncMock()
    om._db = db
    cache = MagicMock()
    cache.get = MagicMock(return_value=_fresh_entry(yes_ask=0.30, no_ask=0.70, depth=10.0))  # < 30
    s = CryptoFairValueStrategy(cfg, om, MagicMock(), MagicMock(), price_feed=pf)
    market = {
        "ticker": "KXBTC-26MAY1017-T89500",
        "close_time": (datetime.now(timezone.utc) + timedelta(seconds=3600)).isoformat(),
    }
    await s._handle_market(market, db=db, cache=cache, now=datetime.now(timezone.utc))
    om.market_order.assert_not_called()
