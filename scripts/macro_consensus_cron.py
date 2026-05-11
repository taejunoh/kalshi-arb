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
from datetime import datetime, timedelta, timezone
from typing import Optional

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

# Polymarket slug → (event_key, bucket) for the FOMC half of C1
# macro_event. Used when CME FedWatch is IP-blocked (verified 2026-05-11).
# Bucket names match Kalshi's actual KXFEDDECISION ticker format
# (verified 2026-05-11 via /trade-api/v2/markets):
#   C25 = cut by exactly 25bp
#   C26 = cut by >25bp (covers 50bp+ — Fed only moves in 25bp increments
#         so this == 50bp+ in practice, matching Polymarket "50+ bps cut")
#   H0  = no change
#   H25 = hike by exactly 25bp
#   H26 = hike by >25bp (== 50bp+ in practice, matches Polymarket "50+ bps hike")
# Polymarket's 5 outcomes are mutually exclusive and yes_prices sum to
# ~1.0, so yes_price is the bucket probability directly. Strategy falls
# back to stale macro_consensus rows for missing buckets.
POLYMARKET_FOMC_MAPPING = {
    "FOMC-26JUN": {
        "C26": "will-the-fed-decrease-interest-rates-by-50-bps-after-the-june-2026-meeting",
        "C25": "will-the-fed-decrease-interest-rates-by-25-bps-after-the-june-2026-meeting",
        "H0":  "will-there-be-no-change-in-fed-interest-rates-after-the-june-2026-meeting",
        "H25": "will-the-fed-increase-interest-rates-by-25-bps-after-the-june-2026-meeting",
        "H26": "will-the-fed-increase-interest-rates-by-50-bps-after-the-june-2026-meeting",
    },
    "FOMC-26JUL": {
        "C26": "will-the-fed-decrease-interest-rates-by-50-bps-after-the-july-2026-meeting",
        "C25": "will-the-fed-decrease-interest-rates-by-25-bps-after-the-july-2026-meeting",
        "H25": "will-the-fed-increase-interest-rates-by-25-bps-after-the-july-2026-meeting",
        # Jul H0, H26 not yet on Polymarket — listings appear closer to
        # meeting. Cron skips these buckets and emits a debug log.
    },
}

# polymarket_quote_cron refreshes every 60s; we treat anything older
# than 30 min as stale (~30 missed cycles, likely cron is down).
POLYMARKET_QUOTE_MAX_STALENESS_SEC = 30 * 60


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


async def refresh_fomc_polymarket(
    db: Database,
    max_staleness_sec: int = POLYMARKET_QUOTE_MAX_STALENESS_SEC,
    now: Optional[datetime] = None,
) -> int:
    """Populate macro_consensus FOMC rows from polymarket_quotes when
    FedWatch is unavailable. Returns # of (event, bucket) rows updated."""
    if now is None:
        now = datetime.now(timezone.utc)
    updated = 0
    for event_key, slug_by_bucket in POLYMARKET_FOMC_MAPPING.items():
        for bucket, slug in slug_by_bucket.items():
            quote = await db.fetch_polymarket_quote(slug)
            if not quote:
                log.debug(f"Polymarket FOMC: no quote for {slug}")
                continue
            fetched_iso = quote.get("fetched_at")
            if fetched_iso:
                try:
                    fetched_dt = datetime.fromisoformat(fetched_iso.replace("Z", "+00:00"))
                    if fetched_dt.tzinfo is None:
                        fetched_dt = fetched_dt.replace(tzinfo=timezone.utc)
                    age = (now - fetched_dt).total_seconds()
                    if age > max_staleness_sec:
                        log.warning(
                            f"Polymarket FOMC: stale quote for {slug} "
                            f"(age={age:.0f}s > {max_staleness_sec}s); skipping"
                        )
                        continue
                except Exception as e:
                    log.warning(f"Polymarket FOMC: bad fetched_at on {slug}: {e}")
                    continue
            yes_price = quote.get("yes_price")
            if yes_price is None:
                continue
            await db.record_macro_consensus(
                event_key, bucket, float(yes_price), "polymarket",
            )
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
    n = len(CPI_BUCKET_VALUES)
    for date in CPI_DATES:
        ek = event_key_cpi(date)
        for i, value in enumerate(CPI_BUCKET_VALUES):
            # First / last buckets are open-ended to capture extreme tails.
            # Without this, far-tail outcomes (e.g. CPI = -0.5) get 0 prob
            # in the lowest bucket, biasing consensus low for tail buckets.
            low = None if i == 0 else value - CPI_BUCKET_WIDTH / 2.0
            high = None if i == n - 1 else value + CPI_BUCKET_WIDTH / 2.0
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
            n_fomc_fedwatch = await refresh_fomc(db, fed_scraper)
            n_fomc_polymarket = await refresh_fomc_polymarket(db)
            n_cpi = await refresh_cpi(db, cpi_scraper)
            elapsed = time.time() - t0
            log.info(
                f"refresh complete in {elapsed:.1f}s — "
                f"fomc_fedwatch={n_fomc_fedwatch} "
                f"fomc_polymarket={n_fomc_polymarket} cpi={n_cpi}"
            )
        except Exception as e:
            log.exception(f"refresh error: {e}")
        if args.once:
            break
        await asyncio.sleep(max(60.0, interval - (time.time() - t0)))


if __name__ == "__main__":
    asyncio.run(main())
