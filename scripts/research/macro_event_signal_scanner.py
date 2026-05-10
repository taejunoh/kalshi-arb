"""Phase 0 prevalence scanner for C1 macro_event.

Mirrors strategies/macro_event.py's signal logic but does NOT fire any
trade. Logs every (signal, action_taken) tuple to macro_event_signals
for offline analysis. Run for ≥2 weeks before paper deployment to
validate fire rate + EV distribution.

Usage:
  python scripts/research/macro_event_signal_scanner.py --once
  python scripts/research/macro_event_signal_scanner.py --interval 3600
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
from strategies.macro_event import MacroEventStrategy, TARGET_SERIES

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("macro_scanner")


def fetch_open_markets(client: KalshiClient, series: str) -> list[dict]:
    out = []
    cursor = None
    pages = 0
    while pages < 10:
        path = f"/trade-api/v2/markets?status=open&series_ticker={series}&limit=200"
        if cursor:
            path += f"&cursor={cursor}"
        d = client._get(path)
        if not d:
            break
        out.extend(d.get("markets", []))
        cursor = d.get("cursor") or None
        pages += 1
        if not cursor:
            break
    return out


async def scan_once(client: KalshiClient, db: Database, cfg: Config) -> dict:
    summary = defaultdict(int)
    strategy = MacroEventStrategy(cfg, None, None, client)
    now = datetime.now(timezone.utc)
    for series in TARGET_SERIES:
        markets = fetch_open_markets(client, series)
        for m in markets:
            ticker = m.get("ticker", "")
            event_key = strategy._extract_event_key(m)
            bucket = strategy._extract_bucket(m)
            if not event_key or not bucket:
                continue

            # Window check
            if not strategy._inside_window(m, now=now):
                summary[f"{series}/skip_window"] += 1
                continue

            # Pull live book (Phase 0 — minor API cost is OK at 1h cadence)
            yes_price, no_price = client.get_market_prices(ticker)
            if yes_price is None or no_price is None:
                continue

            consensus_row = await db.fetch_macro_consensus(event_key, bucket)
            if not consensus_row:
                summary[f"{series}/no_consensus"] += 1
                continue
            consensus = consensus_row["prob"]

            # Staleness
            try:
                fetched_at = datetime.fromisoformat(
                    consensus_row["fetched_at"].replace("Z", "+00:00")
                )
                age_h = (now - fetched_at).total_seconds() / 3600.0
            except Exception:
                continue
            if age_h > cfg.MACRO_CONSENSUS_STALE_HOURS:
                summary[f"{series}/stale_consensus"] += 1
                continue

            edge = strategy._compute_edge(
                kalshi_yes_ask=yes_price, kalshi_yes_bid=yes_price,
                kalshi_no_ask=no_price, kalshi_no_bid=no_price,
                consensus=consensus,
            )
            kalshi_implied = (yes_price + yes_price) / 2.0  # mid for logging
            deviation = consensus - kalshi_implied

            if not edge:
                # Either dev too small or EV negative — log as skip_ev
                action = "skip_ev"
                side = None
                size = None
            else:
                action = "fired"
                side = edge["side"]
                size = strategy._size_for_edge(edge["deviation"])

            await db.record_macro_event_signal(
                ticker=ticker, event_key=event_key, bucket=bucket,
                kalshi_implied=kalshi_implied, consensus=consensus,
                deviation=deviation, side=side,
                expected_size_usdc=size, action_taken=action,
            )
            summary[f"{series}/{action}"] += 1
    return dict(summary)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=3600,
                        help="Seconds between scans")
    parser.add_argument("--once", action="store_true",
                        help="Run one scan then exit")
    args = parser.parse_args()

    cfg = Config()
    db = Database()
    await db.initialize()
    client = KalshiClient(cfg)

    log.info(f"Starting macro_event signal scanner — interval={args.interval}s")
    while True:
        t0 = time.time()
        try:
            summary = await scan_once(client, db, cfg)
            elapsed = time.time() - t0
            log.info(f"scan complete in {elapsed:.1f}s: {summary}")
        except Exception as e:
            log.exception(f"scan error: {e}")
        if args.once:
            break
        await asyncio.sleep(max(60.0, args.interval - (time.time() - t0)))


if __name__ == "__main__":
    asyncio.run(main())
