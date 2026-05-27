"""Per-strategy sim reliability check — wraps core/sim_reliability.assess()
with DB queries so you can run it against any strategy in the trades table.

Usage:
  venv/bin/python scripts/check_sim_reliability.py \\
      --strategy sports_tennis_pinnacle_fade_v1

  venv/bin/python scripts/check_sim_reliability.py --all-paper

Outputs Markdown to stdout (and optionally to a file). Designed for use
in eval scripts and as a manual gate before paper→LIVE promotion.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.sim_reliability import assess, format_result


DB_PATH_DEFAULT = "/home/ubuntu/kalshi-arb/data/bot.db"


def fetch_entries(conn: sqlite3.Connection, strategy: str,
                  status_in=("paper_settled",), days_back: int = 60) -> list:
    """Pull settled trades for `strategy` and convert to assess() format."""
    placeholders = ",".join("?" for _ in status_in)
    rows = conn.execute(
        f"""SELECT timestamp, price, pnl, size, status
            FROM trades
            WHERE strategy = ?
              AND status IN ({placeholders})
              AND timestamp >= datetime('now', ?)
            ORDER BY timestamp""",
        (strategy, *status_in, f"-{days_back} days"),
    ).fetchall()
    # Convert to per-$1 pnl (divide by size).
    entries = []
    for ts, price, pnl, size, status in rows:
        if size is None or size == 0:
            continue
        pnl_per_dollar = pnl / size if size else 0
        entries.append({
            "ts": ts, "price": price, "pnl": pnl_per_dollar,
            "raw_pnl": pnl, "size": size, "status": status,
        })
    return entries


def fetch_live_paper_gap_pp(conn: sqlite3.Connection, strategy: str,
                             days_back: int = 30) -> float | None:
    """For LIVE strategies, compute ROI gap (LIVE − PAPER twin) in pp.

    Returns None if strategy has no LIVE fires (paper-only deploy).
    """
    rows = conn.execute(
        """SELECT
             SUM(CASE WHEN status NOT LIKE 'paper%' AND status IN ('settled','filled') THEN size ELSE 0 END) AS live_vol,
             SUM(CASE WHEN status NOT LIKE 'paper%' AND status = 'settled' THEN pnl ELSE 0 END) AS live_pnl,
             SUM(CASE WHEN status LIKE 'paper%' THEN size ELSE 0 END) AS paper_vol,
             SUM(CASE WHEN status = 'paper_settled' THEN pnl ELSE 0 END) AS paper_pnl
           FROM trades
           WHERE strategy = ?
             AND timestamp >= datetime('now', ?)""",
        (strategy, f"-{days_back} days"),
    ).fetchone()
    if not rows:
        return None
    live_vol, live_pnl, paper_vol, paper_pnl = rows
    if not live_vol or not paper_vol:
        return None
    live_roi_pct = 100 * (live_pnl or 0) / live_vol
    paper_roi_pct = 100 * (paper_pnl or 0) / paper_vol
    return live_roi_pct - paper_roi_pct


def fetch_stale_ratio(conn: sqlite3.Connection, strategy: str,
                       days_back: int = 60) -> float | None:
    """Compute paper_stale / (paper_stale + paper_settled) ratio.

    High stale ratio = hidden losses (entries that never settled, possibly
    because the market closed under conditions that paper-sim doesn't model).
    """
    row = conn.execute(
        """SELECT
             SUM(CASE WHEN status='paper_stale' THEN 1 ELSE 0 END) AS stale,
             SUM(CASE WHEN status='paper_settled' THEN 1 ELSE 0 END) AS settled
           FROM trades
           WHERE strategy = ?
             AND timestamp >= datetime('now', ?)""",
        (strategy, f"-{days_back} days"),
    ).fetchone()
    if not row or (row[0] or 0) + (row[1] or 0) == 0:
        return None
    stale, settled = row[0] or 0, row[1] or 0
    return stale / (stale + settled)


def fetch_deploy_age(conn: sqlite3.Connection, strategy: str) -> int | None:
    row = conn.execute(
        """SELECT MIN(timestamp) FROM trades WHERE strategy = ?""",
        (strategy,),
    ).fetchone()
    if not row or not row[0]:
        return None
    try:
        clean = row[0].replace("Z", "").split("+")[0].split(".")[0]
        first = datetime.fromisoformat(clean).replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return int((now - first).total_seconds() / 86400)
    except ValueError:
        return None


def check_strategy(conn: sqlite3.Connection, strategy: str,
                   days_back: int = 60) -> dict:
    """Pull DB data + run assess()."""
    entries = fetch_entries(conn, strategy, days_back=days_back)
    gap_pp = fetch_live_paper_gap_pp(conn, strategy, days_back=min(30, days_back))
    deploy_age = fetch_deploy_age(conn, strategy)
    stale_ratio = fetch_stale_ratio(conn, strategy, days_back=days_back)

    extra_notes = []
    if gap_pp is not None:
        extra_notes.append(f"LIVE-PAPER twin gap: {gap_pp:+.1f}pp ROI (30d)")
    if deploy_age is not None:
        extra_notes.append(f"Deploy age: {deploy_age}d (first trade ever)")
    if stale_ratio is not None and stale_ratio > 0:
        extra_notes.append(f"Stale ratio: {stale_ratio:.1%} of paper rows never settled")

    result = assess(
        entries=entries,
        has_paper_twin_gap_pp=gap_pp,
        deploy_age_days=deploy_age,
        stale_ratio=stale_ratio,
        extra_notes=extra_notes,
    )
    result["strategy"] = strategy
    result["entry_count"] = len(entries)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", default=None,
                        help="strategy name (omit with --all-paper)")
    parser.add_argument("--all-paper", action="store_true",
                        help="scan all paper-only strategies")
    parser.add_argument("--db", default=DB_PATH_DEFAULT)
    parser.add_argument("--days-back", type=int, default=60)
    parser.add_argument("--out", default=None, help="write Markdown report to file")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"DB not found: {args.db}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA query_only = 1")
    conn.execute("PRAGMA busy_timeout = 30000")

    targets = []
    if args.strategy:
        targets = [args.strategy]
    elif args.all_paper:
        # Strategies with paper activity in last 30d
        rows = conn.execute(
            """SELECT strategy, COUNT(*) FROM trades
               WHERE status = 'paper_settled'
                 AND timestamp >= datetime('now','-30 days')
               GROUP BY strategy HAVING COUNT(*) >= 5
               ORDER BY strategy"""
        ).fetchall()
        targets = [r[0] for r in rows]
    else:
        print("Error: provide --strategy NAME or --all-paper", file=sys.stderr)
        sys.exit(1)

    output_lines = [
        f"# Sim Reliability Report — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]
    for strat in targets:
        result = check_strategy(conn, strat, days_back=args.days_back)
        output_lines.append(f"# {strat}")
        output_lines.append(f"_Entries: n={result['entry_count']} (last {args.days_back}d settled)_")
        output_lines.append("")
        output_lines.append(format_result(result))
        output_lines.append("")
        output_lines.append("---")
        output_lines.append("")

    out = "\n".join(output_lines)
    if args.out:
        with open(args.out, "w") as f:
            f.write(out)
        print(f"Wrote {args.out}")
    print(out)


if __name__ == "__main__":
    main()
