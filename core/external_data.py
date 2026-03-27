import json
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("external_data")

# Static fallback data for common person/keyword pairs
_STATIC_FREQUENCIES: dict[str, dict[str, float]] = {}
_STATIC_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "mention_frequencies.json")


def _load_static_data():
    global _STATIC_FREQUENCIES
    if _STATIC_FREQUENCIES:
        return
    try:
        resolved = os.path.abspath(_STATIC_DATA_PATH)
        if os.path.exists(resolved):
            with open(resolved) as f:
                _STATIC_FREQUENCIES = json.load(f)
            total_keys = sum(len(v) for v in _STATIC_FREQUENCIES.values())
            logger.info(f"Loaded mention frequencies: {len(_STATIC_FREQUENCIES)} persons, {total_keys} keywords from {resolved}")
    except Exception as e:
        logger.warning(f"Could not load static frequency data: {e}")


class FrequencyDataProvider:
    """Provides historical mention frequency for person/keyword pairs."""

    def __init__(self, api_url: str = ""):
        self._api_url = api_url
        self._http: Optional[httpx.AsyncClient] = None

    async def get_mention_frequency(self, person: str, keyword: str) -> Optional[float]:
        """Returns estimated probability (0.0-1.0) or None if unavailable."""
        # Try API first if configured
        if self._api_url:
            result = await self._query_api(person, keyword)
            if result is not None:
                return result
        # Fall back to static data
        return self._query_static(person, keyword)

    async def _query_api(self, person: str, keyword: str) -> Optional[float]:
        try:
            if self._http is None:
                self._http = httpx.AsyncClient(timeout=5.0)
            resp = await self._http.get(
                self._api_url,
                params={"person": person, "keyword": keyword},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("frequency")
        except Exception as e:
            logger.debug(f"API frequency lookup failed for {person}/{keyword}: {e}")
            return None

    def _query_static(self, person: str, keyword: str) -> Optional[float]:
        _load_static_data()
        person_key = person.lower().strip()
        keyword_key = keyword.lower().strip()
        person_data = _STATIC_FREQUENCIES.get(person_key, {})
        return person_data.get(keyword_key)

    async def close(self):
        if self._http:
            await self._http.aclose()
            self._http = None
