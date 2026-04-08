"""Economic data provider — BLS CPI, Atlanta Fed GDPNow."""
import logging
import math
from typing import Optional

import httpx

logger = logging.getLogger("econ_data")


class EconDataProvider:
    """Fetch and analyze economic indicator data for trading signals."""

    BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
    CPI_SERIES_ID = "CUSR0000SA0"  # CPI-U All Items, Seasonally Adjusted

    def __init__(self):
        self._cpi_history: list[dict] = []  # [{period, value, mom_pct}, ...]
        self._last_fetch_time = 0.0

    async def fetch_cpi_history(self) -> list[dict]:
        """Fetch CPI-U monthly data from BLS API. Returns sorted list with MoM %."""
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                resp = await http.get(
                    self.BLS_API_URL + self.CPI_SERIES_ID,
                    params={"startyear": "2024", "endyear": "2026"},
                )
                resp.raise_for_status()
                data = resp.json()

            series = data.get("Results", {}).get("series", [{}])[0]
            points = series.get("data", [])

            # Sort chronologically
            points.sort(key=lambda x: (x["year"], x["period"]))

            result = []
            prev_val = None
            for p in points:
                val = float(p["value"]) if p["value"] != "-" else None
                if val is None:
                    continue
                mom_pct = ((val - prev_val) / prev_val * 100) if prev_val else None
                result.append({
                    "period": f"{p['year']}-{p['period']}",
                    "value": val,
                    "mom_pct": round(mom_pct, 4) if mom_pct is not None else None,
                })
                prev_val = val

            self._cpi_history = result
            logger.info(f"[ECON] Fetched {len(result)} CPI data points from BLS")
            return result
        except Exception as e:
            logger.error(f"[ECON] BLS API error: {e}")
            return self._cpi_history  # return cached

    def estimate_cpi_mom(self, n_months: int = 6) -> Optional[dict]:
        """Estimate next CPI MoM % change using recent history.

        Returns {mean, std, latest_period, latest_value} or None.
        Uses last n_months of MoM changes to calculate mean and std.
        """
        history = [h for h in self._cpi_history if h["mom_pct"] is not None]
        if len(history) < n_months:
            return None

        recent = history[-n_months:]
        changes = [h["mom_pct"] for h in recent]
        mean = sum(changes) / len(changes)
        variance = sum((x - mean) ** 2 for x in changes) / len(changes)
        std = math.sqrt(variance) if variance > 0 else 0.01

        latest = history[-1]
        return {
            "mean": round(mean, 4),
            "std": round(std, 4),
            "latest_period": latest["period"],
            "latest_value": latest["value"],
            "n_months": n_months,
        }

    @staticmethod
    def normal_cdf(x: float) -> float:
        """Standard Normal CDF."""
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    def probability_above_threshold(self, threshold_pct: float,
                                     mean: float, std: float) -> float:
        """P(CPI MoM > threshold) using Normal distribution."""
        if std <= 0:
            return 1.0 if mean > threshold_pct else 0.0
        z = (mean - threshold_pct) / std
        return self.normal_cdf(z)
