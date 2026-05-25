"""Polymarket gamma-api public REST scraper.

No auth required (read-only public market data). Returns None on any
parse/HTTP failure — caller (cron) handles fallback to stale cache.

Spec: docs/superpowers/specs/2026-05-11-xvenue-polymarket-trader-design.md
"""
import logging
from typing import Optional

import httpx

logger = logging.getLogger("polymarket_scraper")

GAMMA_API_BASE = "https://gamma-api.polymarket.com/markets"


def _decode_field(value):
    """gamma-api returns `outcomes` and `outcomePrices` as JSON-encoded strings
    (e.g., '["Yes", "No"]'). Decode if needed, pass through if already list."""
    if isinstance(value, str):
        try:
            import json as _json
            return _json.loads(value)
        except (ValueError, TypeError):
            return None
    return value


def parse_market_response(raw: dict) -> Optional[dict]:
    """Normalize gamma-api market dict → {slug, yes_price, no_price,
    volume_24h, best_bid, best_ask, spread, liquidity_num}.

    Returns None if market is closed, inactive, multi-outcome, or has
    malformed price data.

    2026-05-25: extended with order-book-derived fields (best_bid/best_ask
    from gamma-api). yes_price/no_price are still the "last trade" prices
    (outcomePrices); best_bid/best_ask are the order-book top.
    """
    if not raw.get("active") or raw.get("closed"):
        return None
    outcomes = _decode_field(raw.get("outcomes")) or []
    if outcomes != ["Yes", "No"]:
        return None
    prices = _decode_field(raw.get("outcomePrices")) or []
    if not isinstance(prices, list) or len(prices) < 2:
        return None
    try:
        yes_price = float(prices[0])
        no_price = float(prices[1])
    except (TypeError, ValueError):
        return None
    vol_raw = raw.get("volume24hr") or raw.get("volume")
    try:
        volume_24h = float(vol_raw) if vol_raw is not None else None
    except (TypeError, ValueError):
        volume_24h = None
    slug = raw.get("slug")
    if not slug:
        return None

    def _safe_float(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    return {
        "slug": slug,
        "yes_price": yes_price,
        "no_price": no_price,
        "volume_24h": volume_24h,
        "best_bid": _safe_float(raw.get("bestBid")),
        "best_ask": _safe_float(raw.get("bestAsk")),
        "spread": _safe_float(raw.get("spread")),
        "liquidity_num": _safe_float(raw.get("liquidityNum")),
    }


class PolymarketScraper:
    """Single-market + batch-market fetcher for Polymarket gamma-api."""

    def __init__(self, timeout: float = 10.0):
        self._timeout = timeout

    def fetch_single(self, slug: str) -> Optional[dict]:
        """Returns normalized market dict or None."""
        try:
            response = httpx.get(
                f"{GAMMA_API_BASE}?slug={slug}", timeout=self._timeout,
            )
        except Exception as e:
            logger.warning(f"Polymarket fetch failed for {slug}: {e}")
            return None
        if response.status_code != 200:
            logger.warning(f"Polymarket HTTP {response.status_code} for {slug}")
            return None
        try:
            data = response.json()
        except Exception as e:
            logger.warning(f"Polymarket JSON parse failed for {slug}: {e}")
            return None
        # gamma-api returns either a list (when filtering) or single dict
        if isinstance(data, list):
            if not data:
                return None
            return parse_market_response(data[0])
        return parse_market_response(data)

    def fetch_batch(self, slugs: list[str]) -> dict[str, dict]:
        """Returns {slug: normalized_dict} for slugs that parse successfully.

        Issues one ?slug=<slug> request per entry. The plural form
        ?slugs=a,b,c is broken on Polymarket's side as of 2026-05-11 —
        it ignores the filter entirely and returns 20 unrelated popular
        markets, no matter what's passed. The singular form is the only
        one that does exact-match. With our mapping size (~20 slugs) the
        per-slug call rate is 20/min = 0.3 req/s, well within any
        reasonable rate limit.
        """
        out: dict[str, dict] = {}
        for slug in slugs:
            parsed = self.fetch_single(slug)
            if parsed and parsed["slug"] == slug:
                out[slug] = parsed
        return out
