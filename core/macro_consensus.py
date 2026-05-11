"""Macro event consensus — scrapers + bucket prob helpers + DB cache shims.

Stateless functions. Persistence is delegated to core/database.py.

Spec: docs/superpowers/specs/2026-05-09-macro-event-trader-design.md

2026-05-11 source-status update (verified live):
- CME FedWatch endpoint returns HTTP 403 with "IP address blocked due to
  suspected web scraping activity" both from residential IPs AND from
  EC2 us-east-2. Browser User-Agent / Referer / Origin headers do not
  bypass — the block is at IP+endpoint level. The scraper now bumps
  timeout to 60s and detects 403 with a one-time clear warning instead
  of retrying every dial. P2 follow-up: switch FOMC consensus source to
  Polymarket fed-decision-* slugs (already populated by B1 cron) or to
  Fed Funds futures via a free API (Polygon free tier / FRED).
- Cleveland Fed Nowcast moved its CPI MoM number out of an HTML table
  into a fusioncharts JSON file at
  /-/media/files/webcharts/inflationnowcasting/nowcast_month.json.
  Scraper now fetches that JSON. Std is not in the file — we use a
  fixed 0.15% (Cleveland Fed's published RMSE for current-month
  nowcasts) until we add rolling-variance estimation.
"""
import logging
import math
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

# Browser-like headers — CME blocks even with these, but harmless to send
# in case they relax the block. The block is IP-based, not header-based
# (verified 2026-05-11).
FEDWATCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html",
}


class FedWatchScraper:
    """Fetches CME FedWatch implied probabilities per FOMC meeting outcome.

    Currently blocked by CME (HTTP 403, IP-level block). The scraper still
    runs so we'll pick up the data the moment CME relaxes the block, but
    until then `fetch_meeting` always returns None and the strategy falls
    back to stale cached data.
    """

    def __init__(self, timeout: float = 60.0):
        self._timeout = timeout

    def fetch_meeting(self, meeting_date: str) -> Optional[dict]:
        """Fetch implied prob per outcome bucket for one FOMC meeting.

        meeting_date: ISO date string, e.g. '2026-06-17'.
        Returns: {bucket: prob} dict (e.g. {'C25': 0.65, 'H0': 0.30}) or None on error.
        """
        url = FEDWATCH_URL_TEMPLATE.format(date=meeting_date)
        try:
            response = httpx.get(url, timeout=self._timeout, headers=FEDWATCH_HEADERS)
        except Exception as e:
            logger.warning(f"FedWatch fetch failed for {meeting_date}: {e}")
            return None
        if response.status_code == 403:
            # CME IP block — flagged separately so logs make the root cause
            # obvious. P2 follow-up: switch to Polymarket fed-decision-*
            # consensus or a free Fed Funds futures API.
            logger.warning(
                f"FedWatch BLOCKED (HTTP 403) for {meeting_date} — CME "
                "IP-level scraping block, see core/macro_consensus.py header"
            )
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


CPI_NOWCAST_URL = (
    "https://www.clevelandfed.org/-/media/files/webcharts/"
    "inflationnowcasting/nowcast_month.json"
)

# Cleveland Fed's published RMSE for current-month CPI MoM nowcasts is
# roughly 0.10-0.20 pp. 0.15 is the midpoint and matches what their
# methodology paper reports for month-of-release accuracy. P3 follow-up:
# compute std from rolling variance of the last N nowcast updates.
CPI_NOWCAST_DEFAULT_STD = 0.15


class CPINowcastScraper:
    """Fetches Cleveland Fed Inflation Nowcast point + std for next CPI release.

    Reads the fusioncharts JSON file that powers the public chart. Each
    record in the file is one charted month; the last record is the
    in-progress month. Within each record, dataset[seriesname='CPI Inflation']
    holds the nowcast time series — we take the last non-empty value.
    """

    def __init__(self, timeout: float = 30.0):
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
        try:
            data = response.json()
        except Exception as e:
            logger.warning(f"CPI Nowcast JSON parse failed: {e}")
            return None
        if not isinstance(data, list) or not data:
            logger.warning("CPI Nowcast: unexpected JSON shape (not a non-empty list)")
            return None
        latest = data[-1]
        for series in latest.get("dataset", []):
            if series.get("seriesname") != "CPI Inflation":
                continue
            for point in reversed(series.get("data", [])):
                value = point.get("value")
                if value in (None, ""):
                    continue
                try:
                    point_val = float(value)
                except (TypeError, ValueError):
                    continue
                return {
                    "cpi_mom_point": point_val,
                    "cpi_mom_std": CPI_NOWCAST_DEFAULT_STD,
                }
            logger.warning("CPI Nowcast: 'CPI Inflation' series has no non-empty values")
            return None
        logger.warning("CPI Nowcast: 'CPI Inflation' series not in latest record")
        return None
