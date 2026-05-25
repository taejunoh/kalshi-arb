"""Polymarket quote refresh cron — runs every 1 minute.

Reads mapping JSON, fetches all mapped slugs via gamma-api, upserts into
polymarket_quotes. Designed to run as a separate process (launchd/systemd)
so scrape failures don't impact the main bot loop.

Usage:
  python scripts/polymarket_quote_cron.py --once
  python scripts/polymarket_quote_cron.py        # loop every 60s
"""
import argparse
import asyncio
import logging
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.config import Config
from core.database import Database
from core.polymarket_mapping import PolymarketKalshiMapping
from core.polymarket_scraper import PolymarketScraper

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("polymarket_cron")

MAPPING_PATH = "data/polymarket_kalshi_mapping.json"


async def refresh(db: Database, scraper: PolymarketScraper,
                  mapping: PolymarketKalshiMapping) -> int:
    """Reload mapping, fetch all slugs, upsert quotes. Returns # of updates."""
    mapping.reload()
    slugs = mapping.all_slugs()
    if not slugs:
        log.warning("No slugs in mapping JSON")
        return 0
    # gamma-api can handle ~50 slugs per batch comfortably
    BATCH = 50
    updated = 0
    for i in range(0, len(slugs), BATCH):
        chunk = slugs[i:i + BATCH]
        out = scraper.fetch_batch(chunk)
        for slug, parsed in out.items():
            await db.record_polymarket_quote(
                slug=slug,
                yes_price=parsed["yes_price"],
                no_price=parsed["no_price"],
                volume_24h=parsed.get("volume_24h"),
                best_bid=parsed.get("best_bid"),
                best_ask=parsed.get("best_ask"),
                spread=parsed.get("spread"),
                liquidity_num=parsed.get("liquidity_num"),
            )
            updated += 1
        missing = set(chunk) - set(out.keys())
        if missing:
            log.warning(f"Slugs not returned/parsed: {sorted(missing)[:5]}"
                        f"{'...' if len(missing) > 5 else ''}")
    return updated


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Single refresh + exit")
    args = parser.parse_args()

    cfg = Config()
    db = Database()
    await db.initialize()
    scraper = PolymarketScraper()
    mapping = PolymarketKalshiMapping(MAPPING_PATH)

    interval = 60.0  # 1 minute

    while True:
        t0 = time.time()
        try:
            n = await refresh(db, scraper, mapping)
            elapsed = time.time() - t0
            log.info(f"refresh complete in {elapsed:.1f}s — updated={n}")
        except Exception as e:
            log.exception(f"refresh error: {e}")
        if args.once:
            break
        await asyncio.sleep(max(5.0, interval - (time.time() - t0)))


if __name__ == "__main__":
    asyncio.run(main())
