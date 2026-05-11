# scripts/research/crypto_fair_value_signal_scanner.py
"""Phase 0 prevalence scanner for B2 crypto_fair_value.

Mirrors strategies/crypto_fair_value.py's signal logic but does NOT
fire any trade. Logs every signal evaluation to crypto_fair_value_signals
for offline analysis.

Run for >=1 week before paper deployment to validate fire rate + EV
distribution across BTC/ETH/XRP.

NOTE: This script needs PriceFeed running. The simplest deployment is
to run it INSIDE the main bot process via main.py's _bot_tasks list,
or as a separate process that also subscribes its own PriceFeed.

For local --once testing, this script polls Coinbase REST as a quick
spot reference instead of subscribing to a full WS feed.

Usage:
  python scripts/research/crypto_fair_value_signal_scanner.py --once
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

import httpx

from core.config import Config
from core.database import Database
from core.kalshi_client import KalshiClient
from core.fair_value_math import lognormal_cdf_above
from strategies.crypto_fair_value import (
    CryptoFairValueStrategy, SERIES_TO_ASSET, TARGET_SERIES,
)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("crypto_scanner")

# Coinbase REST spot lookup for --once runs (no WS required)
COINBASE_SPOT_URL = "https://api.coinbase.com/v2/prices/{asset}-USD/spot"


def fetch_spot_via_rest(asset: str) -> float | None:
    try:
        r = httpx.get(COINBASE_SPOT_URL.format(asset=asset), timeout=10.0)
        if r.status_code != 200:
            return None
        return float(r.json()["data"]["amount"])
    except Exception as e:
        log.warning(f"Coinbase spot fetch failed for {asset}: {e}")
        return None


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
    # Build a no-OrderManager strategy instance just to use its parsing helpers
    strategy = CryptoFairValueStrategy(cfg, None, None, client, price_feed=None)
    now = datetime.now(timezone.utc)

    spots: dict[str, float] = {}
    for asset in ("BTC", "ETH", "XRP"):
        s = fetch_spot_via_rest(asset)
        if s is not None:
            spots[asset] = s
            log.info(f"spot {asset} = ${s:.2f}")

    for series in TARGET_SERIES:
        asset = SERIES_TO_ASSET[series]
        spot = spots.get(asset)
        if spot is None:
            continue
        markets = fetch_open_markets(client, series)
        atm_markets = strategy._filter_atm(
            markets, spot=spot, asset=asset,
            n=cfg.CRYPTO_FAIR_ATM_WINDOW_N,
        )
        for m in atm_markets:
            ticker = m.get("ticker", "")
            strike = strategy._extract_strike(m)
            if strike is None:
                continue
            secs = strategy._time_to_close_secs(m, now=now)
            if secs < cfg.CRYPTO_FAIR_MIN_TIME_TO_CLOSE_SECS:
                summary[f"{series}/skip_window"] += 1
                continue

            # Vol from Deribit (BTC/ETH) or skip XRP for --once mode
            vol = None
            vol_source = None
            if asset in ("BTC", "ETH"):
                iv_row = await db.fetch_nearest_crypto_iv(asset)
                if iv_row:
                    vol = float(iv_row["atm_iv"])
                    vol_source = "deribit"
            if vol is None:
                summary[f"{series}/no_vol"] += 1
                continue

            tau_years = secs / (365.25 * 86400)
            fair = lognormal_cdf_above(spot=spot, strike=strike, vol=vol, tau_years=tau_years)
            if fair is None:
                continue

            yes_price, no_price = client.get_market_prices(ticker)
            if yes_price is None or no_price is None:
                continue
            implied = (yes_price + (1.0 - no_price)) / 2.0
            deviation = fair - implied

            if abs(deviation) < cfg.CRYPTO_FAIR_MIN_DEVIATION:
                action = "skip_min_deviation"
                side = None
                size = None
            else:
                fee = cfg.KALSHI_FEE_PER_CONTRACT
                if deviation > 0:
                    side = "yes"
                    ev = fair - yes_price - fee
                else:
                    side = "no"
                    ev = (1.0 - fair) - no_price - fee
                if ev <= 0:
                    action = "skip_ev"
                    side = None
                    size = None
                else:
                    action = "fired"
                    size = strategy._size_for_edge(deviation)

            await db.record_crypto_fair_value_signal(
                ticker=ticker, asset=asset, spot=spot, strike=strike,
                vol=vol, vol_source=vol_source or "unknown",
                time_to_close_secs=int(secs),
                fair_prob=fair, kalshi_implied=implied,
                deviation=deviation, side=side,
                expected_size_usdc=size, action_taken=action,
            )
            summary[f"{series}/{action}"] += 1
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

    log.info(f"Starting crypto_fair_value scanner — interval={args.interval}s")
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
        await asyncio.sleep(max(5.0, args.interval - (time.time() - t0)))


if __name__ == "__main__":
    asyncio.run(main())
