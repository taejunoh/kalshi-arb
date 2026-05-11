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


def parse_market_response(raw: dict) -> Optional[dict]:
    """Normalize gamma-api market dict → {slug, yes_price, no_price, volume_24h}.

    Returns None if market is closed, inactive, multi-outcome, or has
    malformed price data.
    """
    if not raw.get("active") or raw.get("closed"):
        return None
    outcomes = raw.get("outcomes") or []
    if outcomes != ["Yes", "No"]:
        return None
    prices = raw.get("outcomePrices") or []
    if len(prices) < 2:
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
    return {
        "slug": slug,
        "yes_price": yes_price,
        "no_price": no_price,
        "volume_24h": volume_24h,
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

        gamma-api batch syntax: ?slugs=a,b,c (comma-separated).
        """
        if not slugs:
            return {}
        slug_param = ",".join(slugs)
        try:
            response = httpx.get(
                f"{GAMMA_API_BASE}?slugs={slug_param}", timeout=self._timeout,
            )
        except Exception as e:
            logger.warning(f"Polymarket batch fetch failed: {e}")
            return {}
        if response.status_code != 200:
            logger.warning(f"Polymarket batch HTTP {response.status_code}")
            return {}
        try:
            data = response.json()
        except Exception as e:
            logger.warning(f"Polymarket batch JSON parse failed: {e}")
            return {}
        if not isinstance(data, list):
            return {}
        out: dict[str, dict] = {}
        for raw in data:
            parsed = parse_market_response(raw)
            if parsed:
                out[parsed["slug"]] = parsed
        return out
