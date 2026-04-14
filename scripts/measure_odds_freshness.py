"""Measure actual Odds API update frequency.

Polls every POLL_INTERVAL seconds across all (region, sport) combos used
by live trading, hashing each payload + capturing Pinnacle prices.
Afterwards, the analyze step reports how often each key's payload actually
changes in the wild — the real input to the TTL tuning decision.

Usage:
    python3 scripts/measure_odds_freshness.py [minutes]    # collect
    python3 scripts/measure_odds_freshness.py analyze      # report only

Expected cost: ~360 Odds API calls per hour of measurement.
"""
import asyncio
import hashlib
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.config import Config

REGIONS = ["us", "eu"]
SPORTS = ["basketball_nba", "baseball_mlb", "icehockey_nhl"]
POLL_INTERVAL_SEC = 60
DEFAULT_DURATION_MIN = 60
OUT_FILE = "data/odds_freshness_log.jsonl"


def summarize(data: list) -> dict:
    """Per-bookmaker price hashing so we can see which books move how often.

    payload_hash = full response hash (polluted by last_update timestamps,
                   use only as a sanity check, not a price signal)
    per_bm_hash = {bookmaker_key: hash_of_just_its_prices} — this is the
                   clean signal for "how often do THIS book's prices change"
    """
    payload_hash = hashlib.md5(
        json.dumps(data, sort_keys=True).encode()
    ).hexdigest()[:12]
    from collections import defaultdict
    per_bm_prices: dict = defaultdict(list)
    for game in data:
        for bm in game.get("bookmakers", []):
            bm_key = bm.get("key", "")
            if not bm_key:
                continue
            for market in bm.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for outcome in market.get("outcomes", []):
                    per_bm_prices[bm_key].append(
                        f"{game.get('home_team', '?')}-"
                        f"{outcome.get('name', '?')}:{outcome.get('price')}"
                    )
    per_bm_hash = {
        bm: hashlib.md5(";".join(sorted(prices)).encode()).hexdigest()[:8]
        for bm, prices in per_bm_prices.items()
    }
    return {
        "payload_hash": payload_hash,
        "pinnacle_hash": per_bm_hash.get("pinnacle", "none"),
        "per_bm_hash": per_bm_hash,
        "num_games": len(data),
        "num_bookmakers": len(per_bm_hash),
    }


async def collect(api_key: str, duration_min: int):
    os.makedirs("data", exist_ok=True)
    async with httpx.AsyncClient(timeout=15) as client:
        deadline = datetime.now(timezone.utc).timestamp() + duration_min * 60
        tick_num = 0
        while datetime.now(timezone.utc).timestamp() < deadline:
            tick_num += 1
            ts = datetime.now(timezone.utc).isoformat()
            for region in REGIONS:
                for sport in SPORTS:
                    try:
                        resp = await client.get(
                            f"https://api.the-odds-api.com/v4/sports/{sport}/odds",
                            params={
                                "apiKey": api_key,
                                "regions": region,
                                "markets": "h2h",
                                "oddsFormat": "decimal",
                            },
                        )
                        resp.raise_for_status()
                        data = resp.json()
                        rec = {
                            "ts": ts,
                            "tick": tick_num,
                            "region": region,
                            "sport": sport,
                            "remaining": resp.headers.get("x-requests-remaining", "?"),
                            **summarize(data),
                        }
                        with open(OUT_FILE, "a") as f:
                            f.write(json.dumps(rec) + "\n")
                    except Exception as e:
                        print(f"[ERR] {region}/{sport}: {e}", flush=True)
            print(f"[tick {tick_num}] {ts[11:19]} done. remaining: "
                  f"{resp.headers.get('x-requests-remaining', '?')}", flush=True)
            await asyncio.sleep(POLL_INTERVAL_SEC)


