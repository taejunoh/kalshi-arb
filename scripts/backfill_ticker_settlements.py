"""Backfill ticker_settlements from Kalshi for tickers observed by scan observer.

Observer (odds_observations) captures every open sports market's price and
consensus per 60s cycle, but has no settlement outcome. This script fills
that gap by querying Kalshi `get_market(ticker)` for each unique ticker
the observer has seen, and records the result.

Re-run safe: only queries tickers that don't yet have a row in
ticker_settlements. Skips markets that haven't finalized yet.

Usage:
    venv/bin/python scripts/backfill_ticker_settlements.py
    venv/bin/python scripts/backfill_ticker_settlements.py --limit 50
    venv/bin/python scripts/backfill_ticker_settlements.py --dry-run
"""
from __future__ import annotations
import argparse
import logging
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")
from core.config import Config
from core.kalshi_client import KalshiClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_ticker_settlements")


def _safe_float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_settlements(db_path: str, client: KalshiClient, limit: int | None, dry_run: bool) -> dict:
    """Query Kalshi for tickers observed but not yet settled. Return counts."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        """
        SELECT DISTINCT o.kalshi_ticker
        FROM odds_observations o
        LEFT JOIN ticker_settlements s ON o.kalshi_ticker = s.kalshi_ticker
        WHERE s.kalshi_ticker IS NULL
        ORDER BY o.kalshi_ticker
        """
        + (f" LIMIT {int(limit)}" if limit else "")
    )
    tickers = [row["kalshi_ticker"] for row in cursor.fetchall()]
    logger.info(f"Found {len(tickers)} unsettled tickers to query")

    counts = {"inserted": 0, "pending": 0, "errors": 0, "no_result": 0}
    now = datetime.now(timezone.utc).isoformat()

    for i, ticker in enumerate(tickers, 1):
        try:
            m = client.get_market(ticker)
        except Exception as e:
            logger.warning(f"  [{i}/{len(tickers)}] {ticker}: get_market error {e}")
            counts["errors"] += 1
            continue

        if not m:
            counts["errors"] += 1
            continue

        status = m.get("status", "")
        if status not in ("finalized", "settled"):
            counts["pending"] += 1
            continue

        result = m.get("result")
        if not result:
            counts["no_result"] += 1
            continue

        yes_ask = _safe_float(m.get("yes_ask_dollars"))
        no_ask = _safe_float(m.get("no_ask_dollars"))
        settled_at = m.get("expected_expiration_time") or m.get("close_time")

        if dry_run:
            logger.info(
                f"  [{i}/{len(tickers)}] {ticker}: result={result} "
                f"yes_ask={yes_ask} no_ask={no_ask} (dry-run, not inserted)"
            )
        else:
            conn.execute(
                """INSERT OR IGNORE INTO ticker_settlements
                   (kalshi_ticker, result, close_yes_ask, close_no_ask,
                    settled_at, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (ticker, result, yes_ask, no_ask, settled_at, now),
            )
        counts["inserted"] += 1

        if i % 50 == 0:
            if not dry_run:
                conn.commit()
            logger.info(f"  progress: {i}/{len(tickers)}  {counts}")

    if not dry_run:
        conn.commit()
    conn.close()
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/bot.db", help="SQLite DB path")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limit number of tickers to query (for testing)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Query and log but don't write to DB")
    args = parser.parse_args()

    cfg = Config()
    client = KalshiClient(cfg)

    counts = fetch_settlements(args.db, client, args.limit, args.dry_run)
    logger.info(f"Done: {counts}")


if __name__ == "__main__":
    main()
