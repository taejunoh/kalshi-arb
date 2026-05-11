"""Deribit options chain scraper — ATM IV per expiry for BTC + ETH.

Public REST endpoint, no auth required. v0 returns nearest-expiry single
IV; multi-expiry interpolation deferred to Phase 2.

Spec: docs/superpowers/specs/2026-05-10-crypto-fair-value-trader-design.md
"""
import logging
import re
from collections import defaultdict
from typing import Optional

import httpx

logger = logging.getLogger("deribit_iv")

DERIBIT_URL = (
    "https://www.deribit.com/api/v2/public/get_book_summary_by_currency"
    "?currency={asset}&kind=option"
)

# BTC-13MAY26-93000-C
INSTRUMENT_RE = re.compile(
    r"^([A-Z]+)-(\d{1,2})([A-Z]{3})(\d{2})-(\d+(?:\.\d+)?)-([CP])$"
)

MONTH_TO_NUM = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
    "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
}


def parse_instrument_name(name: str) -> Optional[tuple[str, str, float, str]]:
    """'BTC-13MAY26-93000-C' → ('BTC', '2026-05-13', 93000.0, 'C')."""
    m = INSTRUMENT_RE.match(name or "")
    if not m:
        return None
    asset, day, month_abbr, year, strike, kind = m.groups()
    month_num = MONTH_TO_NUM.get(month_abbr)
    if not month_num:
        return None
    iso_date = f"20{year}-{month_num}-{day.zfill(2)}"
    try:
        strike_f = float(strike)
    except ValueError:
        return None
    return asset, iso_date, strike_f, kind


def extract_atm_iv(
    chain: list[dict], asset: str, current_spot: float,
) -> dict[str, float]:
    """For each expiry in `chain` (filtered to `asset`), find the ATM call+put
    pair and return atm_iv = (call.mark_iv + put.mark_iv) / 2.

    Skips any expiry that doesn't have BOTH a call and a put.
    """
    groups: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    for item in chain or []:
        parsed = parse_instrument_name(item.get("instrument_name", ""))
        if not parsed:
            continue
        a, expiry, strike, kind = parsed
        if a != asset:
            continue
        iv = item.get("mark_iv")
        if iv is None:
            continue
        try:
            iv_f = float(iv)
        except (TypeError, ValueError):
            continue
        groups[(expiry, kind)].append((strike, iv_f))

    out: dict[str, float] = {}
    expiries = {e for (e, _k) in groups.keys()}
    for expiry in expiries:
        calls = groups.get((expiry, "C"))
        puts = groups.get((expiry, "P"))
        if not calls or not puts:
            continue
        closest_call = min(calls, key=lambda t: abs(t[0] - current_spot))
        closest_put = min(puts, key=lambda t: abs(t[0] - current_spot))
        out[expiry] = (closest_call[1] + closest_put[1]) / 2.0
    return out


class DeribitIVScraper:
    """Fetches Deribit option chain for a single currency. Returns None on error."""

    def __init__(self, timeout: float = 10.0):
        self._timeout = timeout

    def fetch_chain(self, asset: str) -> Optional[list[dict]]:
        url = DERIBIT_URL.format(asset=asset)
        try:
            response = httpx.get(url, timeout=self._timeout)
        except Exception as e:
            logger.warning(f"Deribit fetch failed for {asset}: {e}")
            return None
        if response.status_code != 200:
            logger.warning(f"Deribit HTTP {response.status_code} for {asset}")
            return None
        try:
            data = response.json()
        except Exception as e:
            logger.warning(f"Deribit JSON parse failed for {asset}: {e}")
            return None
        result = data.get("result")
        if not isinstance(result, list):
            return None
        return result