def analyze(log_file: str):
    if not os.path.exists(log_file):
        print(f"No log at {log_file}", file=sys.stderr)
        sys.exit(1)
    by_key: dict = defaultdict(list)
    with open(log_file) as f:
        for line in f:
            r = json.loads(line)
            by_key[(r["region"], r["sport"])].append(r)

    print()
    print("=" * 92)
    print(f"{'key':24} {'ticks':>6} {'payload(noisy)':>15} {'pinnacle':>10} "
          f"{'est.pinnacle.TTL':>18}")
    print("=" * 92)
    for key, records in sorted(by_key.items()):
        n = len(records)
        payload_changes = 0
        pinnacle_changes = 0
        prev_pl, prev_pn = None, None
        first_ts, last_ts = None, None
        for r in records:
            first_ts = first_ts or r["ts"]
            last_ts = r["ts"]
            if prev_pl is not None and r["payload_hash"] != prev_pl:
                payload_changes += 1
            if prev_pn is not None and r["pinnacle_hash"] != prev_pn:
                pinnacle_changes += 1
            prev_pl, prev_pn = r["payload_hash"], r["pinnacle_hash"]
        est_ttl = (f">{n * POLL_INTERVAL_SEC}" if pinnacle_changes == 0
                   else f"{_seconds_between(first_ts, last_ts) / pinnacle_changes:.0f}")
        print(f"{str(key):24} {n:>6} {payload_changes:>15} {pinnacle_changes:>10} "
              f"{est_ttl:>18}")

    # Per-bookmaker change rate (the real signal)
    print()
    print("=" * 92)
    print(f"Per-bookmaker price change rate (per sport, averaged over regions):")
    print("=" * 92)
    by_bm: dict = defaultdict(lambda: defaultdict(lambda: {"changes": 0, "seen": 0, "span": 0.0}))
    for (region, sport), records in sorted(by_key.items()):
        if not records:
            continue
        prev_per_bm = {}
        first_ts, last_ts = records[0]["ts"], records[-1]["ts"]
        span = _seconds_between(first_ts, last_ts)
        for r in records:
            for bm, h in (r.get("per_bm_hash") or {}).items():
                if bm in prev_per_bm:
                    by_bm[sport][bm]["seen"] += 1
                    if prev_per_bm[bm] != h:
                        by_bm[sport][bm]["changes"] += 1
                prev_per_bm[bm] = h
        for bm in by_bm[sport]:
            by_bm[sport][bm]["span"] = max(by_bm[sport][bm]["span"], span)

    for sport in sorted(by_bm):
        print(f"\n--- {sport} ---")
        bms = by_bm[sport]
        # Sort by changes descending
        ranked = sorted(bms.items(), key=lambda kv: -kv[1]["changes"])
        print(f"  {'bookmaker':25} {'seen':>6} {'changes':>8} {'change-rate':>12} "
              f"{'avg.gap(s)':>12}")
        for bm, stats in ranked[:12]:
            rate = stats["changes"] / max(1, stats["seen"])
            gap = (stats["span"] / stats["changes"]) if stats["changes"] else float("inf")
            gap_str = f"{gap:.0f}" if gap != float("inf") else "∞"
            print(f"  {bm:25} {stats['seen']:>6} {stats['changes']:>8} {rate:>11.1%} "
                  f"{gap_str:>12}")

    print()
    print("Interpretation:")
    print("  avg.gap(s) = average seconds between this book's price changes.")
    print("  If a book's avg gap ≤ 300s → TTL 300s captures every other move.")
    print("  If ≥ 900s → current TTL is already enough for that book.")


def _seconds_between(iso_a: str, iso_b: str) -> float:
    a = datetime.fromisoformat(iso_a)
    b = datetime.fromisoformat(iso_b)
    return (b - a).total_seconds()


if __name__ == "__main__":
    cfg = Config()
    if not cfg.ODDS_API_KEY:
        print("ODDS_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    if len(sys.argv) > 1 and sys.argv[1] == "analyze":
        analyze(OUT_FILE)
    else:
        minutes = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DURATION_MIN
        print(f"Measuring Odds API for {minutes} min, poll every {POLL_INTERVAL_SEC}s, "
              f"{len(REGIONS) * len(SPORTS)} keys per tick...", flush=True)
        asyncio.run(collect(cfg.ODDS_API_KEY, minutes))
        print("\n--- Analysis ---")
        analyze(OUT_FILE)
