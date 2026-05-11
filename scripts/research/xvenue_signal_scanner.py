"""Phase 0 prevalence scanner for B1 xvenue_polymarket.

Mirrors strategies/xvenue_polymarket.py signal logic but does NOT fire any
trade. Logs every (slug × ticker) evaluation to cross_venue_signals for
offline analysis. Run for >=1 week before paper deployment to validate
mapping accuracy + fire rate + EV distribution.

Usage:
  python scripts/research/xvenue_signal_scanner.py --once
  python scripts/research/xvenue_signal_scanner.py --interval 60
"""
import argparse
import asyncio
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.config import Config
from core.database import Database
from core.kalshi_client import KalshiClient
from core.polymarket_mapping import PolymarketKalshiMapping
from strategies.xvenue_polymarket import XVenuePolymarketStrategy

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("xvenue_scanner")

MAPPING_PATH = "data/polymarket_kalshi_mapping.json"


def fetch_open_markets_for_prefix(client: KalshiClient, prefix: str) -> list[dict]:
    """List open markets whose ticker startswith prefix.

    Iterates open markets paginated. Scanner is research-only so a single
    full-universe pass per iteration is acceptable cost.
    """
    out = []
    cursor = None
    pages = 0
    while pages < 20:
        path = f"/trade-api/v2/markets?status=open&limit=200"
        if cursor:
            path += f"&cursor={cursor}"
        d = client._get(path)
        if not d:
            break
        for m in d.get("markets", []):
            if m.get("ticker", "").startswith(prefix):
                out.append(m)
        cursor = d.get("cursor") or None
        pages += 1
        if not cursor:
            break
    return out


async def scan_once(client: KalshiClient, db: Database, cfg: Config,
                    mapping: PolymarketKalshiMapping) -> dict:
    summary = defaultdict(int)
    strategy = XVenuePolymarketStrategy(cfg, None, None, client, mapping=mapping)
    now = datetime.now(timezone.utc)

    for slug in mapping.all_slugs():
        quote_row = await db.fetch_polymarket_quote(slug)
        if quote_row is None:
            summary[f"{slug}/no_quote"] += 1
            continue
        if not strategy._is_quote_fresh(quote_row, now=now):
            summary[f"{slug}/stale_quote"] += 1
            continue
        if not strategy._is_quote_volume_ok(quote_row):
            summary[f"{slug}/low_volume"] += 1
            continue
        poly_yes_raw = float(quote_row["yes_price"])

        for entry in mapping.entries_for_slug(slug):
            prefix = entry["kalshi_ticker_prefix"]
            outcome_match = entry["outcome_match"]
            poly_yes = strategy._apply_outcome_match(poly_yes_raw, outcome_match)

            markets = fetch_open_markets_for_prefix(client, prefix)
            if not markets:
                summary[f"{slug}/no_kalshi_match"] += 1
                continue

            for m in markets:
                ticker = m.get("ticker", "")
                yes_price, no_price = client.get_market_prices(ticker)
                if yes_price is None or no_price is None:
                    continue
                yes_ask, no_ask = yes_price, no_price
                yes_bid = max(0.0, 1.0 - no_ask)
                no_bid = max(0.0, 1.0 - yes_ask)
                kalshi_yes_mid = (yes_ask + yes_bid) / 2.0
                deviation = poly_yes - kalshi_yes_mid

                edge = strategy._compute_edge(
                    polymarket_yes=poly_yes,
                    kalshi_yes_ask=yes_ask, kalshi_yes_bid=yes_bid,
                    kalshi_no_ask=no_ask, kalshi_no_bid=no_bid,
                )
                if edge is None:
                    action = "skip_min_deviation_or_ev"
                    side = None
                    size = None
                else:
                    action = "would_fire"
                    side = edge["side"]
                    size = strategy._size_for_edge(edge["deviation"])

                await db.record_cross_venue_signal(
                    ticker=ticker, slug=slug,
                    kalshi_implied=kalshi_yes_mid,
                    polymarket_implied=poly_yes,
                    deviation=deviation, side=side,
                    expected_size_usdc=size, action_taken=action,
                )
                summary[f"{slug}/{action}"] += 1
    return dict(summary)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=60,
                        help="Seconds between scans")
    parser.add_argument("--once", action="store_true",
                        help="Run one scan then exit")
    args = parser.parse_args()

    cfg = Config()
    db = Database()
    await db.initialize()
    client = KalshiClient(cfg)
    mapping = PolymarketKalshiMapping(MAPPING_PATH)

    log.info(f"Starting xvenue_polymarket scanner — interval={args.interval}s")
    while True:
        t0 = time.time()
        try:
            summary = await scan_once(client, db, cfg, mapping)
            elapsed = time.time() - t0
            log.info(f"scan complete in {elapsed:.1f}s: {summary}")
        except Exception as e:
            log.exception(f"scan error: {e}")
        if args.once:
            break
        await asyncio.sleep(max(5.0, args.interval - (time.time() - t0)))


if __name__ == "__main__":
    asyncio.run(main())
