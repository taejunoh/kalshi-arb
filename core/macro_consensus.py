"""Macro event consensus — scrapers + bucket prob helpers + DB cache shims.

Stateless functions. Persistence is delegated to core/database.py.

Spec: docs/superpowers/specs/2026-05-09-macro-event-trader-design.md
"""
import logging
import math
import re
from typing import Optional

import httpx

logger = logging.getLogger("macro_consensus")


def normal_cdf(x: float, mu: float, sigma: float) -> float:
    """Cumulative distribution function for N(mu, sigma)."""
    if sigma <= 0:
        return 1.0 if x >= mu else 0.0
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))


def bucket_prob_from_normal(
    low: Optional[float],
    high: Optional[float],
    mu: float,
    sigma: float,
) -> float:
    """Probability that X ~ N(mu, sigma) falls in the half-open interval [low, high].

    `low=None` treated as -infinity (P(X <= high)).
    `high=None` treated as +infinity (P(X >= low)).
    `sigma=0` is the degenerate case: returns 1.0 if mu is inside the
    interval, 0.0 otherwise.
    """
    if sigma <= 0:
        in_lower = (low is None) or (mu >= low)
        in_upper = (high is None) or (mu <= high)
        return 1.0 if (in_lower and in_upper) else 0.0
    p_high = 1.0 if high is None else normal_cdf(high, mu, sigma)
    p_low = 0.0 if low is None else normal_cdf(low, mu, sigma)
    return max(0.0, p_high - p_low)


FEDWATCH_URL_TEMPLATE = (
    "https://www.cmegroup.com/CmeWS/mvc/tools/quoteboard/iframe/"
    "cmeFedWatchTool/probabilities/{date}"
)


class FedWatchScraper:
    """Fetches CME FedWatch implied probabilities per FOMC meeting outcome.

    The endpoint is unofficial. If CME changes the schema, this returns None
    and the strategy falls back to stale cached data (with staleness alert).
    """

    def __init__(self, timeout: float = 10.0):
        self._timeout = timeout

    def fetch_meeting(self, meeting_date: str) -> Optional[dict]:
        """Fetch implied prob per outcome bucket for one FOMC meeting.

        meeting_date: ISO date string, e.g. '2026-06-17'.
        Returns: {bucket: prob} dict (e.g. {'C25': 0.65, 'H0': 0.30}) or None on error.
        """
        url = FEDWATCH_URL_TEMPLATE.format(date=meeting_date)
        try:
            response = httpx.get(url, timeout=self._timeout)
        except Exception as e:
            logger.warning(f"FedWatch fetch failed for {meeting_date}: {e}")
            return None
        if response.status_code != 200:
            logger.warning(f"FedWatch HTTP {response.status_code} for {meeting_date}")
            return None
        try:
            data = response.json()
        except Exception as e:
            logger.warning(f"FedWatch JSON parse failed for {meeting_date}: {e}")
            return None
        meetings = data.get("meetings") or []
        for m in meetings:
            if m.get("date") == meeting_date:
                probs = m.get("probabilities") or {}
                if probs:
                    return dict(probs)
        return None


CPI_NOWCAST_URL = "https://www.clevelandfed.org/indicators-and-data/inflation-nowcasting"

# Match a row containing "CPI MoM" then two decimal numbers (point, std).
# Adjust this if Cleveland Fed page layout changes.
CPI_MOM_ROW_RE = re.compile(
    r"CPI\s*MoM.*?>\s*(-?\d+(?:\.\d+)?)\s*</td>\s*<td[^>]*>\s*(-?\d+(?:\.\d+)?)\s*</td>",
    re.IGNORECASE | re.DOTALL,
)


class CPINowcastScraper:
    """Fetches Cleveland Fed Inflation Nowcast point + std for next CPI release."""

    def __init__(self, timeout: float = 10.0):
        self._timeout = timeout

    def fetch(self) -> Optional[dict]:
        """Returns {'cpi_mom_point': float, 'cpi_mom_std': float} or None."""
        try:
            response = httpx.get(CPI_NOWCAST_URL, timeout=self._timeout)
        except Exception as e:
            logger.warning(f"CPI Nowcast fetch failed: {e}")
            return None
        if response.status_code != 200:
            logger.warning(f"CPI Nowcast HTTP {response.status_code}")
            return None
        match = CPI_MOM_ROW_RE.search(response.text)
        if not match:
            logger.warning("CPI Nowcast: CPI MoM row not found in HTML")
            return None
        try:
            point = float(match.group(1))
            std = float(match.group(2))
        except ValueError as e:
            logger.warning(f"CPI Nowcast: failed to parse point/std: {e}")
            return None
        return {"cpi_mom_point": point, "cpi_mom_std": std}
