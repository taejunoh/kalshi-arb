"""Spot price collector — fetch Coinbase + Kraken BTC/ETH every ~5min via timer.

Stores into spot_observations table. Read-only on web APIs, write-only on DB.

Run modes:
  --once     Single fetch + insert + exit (default for systemd timer)
"""
import argparse
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config
from core.spot_scraper import fetch_all_spot

logger = logging.getLogger("spot_collector")


SCHEMA = """
CREATE TABLE IF NOT EXISTS spot_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at TEXT NOT NULL,
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    bid REAL,
    ask REAL,
    last REAL,
    volume_24h REAL
);
CREATE INDEX IF NOT EXISTS idx_spot_venue_symbol_ts ON spot_observations(venue, symbol, fetched_at);
"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true", help="Single fetch + insert + exit")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    config = Config()
    db_path = config.DATABASE_PATH if hasattr(config, "DATABASE_PATH") else "data/bot.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.executescript(SCHEMA)
    conn.commit()

    snapshots = fetch_all_spot()
    if not snapshots:
        logger.warning("No spot data fetched.")
        conn.close()
        return

    now = datetime.now(timezone.utc).isoformat()
    rows = [
        (now, s["venue"], s["symbol"], s["bid"], s["ask"], s["last"], s.get("volume_24h"))
        for s in snapshots
    ]
    cursor.executemany(
        "INSERT INTO spot_observations (fetched_at, venue, symbol, bid, ask, last, volume_24h) "
        "VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    logger.info(f"Inserted {len(rows)} spot observations: " +
                ", ".join(f"{r[1]}/{r[2]}={r[5]:.2f}" for r in rows))


if __name__ == "__main__":
    main()
