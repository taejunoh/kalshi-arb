"""Cross-venue perpetual funding-rate fetcher (Phase 0).

Reads BTC/ETH funding rates from Deribit, OKX, Gate.io, and MEXC in
parallel using httpx.AsyncClient + asyncio.gather. Each venue's fetch
is isolated: one venue failing returns None for that key, the other
venues still populate.

**Venue selection** (2026-05-20 evening): Bybit and Binance Futures are
permanently geo-blocked from AWS us-east-2 (Bybit CloudFront 403,
Binance fapi HTTP 451). Both confirmed inaccessible from prod EC2. Gate.io
and MEXC verified accessible 2026-05-20 evening (HTTP 200, clean 8h-native
funding-rate fields). 4-venue mode preserves robust consensus signal
even if one venue drops out.

Per [[sync_in_async_sweep_2026-05-19]] this module uses async httpx
exclusively. Do NOT mix in sync httpx.get inside the crypto_observer
harness scan_loop — it will block other observers.

API:
  VENUES = ("deribit", "okx", "gate", "mexc")
  get_funding(asset, venue) -> Optional[float]   # 8h-normalized decimal
  get_all_funding(asset)    -> dict[str, Optional[float]]   # 4 keys

All return values are signed 8h-decimal funding rates
(e.g. 0.0005 = 0.05%/8h ≈ 55% APR for longs paying shorts).
TTL-cached at 60s — funding rate updates every 8h on all 4 venues,
so polling more often is wasted.
"""
from __future__ import annotations
import asyncio
import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger("funding_rates_multi")

VENUES: tuple[str, ...] = ("deribit", "okx", "gate", "mexc")
_TTL_S: float = 60.0
_HTTP_TIMEOUT_S: float = 5.0

# Cache: (venue, asset) -> (epoch_fetched, value | None)
_CACHE: dict[tuple[str, str], tuple[float, Optional[float]]] = {}


def _clear_cache_for_tests() -> None:
    """Wipe the cache (test-only helper). Not part of public API."""
    _CACHE.clear()


# Venue endpoint templates. Each parser must return the 8h-normalized
# funding rate as a signed decimal float.
_VENUE_ENDPOINTS: dict[str, str] = {
    "deribit": "https://www.deribit.com/api/v2/public/ticker"
               "?instrument_name={asset}-PERPETUAL",
    "okx":     "https://www.okx.com/api/v5/public/funding-rate"
               "?instId={asset}-USDT-SWAP",
    "gate":    "https://api.gateio.ws/api/v4/futures/usdt/contracts"
               "/{asset}_USDT",
    "mexc":    "https://contract.mexc.com/api/v1/contract/funding_rate"
               "/{asset}_USDT",
}


def _parse_deribit(payload: dict) -> Optional[float]:
    result = payload.get("result")
    if not isinstance(result, dict):
        return None
    v = result.get("funding_8h")
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_okx(payload: dict) -> Optional[float]:
    # OKX V5 BTC-USDT-SWAP: fundingRate is per-interval (8h). Verified
    # 2026-05-20 from live API: nextFundingTime - fundingTime = 28_800_000ms.
    # Same instType="SWAP" applies to ETH-USDT-SWAP. No 8h normalization
    # required.
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return None
    try:
        return float(data[0].get("fundingRate"))
    except (TypeError, ValueError):
        return None


def _parse_gate(payload: dict) -> Optional[float]:
    # Gate.io V4 USDT-perp /contracts/{asset}_USDT returns
    # funding_rate as a decimal string + funding_interval in seconds.
    # Verified 2026-05-20 from live API: funding_interval=28800
    # (= 8h). No 8h normalization required for BTC/ETH USDT perps.
    # Returns the whole contract object at top level (not wrapped).
    if not isinstance(payload, dict):
        return None
    v = payload.get("funding_rate")
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_mexc(payload: dict) -> Optional[float]:
    # MEXC V1 /contract/funding_rate/{asset}_USDT returns
    # data.fundingRate (number) + data.collectCycle (hours).
    # Verified 2026-05-20 from live API: collectCycle=8.
    # No 8h normalization required for BTC/ETH USDT perps.
    if not isinstance(payload, dict):
        return None
    if not payload.get("success", True):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    v = data.get("fundingRate")
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


_VENUE_PARSERS = {
    "deribit": _parse_deribit,
    "okx":     _parse_okx,
    "gate":    _parse_gate,
    "mexc":    _parse_mexc,
}


async def _fetch_one(client: httpx.AsyncClient, asset: str,
                     venue: str) -> Optional[float]:
    """Fetch and parse one venue. Returns None on any failure."""
    url = _VENUE_ENDPOINTS[venue].format(asset=asset)
    try:
        r = await client.get(url, timeout=_HTTP_TIMEOUT_S)
    except Exception as e:
        logger.warning(f"funding fetch {venue}/{asset} HTTP error: {e}")
        return None
    if r.status_code != 200:
        logger.warning(f"funding fetch {venue}/{asset} status {r.status_code}")
        return None
    try:
        payload = r.json()
    except Exception as e:
        logger.warning(f"funding fetch {venue}/{asset} json error: {e}")
        return None
    return _VENUE_PARSERS[venue](payload)


async def get_funding(asset: str, venue: str) -> Optional[float]:
    """Fetch one venue's funding rate for the given asset.

    Returns 8h-normalized signed decimal. None on failure.
    Cached at _TTL_S per (venue, asset).
    """
    if venue not in VENUES:
        raise ValueError(f"unknown venue {venue!r}; expected one of {VENUES}")
    now = time.time()
    cached = _CACHE.get((venue, asset))
    if cached is not None and (now - cached[0]) < _TTL_S:
        return cached[1]
    async with httpx.AsyncClient() as client:
        val = await _fetch_one(client, asset, venue)
    _CACHE[(venue, asset)] = (now, val)
    return val


async def get_all_funding(asset: str) -> dict[str, Optional[float]]:
    """Fetch all VENUES in parallel. Each venue independent.

    Returns dict with one entry per VENUES name. Values are signed
    8h-decimal funding rate or None on per-venue failure.
    """
    now = time.time()

    # Decide which venues need a live fetch vs cache hit.
    fetched: dict[str, Optional[float]] = {}
    to_fetch: list[str] = []
    for v in VENUES:
        cached = _CACHE.get((v, asset))
        if cached is not None and (now - cached[0]) < _TTL_S:
            fetched[v] = cached[1]
        else:
            to_fetch.append(v)

    if to_fetch:
        async with httpx.AsyncClient() as client:
            coros = [_fetch_one(client, asset, v) for v in to_fetch]
            results = await asyncio.gather(*coros, return_exceptions=False)
        for v, val in zip(to_fetch, results):
            _CACHE[(v, asset)] = (now, val)
            fetched[v] = val

    return {v: fetched.get(v) for v in VENUES}
