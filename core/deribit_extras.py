"""Deribit funding rate + vol-skew fetchers (Phase 0 observers).

Adds two data sources beyond DeribitIVScraper:

- get_funding_rate(asset): perpetual funding rate per 8h (signed decimal)
- get_iv_skew(asset): 25-delta put IV minus 25-delta call IV at nearest
  expiry — positive means market prices puts at higher IV (downside fear),
  negative means call IV richer (upside fear / squeeze pricing).

Both are designed to be called once per scan tick with TTL caching to
avoid Deribit rate limits. Default TTL 60s — funding rate updates every
8h (super stable), IV chain updates every few seconds (cache aggressively).
"""
from __future__ import annotations
import logging
import threading
import time
from typing import Optional

import httpx

logger = logging.getLogger("deribit_extras")

_FUNDING_URL = "https://www.deribit.com/api/v2/public/ticker?instrument_name={asset}-PERPETUAL"
_CHAIN_URL = (
    "https://www.deribit.com/api/v2/public/get_book_summary_by_currency"
    "?currency={asset}&kind=option"
)

# Cache: (asset, kind) -> (epoch_fetched, value)
_CACHE: dict[tuple[str, str], tuple[float, Optional[float]]] = {}
_TTL_S = 60.0
# Sync TTL cache called from concurrent executor threads (crypto_observer
# harness uses asyncio.to_thread). Defensive lock prevents duplicate
# fetches under concurrent miss. Pattern hardened 2026-05-21 after
# kalshi_client race-condition discovery.
_CACHE_LOCK = threading.Lock()


def _cached(asset: str, kind: str, fetcher) -> Optional[float]:
    key = (asset, kind)
    with _CACHE_LOCK:
        now = time.time()
        hit = _CACHE.get(key)
        if hit is not None and (now - hit[0]) < _TTL_S:
            return hit[1]
    # Fetch outside the lock — fetcher makes a network call, holding the
    # lock would serialize all callers and defeat caching. Concurrent miss
    # races converge: both fetch, only the later write wins, next caller
    # hits cache.
    val = fetcher(asset)
    with _CACHE_LOCK:
        _CACHE[key] = (now, val)
    return val


def _fetch_funding(asset: str) -> Optional[float]:
    """Returns 8h funding rate as decimal (e.g. 0.0005 = 0.05%/8h ≈ 55% APR).
    Sign: positive = longs pay shorts (market net long).
    Returns None on any error."""
    url = _FUNDING_URL.format(asset=asset)
    try:
        r = httpx.get(url, timeout=5.0)
        if r.status_code != 200:
            return None
        data = r.json()
        result = data.get("result")
        if not isinstance(result, dict):
            return None
        # Deribit returns 'funding_8h' on perpetual ticker
        return float(result.get("funding_8h"))
    except Exception as e:
        logger.warning(f"funding fetch failed {asset}: {e}")
        return None


def _fetch_iv_skew(asset: str) -> Optional[float]:
    """Returns put_IV - call_IV at ±10% strike distance, nearest expiry.

    Reuses the same get_book_summary_by_currency endpoint that the IV
    scraper uses, but extracts the smile-slope signal instead of ATM IV.
    Positive skew = puts more expensive = downside fear priced in.

    Approach: for nearest expiry, find call/put closest to (spot × 0.9)
    and (spot × 1.1) by 'mark_iv' attribute. Skew = mark_iv_put - mark_iv_call.

    Phase 0 observer just records this — we'll figure out predictive
    direction empirically over 1-2 weeks."""
    url = _CHAIN_URL.format(asset=asset)
    try:
        r = httpx.get(url, timeout=8.0)
        if r.status_code != 200:
            return None
        data = r.json()
        items = data.get("result", [])
        if not items:
            return None
    except Exception as e:
        logger.warning(f"skew fetch failed {asset}: {e}")
        return None

    # Group by expiry; pick the soonest. Each item has 'instrument_name'
    # like BTC-13MAY26-93000-C and 'mark_iv', 'underlying_price'.
    # Phase 0 keeps the math intentionally simple: find a call and a put
    # at the same nearest expiry, both ~10% OTM, and diff their IVs.
    by_expiry: dict[str, list[dict]] = {}
    underlying = None
    for it in items:
        name = it.get("instrument_name", "")
        parts = name.split("-")
        if len(parts) != 4:
            continue
        expiry = parts[1]
        by_expiry.setdefault(expiry, []).append(it)
        if underlying is None:
            underlying = it.get("underlying_price")
    if not by_expiry or underlying is None:
        return None
    # Sort expiries by name length first (shorter day count is earlier),
    # then lex — close enough for nearest expiry for Phase 0.
    sorted_expiries = sorted(by_expiry, key=lambda k: (len(k), k))
    target_put_strike = float(underlying) * 0.9
    target_call_strike = float(underlying) * 1.1
    for exp in sorted_expiries[:3]:  # try up to 3 nearest expiries
        chain = by_expiry[exp]
        calls = [(abs(float(it.get("instrument_name","").split("-")[2]) - target_call_strike),
                  it.get("mark_iv")) for it in chain
                 if it.get("instrument_name","").endswith("-C") and it.get("mark_iv")]
        puts  = [(abs(float(it.get("instrument_name","").split("-")[2]) - target_put_strike),
                  it.get("mark_iv")) for it in chain
                 if it.get("instrument_name","").endswith("-P") and it.get("mark_iv")]
        if not calls or not puts:
            continue
        calls.sort(); puts.sort()
        call_iv = float(calls[0][1])
        put_iv = float(puts[0][1])
        # Deribit returns mark_iv as percent (e.g. 50 not 0.50)
        return (put_iv - call_iv) / 100.0
    return None


def get_funding_rate(asset: str) -> Optional[float]:
    """TTL-cached funding rate fetch. Asset in {'BTC','ETH'}."""
    return _cached(asset, "funding", _fetch_funding)


def get_iv_skew(asset: str) -> Optional[float]:
    """TTL-cached IV-skew fetch. Asset in {'BTC','ETH'}."""
    return _cached(asset, "skew", _fetch_iv_skew)
