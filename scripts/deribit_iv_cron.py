# scripts/deribit_iv_cron.py
"""Deribit IV refresh cron — runs every 4 hours.

Fetches BTC + ETH option chains from Deribit, extracts ATM IV per
expiry, upserts into crypto_iv DB. Designed to run as a separate
process (launchd/systemd) so scrape failures don't impact the main
bot loop.

Usage:
  python scripts/deribit_iv_cron.py --once
  python scripts/deribit_iv_cron.py        # loop every 4h
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
from core.deribit_iv import DeribitIVScraper, extract_atm_iv

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("deribit_cron")

# Spot reference for ATM strike selection. Manual fallback list — in
# production these will be overridden by PriceFeed.get_price if running
# inside the main bot, but the cron is standalone so use coarse defaults.
# Adjust periodically as crypto prices drift; the strike picker is robust
# to ±20% spot error (just picks slightly OTM/ITM).
SPOT_REFERENCE_FALLBACK = {
    "BTC": 90000.0,
    "ETH": 3000.0,
}


async def refresh_one(db: Database, scraper: DeribitIVScraper, asset: str) -> int:
    chain = scraper.fetch_chain(asset)
    if not chain:
        log.warning(f"Deribit fetch returned no chain for {asset}")
        return 0
    spot = SPOT_REFERENCE_FALLBACK.get(asset, 100.0)
    atm_per_expiry = extract_atm_iv(chain, asset=asset, current_spot=spot)
    updated = 0
    for expiry, atm_iv in atm_per_expiry.items():
        await db.record_crypto_iv(asset, expiry, atm_iv)
        updated += 1
    return updated


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Single refresh + exit")
    args = parser.parse_args()

    cfg = Config()
    db = Database()
    await db.initialize()
    scraper = DeribitIVScraper()

    interval = 4 * 60 * 60  # 4 hours

    while True:
        t0 = time.time()
        try:
            n_btc = await refresh_one(db, scraper, "BTC")
            n_eth = await refresh_one(db, scraper, "ETH")
            elapsed = time.time() - t0
            log.info(f"refresh complete in {elapsed:.1f}s — btc={n_btc} eth={n_eth}")
        except Exception as e:
            log.exception(f"refresh error: {e}")
        if args.once:
            break
        await asyncio.sleep(max(60.0, interval - (time.time() - t0)))


if __name__ == "__main__":
    asyncio.run(main())
