"""Daily Telegram summary of ladder_crypto Phase 0 prevalence.

Counts walked-violation candidates in the last 24h, the 14d cumulative
gate progress, and phantom-rate (p90 of top_vs_walked_max_gap_cents).
Triggers Phase 1 promotion alarm when 14d would_fire count >= 10.

CLI: python3 scripts/ladder_crypto_phase0_summary.py [--db PATH] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ladder_phase0_summary")

PHASE1_GATE_14D_COUNT = 10  # Spec decision tree


def _query_summary(db_path: str) -> dict:
    """Return dict with 24h + 14d counts and phantom percentile."""
    conn = sqlite3.connect(db_path, timeout=30.0)
    cur = conn.cursor()
    # Verify schema is migrated (phase column must exist)
    cols = {r[1] for r in cur.execute("PRAGMA table_info(ladder_violations)").fetchall()}
    if "phase" not in cols:
        conn.close()
        raise RuntimeError(
            "ladder_violations schema not migrated: 'phase' column missing. "
            "Run the Task 2 migration on this DB first."
        )
    # 24h would_fire count, split by series
    cur.execute("""
        SELECT series, COUNT(*) FROM ladder_violations
        WHERE sub_strategy='ladder_crypto' AND phase='phase0_paper'
          AND would_fire=1 AND ts >= datetime('now', '-1 days')
        GROUP BY series
    """)
    by_series_24h = dict(cur.fetchall())
    # 24h avg walked_edge
    cur.execute("""
        SELECT AVG(walked_edge_usdc) FROM ladder_violations
        WHERE sub_strategy='ladder_crypto' AND phase='phase0_paper'
          AND would_fire=1 AND ts >= datetime('now', '-1 days')
    """)
    avg_edge_24h = cur.fetchone()[0]
    # 14d cumulative would_fire count
    cur.execute("""
        SELECT COUNT(*) FROM ladder_violations
        WHERE sub_strategy='ladder_crypto' AND phase='phase0_paper'
          AND would_fire=1 AND ts >= datetime('now', '-14 days')
    """)
    count_14d = cur.fetchone()[0]
    # Phantom rate: p90 of top_vs_walked_max_gap_cents on candidates (all detected, fire or not)
    cur.execute("""
        SELECT top_vs_walked_max_gap_cents FROM ladder_violations
        WHERE sub_strategy='ladder_crypto' AND phase='phase0_paper'
          AND top_vs_walked_max_gap_cents IS NOT NULL
          AND ts >= datetime('now', '-14 days')
        ORDER BY top_vs_walked_max_gap_cents
    """)
    gaps = [r[0] for r in cur.fetchall()]
    if gaps:
        p90_idx = int(len(gaps) * 0.9)
        gap_p90 = gaps[min(p90_idx, len(gaps) - 1)]
    else:
        gap_p90 = None
    # Total candidates (any fire status) in last 48h — early-abort signal
    cur.execute("""
        SELECT COUNT(*) FROM ladder_violations
        WHERE sub_strategy='ladder_crypto' AND phase='phase0_paper'
          AND ts >= datetime('now', '-2 days')
    """)
    candidates_48h = cur.fetchone()[0]
    conn.close()
    return {
        "by_series_24h": by_series_24h,
        "avg_edge_24h": avg_edge_24h,
        "count_14d": count_14d,
        "gap_p90": gap_p90,
        "candidates_48h": candidates_48h,
    }


def _format_message(s: dict) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    btc = s["by_series_24h"].get("KXBTC", 0)
    eth = s["by_series_24h"].get("KXETH", 0)
    avg_edge = s["avg_edge_24h"]
    avg_edge_str = f"${avg_edge:.3f}" if avg_edge is not None else "n/a"
    gap_p90_str = f"{s['gap_p90']:.1f}c" if s["gap_p90"] is not None else "n/a"
    gate_line = (
        f"  cumulative 14d: {s['count_14d']} / Phase 1 gate={PHASE1_GATE_14D_COUNT}"
    )
    if s["count_14d"] >= PHASE1_GATE_14D_COUNT:
        gate_line += " - PROMOTE CANDIDATE"
    lines = [
        f"ladder_crypto Phase 0 -- daily ({today})",
        f"  BTC would_fire 24h: {btc}",
        f"  ETH would_fire 24h: {eth}",
        f"  avg walked_edge 24h: {avg_edge_str}",
        f"  phantom p90 gap (14d): {gap_p90_str}",
        gate_line,
    ]
    # Early-abort flag
    if s["candidates_48h"] == 0:
        lines.append("  WARNING: 48h candidates=0 -- code-bug check required")
    return "\n".join(lines)


async def _send_telegram(message: str) -> None:
    from core.config import Config
    cfg = Config()
    if not cfg.TELEGRAM_BOT_TOKEN or not cfg.TELEGRAM_CHAT_ID:
        logger.warning("No TELEGRAM credentials -- printing to stdout instead.")
        print(message)
        return
    from monitoring.notifier import TelegramNotifier
    notifier = TelegramNotifier(cfg.TELEGRAM_BOT_TOKEN, cfg.TELEGRAM_CHAT_ID)
    await notifier.send(message, msg_type="ladder_phase0_summary")
    logger.info(f"Sent Telegram message, {len(message)} chars.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/bot.db")
    parser.add_argument("--dry-run", action="store_true", help="Print to stdout, skip Telegram")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        logger.error(f"DB not found: {args.db}")
        sys.exit(1)

    summary = _query_summary(args.db)
    message = _format_message(summary)
    logger.info(message)
    if args.dry_run:
        print(message)
        return
    asyncio.run(_send_telegram(message))


if __name__ == "__main__":
    main()
