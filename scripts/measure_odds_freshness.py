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
    payload_hash = hashlib.md5(
        json.dumps(data, sort_keys=True).encode()
    ).hexdigest()[:12]
    pinnacle = []
    for game in data:
        for bm in game.get("bookmakers", []):
            if bm.get("key") != "pinnacle":
                continue
            for market in bm.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for outcome in market.get("outcomes", []):
                    pinnacle.append(
                        f"{game.get('home_team', '?')}-"
                        f"{outcome.get('name', '?')}:{outcome.get('price')}"
                    )
    # Hash of just the Pinnacle section — lets us detect Pinnacle moves
    # separately from bulk book moves
    pinnacle_hash = hashlib.md5(
        ";".join(sorted(pinnacle)).encode()
    ).hexdigest()[:12]
    return {
        "payload_hash": payload_hash,
        "pinnacle_hash": pinnacle_hash,
        "num_games": len(data),
        "pinnacle_prices": len(pinnacle),
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
    print(f"{'key':22} {'ticks':>6} {'payload-changes':>16} {'pinnacle-changes':>18} "
          f"{'est.TTL (s)':>12}")
    print("=" * 92)
    for key, records in sorted(by_key.items()):
        n = len(records)
        payload_changes = 0
        pinnacle_changes = 0
        prev_pl, prev_pn = None, None
        first_ts = None
        last_ts = None
        for r in records:
            if first_ts is None:
                first_ts = r["ts"]
            last_ts = r["ts"]
            if prev_pl is not None and r["payload_hash"] != prev_pl:
                payload_changes += 1
            if prev_pn is not None and r["pinnacle_hash"] != prev_pn:
                pinnacle_changes += 1
            prev_pl, prev_pn = r["payload_hash"], r["pinnacle_hash"]

        # Estimated effective TTL: average seconds between payload changes
        if payload_changes == 0:
            est_ttl = f"> {n * POLL_INTERVAL_SEC}"
        else:
            span = _seconds_between(first_ts, last_ts)
            est_ttl = f"{span / payload_changes:.0f}"
        print(f"{str(key):22} {n:>6} {payload_changes:>16} {pinnacle_changes:>18} "
              f"{est_ttl:>12}")
    print()
    print("Interpretation:")
    print("  est.TTL ≈ seconds between payload hash changes.")
    print("  If est.TTL ≥ current TTL (900s) → current cache is not losing freshness.")
    print("  If est.TTL ≤ 60s → we're missing updates every cache hit; tighter TTL helps.")


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
