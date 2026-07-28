"""Portfolio performance — the methodologically honest version.

WHY THIS EXISTS
---------------
The standing audit playbook pulled 7d + 14d + 30d and read a stake-weighted
pooled ROI off them. On 2026-07-28 that produced a clean monotonic decline
(+9.8% -> -19.6%) that looked like the whole book was falling apart. It was not:

  * The three windows are NESTED. 7d is a subset of 14d is a subset of 30d, so
    "all three are negative" is one sample counted three times, never three
    independent confirmations.
  * The pool mixes strategies whose stakes differ ~10x. Three World Cup
    outright bets from xvenue_polymarket at ~$24 each carried -$44 of an -$85
    fortnight, drowning out hundreds of $2-4 sports bets.
  * The strategy SET changes week to week (22 -> 11 as the paper bankroll
    froze), so consecutive weeks are not like-for-like at all.

Holding the strategy set fixed made the trend vanish entirely: +8.6, -10.3,
-23.9, +23.3, +4.1, -13.0 — noise, with the worst week in the middle.

WHAT THIS REPORTS
  * disjoint weeks, never nested windows
  * stake-weighted AND equal-weighted side by side — their divergence IS the
    diagnostic, not a detail
  * a fixed basket (strategies present in every week) to control mix shift
  * concentration: the single largest trade's share of gross PnL, because at
    n=42 one trade can be the whole story
  * sports_* and everything else split apart — a cross-venue Polymarket
    outright does not belong in the same denominator as an NBA game bet

    venv/bin/python scripts/portfolio_performance.py
    venv/bin/python scripts/portfolio_performance.py --weeks 8 --book live
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_PATH = os.environ.get("KALSHI_DB", "data/bot.db")
BOOKS = {"paper": "paper_settled", "live": "settled"}
# Divergence beyond this between the two weightings means the headline is being
# set by stake concentration rather than by how the strategies actually did.
WEIGHTING_DIVERGENCE_PP = 10.0
# One trade owning this much of a week's gross PnL makes the week anecdote.
CONCENTRATION_FLAG = 0.25
# Minimum settled trades before a strategy gets a vote in the equal-weighted
# mean. Without this the mean is dominated by singletons: one strategy with a
# single -105% longshot loss would outvote another with twenty trades near
# breakeven, which is how equal-weighting stops fixing bias and starts adding
# it. Strategies below the bar still count in the stake-weighted number.
MIN_STRATEGY_N = 3


def load_rows(conn, *, status: str, weeks: int) -> list[tuple]:
    """(week_index, strategy, size, pnl); week 0 = most recent 7 days.

    Both sides of the comparison go through datetime() — ledger timestamps are
    ISO-T with an offset, and a raw string compare against datetime('now',...)
    silently misorders at the boundary (feedback_sqlite_timestamp_t_space).
    """
    return conn.execute(
        """SELECT CAST((julianday('now') - julianday(timestamp)) / 7 AS INT) AS wk,
                  strategy, size, pnl
             FROM trades
            WHERE status = ?
              AND datetime(timestamp) >= datetime('now', ?)
              AND size > 0""",
        (status, f"-{weeks * 7} days"),
    ).fetchall()


def _roi(pnl: float, stake: float) -> float | None:
    return 100.0 * pnl / stake if stake else None


def analyze(rows: list[tuple], *, weeks: int) -> dict:
    per_week: dict[int, dict[str, list]] = defaultdict(
        lambda: defaultdict(lambda: [0, 0.0, 0.0, 0, 0.0])
    )
    for wk, strategy, size, pnl in rows:
        if wk < 0 or wk >= weeks:
            continue
        s = per_week[wk][strategy]
        s[0] += 1
        s[1] += size
        s[2] += pnl
        s[3] += 1 if pnl > 0 else 0
        s[4] = max(s[4], abs(pnl))

    out = {"weeks": [], "basket": [], "common": [], "flags": []}
    if not per_week:
        return out

    for wk in sorted(per_week):
        strats = per_week[wk]
        n = sum(v[0] for v in strats.values())
        stake = sum(v[1] for v in strats.values())
        pnl = sum(v[2] for v in strats.values())
        wins = sum(v[3] for v in strats.values())
        gross = sum(abs(v[2]) for v in strats.values())
        biggest = max((v[4] for v in strats.values()), default=0.0)
        # One vote per strategy, so a single large-stake book cannot set the
        # headline — but only for strategies with enough trades to deserve a
        # vote (see MIN_STRATEGY_N). Zero-stake rows are skipped, not scored 0%.
        voters = [_roi(v[2], v[1]) for v in strats.values()
                  if v[1] > 0 and v[0] >= MIN_STRATEGY_N]
        equal = sum(voters) / len(voters) if voters else None
        out["weeks"].append({
            "wk": wk, "n": n, "stake": stake, "pnl": pnl,
            "stake_roi": _roi(pnl, stake), "equal_roi": equal,
            "wr": (100.0 * wins / n) if n else None,
            "strats": len(strats), "voters": len(voters),
            "top_share": (biggest / gross) if gross else 0.0,
        })

    common = set.intersection(*(set(per_week[w]) for w in per_week))
    out["common"] = sorted(common)
    for wk in sorted(per_week):
        n = sum(per_week[wk][s][0] for s in common)
        stake = sum(per_week[wk][s][1] for s in common)
        pnl = sum(per_week[wk][s][2] for s in common)
        out["basket"].append({"wk": wk, "n": n, "stake": stake, "pnl": pnl,
                              "roi": _roi(pnl, stake)})

    for w in out["weeks"]:
        if w["stake_roi"] is not None and w["equal_roi"] is not None:
            gap = abs(w["stake_roi"] - w["equal_roi"])
            if gap >= WEIGHTING_DIVERGENCE_PP:
                out["flags"].append(
                    f"wk{w['wk']}: stake-weighted and equal-weighted differ by "
                    f"{gap:.1f}pp — the headline is being set by stake "
                    f"concentration, not by strategy performance"
                )
        if w["top_share"] >= CONCENTRATION_FLAG:
            out["flags"].append(
                f"wk{w['wk']}: one strategy owns {100*w['top_share']:.0f}% of "
                f"gross PnL (n={w['n']}) — read this week as anecdote"
            )
    return out


def _fmt(v, suffix="%", width=9):
    return f"{'—':>{width}}" if v is None else f"{v:>{width-len(suffix)}.2f}{suffix}"


def render(label: str, r: dict) -> str:
    if not r["weeks"]:
        return f"\n=== {label} ===\n  (no settled trades in window)"
    out = [f"\n=== {label} — disjoint weeks, wk0 = last 7 days ==="]
    out.append(f"{'week':>5} {'n':>5} {'staked':>10} {'stake-wt':>9} "
               f"{'equal-wt':>9} {'WR':>7} {'strats':>6} {'vote':>5} {'top':>5}")
    for w in r["weeks"]:
        out.append(
            f"{'wk'+str(w['wk']):>5} {w['n']:>5} {w['stake']:>10,.2f} "
            f"{_fmt(w['stake_roi'])} {_fmt(w['equal_roi'])} "
            f"{_fmt(w['wr'], width=7)} {w['strats']:>6} {w['voters']:>5} "
            f"{100*w['top_share']:>4.0f}%"
        )
    if r["common"]:
        out.append(f"\n  FIXED BASKET — {len(r['common'])} strategies in every "
                   f"week (controls for mix shift)")
        out.append(f"  {'week':>5} {'n':>5} {'staked':>10} {'ROI':>9}")
        for b in r["basket"]:
            out.append(f"  {'wk'+str(b['wk']):>5} {b['n']:>5} "
                       f"{b['stake']:>10,.2f} {_fmt(b['roi'])}")
        out.append(f"  basket: {', '.join(s.replace('sports_', '') for s in r['common'])}")
    else:
        out.append("\n  FIXED BASKET — empty: no strategy traded in every week, "
                   "so week-to-week comparison is NOT like-for-like")
    out.append(f"  (equal-wt averages the {MIN_STRATEGY_N}+-trade strategies "
               f"only — 'vote' is how many qualified)")
    for f in r["flags"]:
        out.append(f"  ⚠ {f}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=6)
    ap.add_argument("--book", choices=sorted(BOOKS), default="paper")
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    try:
        rows = load_rows(conn, status=BOOKS[args.book], weeks=args.weeks)
    finally:
        conn.close()

    print(f"PORTFOLIO PERFORMANCE — {args.book} book, last {args.weeks} weeks")
    print("Nested 7/14/30d windows are one sample counted three times; these "
          "weeks are disjoint.")
    sports = [r for r in rows if str(r[1]).startswith("sports_")]
    other = [r for r in rows if not str(r[1]).startswith("sports_")]
    print(render("SPORTS", analyze(sports, weeks=args.weeks)))
    print(render("NON-SPORTS (separate denominator on purpose)",
                 analyze(other, weeks=args.weeks)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
