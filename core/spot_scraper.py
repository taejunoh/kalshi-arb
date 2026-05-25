"""Multi-venue crypto spot price scraper — Coinbase + Kraken public REST.

No auth required. Returns None on parse/HTTP failure. Designed for periodic
poll (every ~5min via systemd timer); store snapshots in spot_observations
table for future cross-venue lead-lag / premium-detection strategies.

Built 2026-05-25 as D4 minimal data collection (not yet consumed by any
strategy). Deribit index (existing) already aggregates these, but separate
storage enables per-venue divergence analysis when a strategy materializes.
"""
import logging
from typing import Optional

import httpx

logger = logging.getLogger("spot_scraper")


def _fetch_coinbase(symbol: str, timeout: float = 5.0) -> Optional[dict]:
    """Coinbase Exchange ticker for symbol (e.g., 'BTC-USD', 'ETH-USD')."""
    try:
        r = httpx.get(
            f"https://api.exchange.coinbase.com/products/{symbol}/ticker",
            timeout=timeout,
            headers={"User-Agent": "kalshi-arb/1.0"},
        )
    except Exception as e:
        logger.warning(f"Coinbase fetch failed for {symbol}: {e}")
        return None
    if r.status_code != 200:
        logger.warning(f"Coinbase HTTP {r.status_code} for {symbol}")
        return None
    try:
        d = r.json()
        return {
            "venue": "coinbase",
            "symbol": symbol,
            "bid": float(d["bid"]),
            "ask": float(d["ask"]),
            "last": float(d["price"]),
            "volume_24h": float(d.get("volume", 0)),
        }
    except (KeyError, ValueError, TypeError) as e:
        logger.warning(f"Coinbase parse failed for {symbol}: {e}")
        return None


def _fetch_kraken(pair: str, timeout: float = 5.0) -> Optional[dict]:
    """Kraken public ticker for pair (e.g., 'XBTUSD', 'ETHUSD').

    Note: Kraken returns 'XXBTZUSD' as the response key for 'XBTUSD' input.
    """
    try:
        r = httpx.get(
            f"https://api.kraken.com/0/public/Ticker?pair={pair}",
            timeout=timeout,
            headers={"User-Agent": "kalshi-arb/1.0"},
        )
    except Exception as e:
        logger.warning(f"Kraken fetch failed for {pair}: {e}")
        return None
    if r.status_code != 200:
        logger.warning(f"Kraken HTTP {r.status_code} for {pair}")
        return None
    try:
        body = r.json()
        if body.get("error"):
            logger.warning(f"Kraken error for {pair}: {body['error']}")
            return None
        result = body.get("result", {})
        if not result:
            return None
        # Kraken returns one key per pair; pick the first
        key = next(iter(result))
        t = result[key]
        return {
            "venue": "kraken",
            "symbol": pair,
            "bid": float(t["b"][0]),
            "ask": float(t["a"][0]),
            "last": float(t["c"][0]),
            "volume_24h": float(t["v"][1]),  # 24h vol
        }
    except (KeyError, ValueError, TypeError, StopIteration) as e:
        logger.warning(f"Kraken parse failed for {pair}: {e}")
        return None


VENUE_FETCHERS = {
    ("coinbase", "BTC"): lambda: _fetch_coinbase("BTC-USD"),
    ("coinbase", "ETH"): lambda: _fetch_coinbase("ETH-USD"),
    ("kraken", "BTC"): lambda: _fetch_kraken("XBTUSD"),
    ("kraken", "ETH"): lambda: _fetch_kraken("ETHUSD"),
}


def fetch_all_spot() -> list[dict]:
    """Returns list of normalized dicts (one per successful venue×asset)."""
    out = []
    for key, fetcher in VENUE_FETCHERS.items():
        result = fetcher()
        if result is not None:
            out.append(result)
    return out
