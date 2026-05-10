"""Macro consensus refresh cron — runs every 4 hours.

Fetches CME FedWatch + Cleveland Fed Nowcast and upserts rows into
macro_consensus DB. Designed to be run as a separate process (e.g.
launchd/cron entry on the EC2 host) so scrape failures do NOT impact
the main bot loop.

Usage:
  python scripts/macro_consensus_cron.py --once
  python scripts/macro_consensus_cron.py        # loop every 4h
"""
import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.config import Config
from core.database import Database
from core.macro_consensus import (
    FedWatchScraper, CPINowcastScraper, bucket_prob_from_normal,
)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("macro_cron")

# Known FOMC meeting dates — keep manually maintained; alternative is a
# Kalshi /series API parse but the schedule is stable enough that a
# constant list is the simplest correct option for v0.
FOMC_DATES = [
    "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28",
    "2026-12-16", "2027-01-27", "2027-03-17", "2027-05-05",
]

# CPI bucket boundaries — KXCPI bucket suffix format is `T<value>`
# meaning "CPI MoM <= value%". Each bucket is a 0.1% wide interval
# centered on the suffix value: e.g., T0.3 covers (0.25, 0.35].
# v0 assumes 0.1% bucket width; verify against actual Kalshi spec
# (open question 1 in design doc).
CPI_BUCKET_VALUES = [-0.3, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3, 0.4, 0.5]
CPI_BUCKET_WIDTH = 0.1

# Upcoming CPI release dates — same logic as FOMC_DATES.
CPI_DATES = [
    "2026-06-10", "2026-07-15", "2026-08-12", "2026-09-11",
    "2026-10-15", "2026-11-13", "2026-12-10", "2027-01-14",
]


def event_key_fomc(meeting_date: str) -> str:
    """2026-06-17 → 'FOMC-26JUN' (matches strategy._extract_event_key)."""
    dt = datetime.fromisoformat(meeting_date)
    return f"FOMC-{dt.strftime('%y%b').upper()}"


def event_key_cpi(release_date: str) -> str:
    dt = datetime.fromisoformat(release_date)
    return f"CPI-{dt.strftime('%y%b').upper()}"


async def refresh_fomc(db: Database, scraper: FedWatchScraper) -> int:
    """Returns # of (event, bucket) rows updated."""
    updated = 0
    for date in FOMC_DATES:
        probs = scraper.fetch_meeting(date)
        if not probs:
            log.warning(f"FedWatch fetch returned no data for {date}")
            continue
        ek = event_key_fomc(date)
        for bucket, prob in probs.items():
            await db.record_macro_consensus(ek, bucket, float(prob), "fedwatch")
            updated += 1
    return updated


async def refresh_cpi(db: Database, scraper: CPINowcastScraper) -> int:
    """Pull point + std once, then fan out to per-bucket prob via normal CDF."""
    nowcast = scraper.fetch()
    if not nowcast:
        log.warning("CPI Nowcast scrape returned no data")
        return 0
    point = nowcast["cpi_mom_point"]
    std = nowcast["cpi_mom_std"]
    updated = 0
    for date in CPI_DATES:
        ek = event_key_cpi(date)
        for value in CPI_BUCKET_VALUES:
            low = value - CPI_BUCKET_WIDTH / 2.0
            high = value + CPI_BUCKET_WIDTH / 2.0
            prob = bucket_prob_from_normal(low, high, point, std)
            bucket = f"T{value}"
            await db.record_macro_consensus(ek, bucket, prob, "clevelandfed")
            updated += 1
    return updated


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Single refresh + exit")
    args = parser.parse_args()

    cfg = Config()
    db = Database()
    await db.initialize()
    fed_scraper = FedWatchScraper()
    cpi_scraper = CPINowcastScraper()

    interval = 4 * 60 * 60  # 4 hours

    while True:
        t0 = time.time()
        try:
            n_fomc = await refresh_fomc(db, fed_scraper)
            n_cpi = await refresh_cpi(db, cpi_scraper)
            elapsed = time.time() - t0
            log.info(f"refresh complete in {elapsed:.1f}s — "
                     f"fomc={n_fomc} cpi={n_cpi}")
        except Exception as e:
            log.exception(f"refresh error: {e}")
        if args.once:
            break
        await asyncio.sleep(max(60.0, interval - (time.time() - t0)))


if __name__ == "__main__":
    asyncio.run(main())
