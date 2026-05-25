"""Open-Meteo weather forecast scraper — no API key required.

Fetches current weather + 12h hourly forecast for a stadium's coordinates.
Data fields: temperature_2m, wind_speed_10m, wind_direction_10m,
precipitation, precipitation_probability.

Open-Meteo free tier: 10,000 calls/day, ~1 call/second, no auth.
"""
import logging
from typing import Optional

import httpx

logger = logging.getLogger("weather_scraper")

OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"


def fetch_forecast(lat: float, lon: float, timeout: float = 5.0) -> Optional[dict]:
    """Returns dict with current_weather + next 12h hourly forecast, or None."""
    try:
        r = httpx.get(
            OPEN_METEO_BASE,
            params={
                "latitude": lat,
                "longitude": lon,
                "current_weather": "true",
                "hourly": "temperature_2m,wind_speed_10m,wind_direction_10m,precipitation,precipitation_probability",
                "forecast_hours": 12,
                "timezone": "UTC",
                "wind_speed_unit": "mph",
                "temperature_unit": "fahrenheit",
            },
            timeout=timeout,
            headers={"User-Agent": "kalshi-arb/1.0"},
        )
    except Exception as e:
        logger.warning(f"Open-Meteo fetch failed for {lat},{lon}: {e}")
        return None
    if r.status_code != 200:
        logger.warning(f"Open-Meteo HTTP {r.status_code} for {lat},{lon}")
        return None
    try:
        d = r.json()
        current = d.get("current_weather", {})
        hourly = d.get("hourly", {})
        return {
            "lat": lat,
            "lon": lon,
            "current_temp_f": current.get("temperature"),
            "current_wind_mph": current.get("windspeed"),
            "current_wind_dir": current.get("winddirection"),
            "weather_code": current.get("weathercode"),
            "hourly_temp_f": hourly.get("temperature_2m"),
            "hourly_wind_mph": hourly.get("wind_speed_10m"),
            "hourly_wind_dir": hourly.get("wind_direction_10m"),
            "hourly_precip_in": hourly.get("precipitation"),
            "hourly_precip_prob": hourly.get("precipitation_probability"),
            "hourly_time": hourly.get("time"),
        }
    except (KeyError, ValueError, TypeError) as e:
        logger.warning(f"Open-Meteo parse failed for {lat},{lon}: {e}")
        return None
