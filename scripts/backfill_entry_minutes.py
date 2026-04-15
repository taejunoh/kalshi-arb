"""One-shot backfill: populate trades.minutes_to_start for existing sports trades.

Sources:
- MLB: ticker parse (exact, e.g. KXMLBGAME-26APR102140TEX-TEX -> 2026-04-10 21:40 ET).
- NBA/NHL: Kalshi get_market(ticker).close_time - nominal_duration.

Re-run safe: only updates rows where minutes_to_start IS NULL.
"""
from __future__ import annotations
import argparse
import logging
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")
from core.config import Config
from core.kalshi_client import KalshiClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_entry_minutes")

MONTH = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,"JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}
NOMINAL_DURATION = {
    "KXNBA": timedelta(hours=2, minutes=30),
    "KXNHL": timedelta(hours=2, minutes=40),
}


def parse_mlb_start_utc(ticker: str) -> datetime | None:
    m = re.search(r"KXMLBGAME-(\d{2})([A-Z]{3})(\d{2})(\d{2})(\d{2})", ticker or "")
    if not m:
        return None
    yy, mon, dd, hh, mm = m.groups()
    try:
        # MLB ticker time is America/New_York (EDT/EST). Use EDT offset for
        # April-November. Acceptable +/-1h error doesn't affect pre/live classification.
        return datetime(2000+int(yy), MONTH[mon], int(dd), int(hh), int(mm),
                       tzinfo=timezone.utc) + timedelta(hours=4)
    except (ValueError, KeyError):
        return None


def resolve_start_utc(ticker: str, client: KalshiClient) -> datetime | None:
    s = parse_mlb_start_utc(ticker)
    if s is not None:
        return s
    prefix = ticker[:5]  # "KXNBA" / "KXNHL"
    dur = NOMINAL_DURATION.get(prefix)
    if dur is None:
        return None
    try:
        md = client.get_market(ticker)
        if not md:
            return None
        ct = md.get("close_time")
        if not ct:
            return None
        close = datetime.fromisoformat(ct.replace("Z", "+00:00"))
        return close - dur
    except Exception as e:
        logger.warning(f"get_market failed for {ticker}: {e}")
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/bot.db")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    config = Config()
    client = KalshiClient(config)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT id, timestamp, market_id FROM trades
           WHERE strategy LIKE 'sports_%' AND minutes_to_start IS NULL"""
    ).fetchall()
    logger.info(f"candidates: {len(rows)}")

    start_cache: dict[str, datetime | None] = {}
    updated = 0
    skipped = 0
    for r in rows:
        mkt = r["market_id"]
        if mkt not in start_cache:
            start_cache[mkt] = resolve_start_utc(mkt, client)
        start = start_cache[mkt]
        if start is None:
            skipped += 1
            continue
        trade_ts = datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00"))
        if trade_ts.tzinfo is None:
            trade_ts = trade_ts.replace(tzinfo=timezone.utc)
        minutes = (start - trade_ts).total_seconds() / 60.0
        if args.dry_run:
            logger.info(f"[DRY] trade {r['id']} {mkt} -> {minutes:+.1f} min")
        else:
            conn.execute(
                "UPDATE trades SET minutes_to_start=? WHERE id=?",
                (minutes, r["id"]),
            )
        updated += 1

    if not args.dry_run:
        conn.commit()
    conn.close()
    logger.info(f"updated: {updated}  skipped: {skipped}  unique_markets: {len(start_cache)}")


if __name__ == "__main__":
    main()
