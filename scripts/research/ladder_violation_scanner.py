"""Ladder violation prevalence scanner — Phase 0 of A1.

Polls KXBTC/KXETH/KXFEDDECISION/KXTRUMPAPPROVAL*/KX538APPROVE*/
KXNUMSHUTDOWNS/KXGOVTSHUTLENGTH every minute, runs framework detection
WITHOUT firing trades, logs every detected inversion to
ladder_violations table for offline analysis.

Run for ≥2 weeks before deciding which sub-strategies pass Phase 0
gate (≥1 violation/day + simulated ROI ≥ +1% arb / +3% fade).

Usage:
  python scripts/research/ladder_violation_scanner.py --interval 60 --once
  python scripts/research/ladder_violation_scanner.py --interval 60
"""
import argparse
import asyncio
import logging
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.config import Config
from core.database import Database
from core.kalshi_client import KalshiClient
from core.ladder_framework import (
    group_legs_into_ladders,
    find_arb_opportunities,
    find_fade_opportunities,
)
from strategies.ladder_crypto import LadderCryptoStrategy
from strategies.ladder_fed import LadderFedStrategy
from strategies.ladder_approval import LadderApprovalStrategy
from strategies.ladder_shutdown import LadderShutdownStrategy

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("ladder_scanner")

# Each entry: (sub_strategy_name, target_series, parser_strategy_class,
#              ladder_type, direction)
SCANNERS = [
    ("ladder_crypto", ("KXBTC", "KXETH"), LadderCryptoStrategy, "cumulative", "asc"),
    ("ladder_fed", ("KXFEDDECISION",), LadderFedStrategy, "bucketed", "asc"),
    ("ladder_approval",
     ("KXTRUMPAPPROVALYEAR", "KXTRUMPAPPROVALBELOW", "KX538APPROVE",
      "KX538APPROVEMAX", "KX538APPROVEMIN", "KX538APPROVEY"),
     LadderApprovalStrategy, "cumulative", "mixed"),  # asc/desc partitioned per series
    ("ladder_shutdown", ("KXNUMSHUTDOWNS", "KXGOVTSHUTLENGTH"),
     LadderShutdownStrategy, "cumulative", "asc"),
]


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


def normalize_market(m: dict) -> dict:
    """Convert Kalshi cents → dollars for framework consumption."""
    return {
        **m,
        "yes_ask": (m.get("yes_ask") or 0) / 100.0,
        "yes_bid": (m.get("yes_bid") or 0) / 100.0,
        "no_ask": (m.get("no_ask") or 0) / 100.0,
        "no_bid": (m.get("no_bid") or 0) / 100.0,
        # Phase 0: depth probe omitted (orderbook calls are expensive at
        # scan cadence). depth=999 to bypass framework's depth gate
        # — we still log inversion magnitude for offline depth backfill.
        "yes_depth": 999.0, "no_depth": 999.0,
    }


async def scan_once(client: KalshiClient, db: Database, cfg: Config) -> dict:
    summary = defaultdict(int)
    for entry in SCANNERS:
        sub_name, series_list, parser_cls, ladder_type, direction = entry
        # Build a parser instance to reuse static parser methods
        parser = parser_cls(cfg, None, None, None)
        for series in series_list:
            raw = fetch_open_markets(client, series)
            if not raw:
                continue
            normalized = [normalize_market(m) for m in raw]
            # Approval has mixed direction — partition per series
            if direction == "mixed":
                d = parser._direction_for(series)  # type: ignore[attr-defined]
            else:
                d = direction
            groups = group_legs_into_ladders(
                normalized, suffix_parser=parser._parse_suffix,
                ladder_type=ladder_type,
                event_key_extractor=parser._extract_event_key,
                direction=d,
            )
            for g in groups:
                arb = find_arb_opportunities(
                    g, fee=cfg.KALSHI_FEE_PER_CONTRACT,
                    min_edge_usdc=cfg.LADDER_MIN_ARB_EDGE_USDC,
                    min_depth_usdc=0.0,  # Phase 0: ignore depth gate
                    max_position_usdc=cfg.LADDER_MAX_POSITION_PER_GROUP,
                )
                if arb:
                    for t in arb:
                        a, b = (t.legs[0].ticker,
                                t.legs[1].ticker if len(t.legs) > 1 else None)
                        await db.record_ladder_violation(
                            series=g.series, event_key=g.event_key,
                            ladder_type=ladder_type,
                            leg_a_ticker=a, leg_b_ticker=b,
                            inversion_amount=t.expected_edge_usdc,
                            leg_a_depth=None, leg_b_depth=None,
                            action_taken="arb", sub_strategy=sub_name,
                        )
                        summary[f"{sub_name}/arb"] += 1
                else:
                    fade = find_fade_opportunities(
                        g, fee=cfg.KALSHI_FEE_PER_CONTRACT,
                        min_inversion=cfg.LADDER_MIN_FADE_INVERSION,
                        max_size_usdc=cfg.LADDER_MAX_POSITION_PER_GROUP / 2.5,
                    )
                    if fade:
                        for f in fade:
                            await db.record_ladder_violation(
                                series=g.series, event_key=g.event_key,
                                ladder_type=ladder_type,
                                leg_a_ticker=f.ticker, leg_b_ticker=None,
                                inversion_amount=f.inversion_amount,
                                leg_a_depth=None, leg_b_depth=None,
                                action_taken="fade", sub_strategy=sub_name,
                            )
                            summary[f"{sub_name}/fade"] += 1
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

    log.info(f"Starting ladder violation scanner — interval={args.interval}s")
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
        await asyncio.sleep(max(1, args.interval - (time.time() - t0)))


if __name__ == "__main__":
    asyncio.run(main())
