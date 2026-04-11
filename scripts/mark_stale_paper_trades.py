"""Mark stale paper_filled trades that should not participate in settlement.

Context (2026-04-10):
spread_scanner V1 is marked as a failed strategy (see memory:
strategy_spread_scanner_analysis). Its pending paper_filled trades from
2026-03-26~27 are legacy bets against Fed/CPI/GDP macro events that will
settle over the next several weeks with mostly negative expected value.

After fixing the settlement bankroll logic (core/settlement.py), losing
settlements no longer drain bankroll — but they still clutter the
settlement loop, get telegram notifications, and bias win/loss stats.
Marking them as `paper_stale` excludes them from `get_unsettled_trades()`
without deleting the rows (evidence preservation).

Usage:
    python3 scripts/mark_stale_paper_trades.py --dry-run   # preview
    python3 scripts/mark_stale_paper_trades.py --execute   # apply

This script is idempotent — re-running it has no effect once trades are
marked stale.
"""
import argparse
import sqlite3
import sys
from pathlib import Path


STALE_STRATEGIES = {"spread_scanner"}
DB_PATH = Path(__file__).parent.parent / "data" / "bot.db"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Preview changes")
    group.add_argument("--execute", action="store_true", help="Apply changes")
    parser.add_argument("--db", default=str(DB_PATH), help="Path to bot.db")
    args = parser.parse_args()

    con = sqlite3.connect(args.db)
    cur = con.cursor()

    placeholders = ",".join("?" for _ in STALE_STRATEGIES)
    target_query = f"""
        SELECT id, strategy, timestamp, market_id, direction, price, size
        FROM trades
        WHERE status = 'paper_filled'
          AND strategy IN ({placeholders})
        ORDER BY strategy, timestamp
    """
    rows = list(cur.execute(target_query, tuple(STALE_STRATEGIES)))

    if not rows:
        print("No matching paper_filled trades found. Nothing to do.")
        return 0

    print(f"Found {len(rows)} paper_filled trades to mark stale:")
    by_strategy: dict[str, tuple[int, float]] = {}
    for _id, strat, ts, mkt, direction, price, size in rows:
        n, total = by_strategy.get(strat, (0, 0.0))
        by_strategy[strat] = (n + 1, total + (size or 0))
    for strat, (n, total) in sorted(by_strategy.items()):
        print(f"  {strat:20} {n:4} trades, total size ${total:,.2f}")

    if args.dry_run:
        print("\n(dry-run) First 5 rows that WOULD be marked stale:")
        for _id, strat, ts, mkt, direction, price, size in rows[:5]:
            print(f"  id={_id} {strat} {ts[:19]} {mkt[:30]:30} {direction} @{price:.2f} ${size:.2f}")
        print("\nRun with --execute to apply.")
        return 0

    update_query = f"""
        UPDATE trades
        SET status = 'paper_stale'
        WHERE status = 'paper_filled'
          AND strategy IN ({placeholders})
    """
    cur.execute(update_query, tuple(STALE_STRATEGIES))
    con.commit()
    print(f"\n✓ Marked {cur.rowcount} trades as paper_stale.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
