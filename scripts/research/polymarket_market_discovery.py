"""Polymarket market discovery — surfaces gamma-api active markets that
correspond to the B1 xvenue_polymarket target universe.

The v0 mapping JSON used best-guess slugs that don't exist on Polymarket
(verified live 2026-05-11 — all 19 returned 'not parsed'). This script
queries gamma-api for active markets, filters by question-text keyword
buckets that match what Kalshi lists (FOMC, CPI, recession, election,
BTC/ETH strikes, Trump approval, etc), and prints candidate mapping
entries for the user to verify against the real polymarket.com URLs.

Usage:
  python scripts/research/polymarket_market_discovery.py
  python scripts/research/polymarket_market_discovery.py --limit 1000
"""
import argparse
import json
import re
import sys
from collections import OrderedDict
from typing import Iterable

import httpx

GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"

# Category buckets — each is (label, regex pattern). Markets whose
# question text matches the pattern are surfaced under that bucket.
# Order matters: a market falls into the first matching bucket.
CATEGORY_PATTERNS = [
    ("fomc_fed_decision", r"\b(fed|fomc).{0,30}(decision|cut|hike|hold|rate)|interest rate.{0,20}(june|jul|aug|sep|oct|nov|dec)"),
    ("fed_chair", r"\bfed chair|federal reserve chair\b"),
    ("cpi", r"\bcpi\b|consumer price index|inflation.{0,20}(report|reading|release)"),
    ("recession", r"\brecession\b"),
    ("trump_approval", r"trump.{0,20}approval"),
    ("election_2028", r"\b2028\b.{0,40}(president|election|win)|win.{0,30}2028"),
    ("election_vp_2028", r"\b(vp|vice president).{0,30}2028|2028.{0,30}vp"),
    ("btc_strike", r"\b(bitcoin|btc).{0,20}(hit|reach|above|over|by).{0,30}\$?\d{2,3}[k,]?"),
    ("eth_strike", r"\b(ethereum|eth).{0,20}(hit|reach|above|over|by).{0,30}\$?\d{2,3}"),
    ("xrp_strike", r"\bxrp\b.{0,30}\$?\d"),
    ("sp500", r"s.?p.?500|s&p\s*500"),
    ("nasdaq", r"\bnasdaq\b"),
    ("oil", r"\b(oil|wti|brent).{0,20}\$?\d{2,3}"),
    ("ukraine_russia", r"ukraine.{0,30}russia|russia.{0,30}ukraine|ceasefire"),
    ("china_taiwan", r"china.{0,30}taiwan|taiwan.{0,30}china"),
    ("government_shutdown", r"government.{0,10}shutdown"),
    ("scotus", r"supreme court|scotus"),
]


def fetch_active_markets(limit: int = 500) -> list[dict]:
    """Pull `limit` active+open markets from gamma-api, sorted by 24h volume."""
    params = {
        "active": "true",
        "closed": "false",
        "limit": str(limit),
        "order": "volume24hr",
        "ascending": "false",
    }
    headers = {"User-Agent": "Mozilla/5.0"}
    response = httpx.get(GAMMA_MARKETS_URL, params=params, headers=headers, timeout=30)
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, list) else data.get("data", [])


def categorize(markets: Iterable[dict]) -> dict[str, list[dict]]:
    """Bucket markets by category. Each market lands in the first matching
    bucket — no double counting."""
    buckets: dict[str, list[dict]] = OrderedDict((label, []) for label, _ in CATEGORY_PATTERNS)
    for m in markets:
        q = (m.get("question") or "").lower()
        if not q:
            continue
        for label, pattern in CATEGORY_PATTERNS:
            if re.search(pattern, q):
                buckets[label].append(m)
                break
    return buckets


def format_mapping_entry(market: dict, kalshi_prefix_hint: str = "TODO") -> dict:
    """Return a mapping-JSON-ready entry the user can paste after verifying
    the kalshi_ticker_prefix."""
    return {
        "polymarket_slug": market.get("slug", ""),
        "kalshi_ticker_prefix": kalshi_prefix_hint,
        "outcome_match": "direct",
        "notes": (market.get("question") or "")[:120],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=500,
                        help="Max active markets to scan (gamma-api page size)")
    parser.add_argument("--json", action="store_true",
                        help="Print as a single JSON array of mapping candidates")
    args = parser.parse_args()

    print(f"Fetching up to {args.limit} active markets from gamma-api...", file=sys.stderr)
    markets = fetch_active_markets(args.limit)
    print(f"Got {len(markets)} markets. Categorizing...", file=sys.stderr)

    buckets = categorize(markets)
    matched = sum(len(v) for v in buckets.values())
    print(f"Matched {matched}/{len(markets)} into target categories.", file=sys.stderr)
    print(file=sys.stderr)

    if args.json:
        candidates = []
        for label, items in buckets.items():
            for m in items:
                candidates.append(format_mapping_entry(m, kalshi_prefix_hint=f"TODO_{label}"))
        print(json.dumps(candidates, indent=2))
        return

    # Human-readable bucketed report
    for label, items in buckets.items():
        if not items:
            continue
        print(f"\n=== {label} ({len(items)}) ===")
        for m in items:
            slug = m.get("slug", "?")
            q = (m.get("question") or "?")[:80]
            vol = float(m.get("volume24hr") or 0)
            print(f"  {vol:>10.0f}  {slug:55}  {q}")


if __name__ == "__main__":
    main()
