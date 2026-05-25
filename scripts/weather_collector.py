"""MLB weather collector — fetches forecast for outdoor MLB stadiums.

Runs daily (~10:00 UTC = ~6am ET, before US daytime games). Fetches 12h
forecast for each open-roof + retractable stadium. Skips dome stadiums.
Stores into weather_observations table.

Future use: MLB totals/runs strategy that adjusts for weather (wind toward
CF blowing out → totals over; cold + wind in → totals under).
"""
import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config
from core.weather_scraper import fetch_forecast

logger = logging.getLogger("weather_collector")


SCHEMA = """
CREATE TABLE IF NOT EXISTS weather_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at TEXT NOT NULL,
    team TEXT NOT NULL,
    stadium TEXT,
    lat REAL,
    lon REAL,
    current_temp_f REAL,
    current_wind_mph REAL,
    current_wind_dir REAL,
    weather_code INTEGER,
    hourly_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_weather_team_ts ON weather_observations(team, fetched_at);
"""


def load_stadiums(path: str) -> list[dict]:
    with open(path) as f:
        data = json.load(f)
    return [s for s in data["stadiums"] if s.get("roof") != "dome"]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true", help="Single fetch all + exit")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    config = Config()
    db_path = config.DATABASE_PATH if hasattr(config, "DATABASE_PATH") else "data/bot.db"
    stadiums_path = os.path.join(os.path.dirname(db_path), "mlb_stadiums.json")

    stadiums = load_stadiums(stadiums_path)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.executescript(SCHEMA)
    conn.commit()

    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    for s in stadiums:
        forecast = fetch_forecast(s["lat"], s["lon"])
        if forecast is None:
            continue
        hourly_blob = json.dumps({
            "time": forecast["hourly_time"],
            "temp_f": forecast["hourly_temp_f"],
            "wind_mph": forecast["hourly_wind_mph"],
            "wind_dir": forecast["hourly_wind_dir"],
            "precip_in": forecast["hourly_precip_in"],
            "precip_prob": forecast["hourly_precip_prob"],
        })
        cursor.execute(
            "INSERT INTO weather_observations (fetched_at, team, stadium, lat, lon, "
            "current_temp_f, current_wind_mph, current_wind_dir, weather_code, hourly_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                now, s["team"], s["name"], s["lat"], s["lon"],
                forecast["current_temp_f"], forecast["current_wind_mph"],
                forecast["current_wind_dir"], forecast["weather_code"],
                hourly_blob,
            ),
        )
        inserted += 1
        time.sleep(0.2)  # rate-limit politeness

    conn.commit()
    conn.close()
    logger.info(f"Inserted {inserted}/{len(stadiums)} stadium forecasts.")


if __name__ == "__main__":
    main()
