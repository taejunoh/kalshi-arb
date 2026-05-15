"""Phase 0 prevalence scanner — Polymarket totals/spreads observer.

Scrapes Polymarket gamma-api for daily sports totals and spreads and
writes one row per (slug, timestamp) into polymarket_totals_observations.
Does NOT fire trades; this is a data-collection phase.

Kalshi event mapping is left to NULL here — post-hoc joins by
(sport, game_date, away/home raw codes) against odds_observations.

Usage:
  python scripts/research/polymarket_totals_signal_scanner.py --once
  python scripts/research/polymarket_totals_signal_scanner.py --interval 300
"""
import argparse
import asyncio
import logging
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.database import Database
from core.polymarket_totals_scraper import PolymarketTotalsScraper

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("polymarket_totals_scanner")


async def scan_once(db: Database, scraper: PolymarketTotalsScraper) -> dict:
    rows = scraper.fetch_all_totals()
    summary: Counter = Counter()
    for r in rows:
        try:
            await db.record_polymarket_totals_observation(
                slug=r["slug"],
                sport=r["sport"],
                game_date=r.get("game_date"),
                away_team_raw=r.get("away_team_raw"),
                home_team_raw=r.get("home_team_raw"),
                market_type=r["market_type"],
                line=r["line"],
                spread_side=r.get("spread_side"),
                yes_price=r.get("yes_price"),
                no_price=r.get("no_price"),
                volume_24h=r.get("volume_24h"),
                question=r.get("question"),
                kalshi_event_ticker=None,
            )
            summary[f"{r['sport']}/{r['market_type']}"] += 1
        except Exception as e:
            log.warning(f"record failed for {r.get('slug')}: {e}")
            summary["record_error"] += 1
    summary["total"] = len(rows)
    return dict(summary)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=300,
                        help="Seconds between scans (default 300 = 5min)")
    parser.add_argument("--once", action="store_true",
                        help="Run one scan then exit")
    args = parser.parse_args()

    db = Database()
    await db.initialize()
    scraper = PolymarketTotalsScraper()

    log.info(f"Starting Polymarket totals Phase 0 scanner — interval={args.interval}s")
    while True:
        t0 = time.time()
        try:
            summary = await scan_once(db, scraper)
            elapsed = time.time() - t0
            log.info(f"scan complete in {elapsed:.1f}s: {summary}")
        except Exception as e:
            log.exception(f"scan error: {e}")
        if args.once:
            break
        await asyncio.sleep(max(5.0, args.interval - (time.time() - t0)))


if __name__ == "__main__":
    asyncio.run(main())
