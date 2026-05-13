"""Backfill actual_outcome on crypto_observer_signals.

For each crypto_observer_signals row with actual_outcome IS NULL whose
market_id has a finalized Kalshi market, copy the resolution into
actual_outcome ∈ {'yes', 'no'} and stamp resolution_ts.

Pulls from ticker_settlements (populated by backfill_ticker_settlements
or via this script's --include-fetch flag) to avoid duplicate API hits.
With --include-fetch, also calls Kalshi for any market_id not yet in
ticker_settlements.

Run from cron every ~hour. Run with --dry-run first to preview.

Usage:
    venv/bin/python scripts/crypto_observer_settlement_backfill.py
    venv/bin/python scripts/crypto_observer_settlement_backfill.py --include-fetch
    venv/bin/python scripts/crypto_observer_settlement_backfill.py --dry-run
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

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("crypto_observer_backfill")


def _safe_float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _result_to_outcome(result: str) -> str | None:
    """Map Kalshi result string to 'yes' / 'no'."""
    if not result:
        return None
    r = result.lower()
    if r in ("yes",):
        return "yes"
    if r in ("no",):
        return "no"
    # "void" or other → leave unresolved
    return None


def fetch_for_unsettled(conn: sqlite3.Connection, client: KalshiClient,
                       limit: int | None) -> int:
    """Populate ticker_settlements for any market_id in
    crypto_observer_signals that has no settlement row yet."""
    cur = conn.execute(
        """
        SELECT DISTINCT s.market_id
        FROM crypto_observer_signals s
        LEFT JOIN ticker_settlements t ON s.market_id = t.kalshi_ticker
        WHERE s.actual_outcome IS NULL AND t.kalshi_ticker IS NULL
        ORDER BY s.market_id
        """ + (f" LIMIT {int(limit)}" if limit else "")
    )
    tickers = [r[0] for r in cur.fetchall()]
    if not tickers:
        return 0
    logger.info(f"Fetching settlement for {len(tickers)} new tickers")
    now_iso = datetime.now(timezone.utc).isoformat()
    inserted = 0
    for i, ticker in enumerate(tickers, 1):
        try:
            m = client.get_market(ticker)
        except Exception as e:
            logger.warning(f"  [{i}/{len(tickers)}] {ticker}: get_market error {e}")
            continue
        if not m:
            continue
        if m.get("status") not in ("finalized", "settled"):
            continue
        result = m.get("result")
        if not result:
            continue
        conn.execute(
            """INSERT OR IGNORE INTO ticker_settlements
               (kalshi_ticker, result, close_yes_ask, close_no_ask,
                settled_at, fetched_at) VALUES (?, ?, ?, ?, ?, ?)""",
            (ticker, result,
             _safe_float(m.get("yes_ask_dollars")),
             _safe_float(m.get("no_ask_dollars")),
             m.get("expected_expiration_time") or m.get("close_time"),
             now_iso),
        )
        inserted += 1
        if i % 50 == 0:
            conn.commit()
            logger.info(f"  progress: {i}/{len(tickers)}")
    conn.commit()
    return inserted


def backfill_outcomes(conn: sqlite3.Connection, dry_run: bool) -> dict:
    """Join unsettled signals to ticker_settlements; copy result."""
    cur = conn.execute(
        """
        SELECT s.id, s.market_id, t.result, t.settled_at
        FROM crypto_observer_signals s
        JOIN ticker_settlements t ON s.market_id = t.kalshi_ticker
        WHERE s.actual_outcome IS NULL
        """
    )
    rows = cur.fetchall()
    counts = {"updated": 0, "void": 0}
    now_iso = datetime.now(timezone.utc).isoformat()
    for row in rows:
        sig_id, _market, result, settled_at = row
        outcome = _result_to_outcome(result)
        if outcome is None:
            counts["void"] += 1
            continue
        if dry_run:
            logger.info(f"  [dry-run] signal#{sig_id} → {outcome}")
        else:
            conn.execute(
                "UPDATE crypto_observer_signals "
                "SET actual_outcome = ?, resolution_ts = ? "
                "WHERE id = ?",
                (outcome, settled_at or now_iso, sig_id),
            )
        counts["updated"] += 1
    if not dry_run:
        conn.commit()
    return counts


def summarize(conn: sqlite3.Connection):
    """Per-observer WR + sample size — quick health check."""
    cur = conn.execute(
        """
        SELECT observer,
               COUNT(*) AS n,
               SUM(CASE WHEN actual_outcome IS NULL THEN 1 ELSE 0 END) AS open,
               SUM(CASE WHEN actual_outcome = predicted_outcome THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN actual_outcome IS NOT NULL THEN 1 ELSE 0 END) AS settled
        FROM crypto_observer_signals
        GROUP BY observer ORDER BY n DESC
        """
    )
    rows = cur.fetchall()
    if not rows:
        logger.info("No signals recorded yet.")
        return
    logger.info("Per-observer status:")
    logger.info(f"  {'observer':25s} {'n':>5} {'settled':>7} {'wins':>5} {'WR':>6}")
    for observer, n, open_, wins, settled in rows:
        wr = (100.0 * wins / settled) if settled else 0.0
        logger.info(f"  {observer:25s} {n:5d} {settled:7d} {wins:5d} {wr:5.1f}%")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/bot.db")
    parser.add_argument("--include-fetch", action="store_true",
                        help="Also call Kalshi API for tickers missing from "
                             "ticker_settlements (slower).")
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap on API fetches per run.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show updates but don't write.")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db, timeout=30.0)
    if args.include_fetch and not args.dry_run:
        cfg = Config()
        client = KalshiClient(cfg)
        n = fetch_for_unsettled(conn, client, args.limit)
        logger.info(f"Fetched {n} new settlements")
    counts = backfill_outcomes(conn, args.dry_run)
    logger.info(f"Backfill: {counts}")
    summarize(conn)
    conn.close()


if __name__ == "__main__":
    main()
