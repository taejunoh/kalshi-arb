"""Sports per-strategy × per-sport fill slip quantification.

Built 2026-05-21 after [[live-pnl-realized-bug-2026-05-20]] surfaced a
systematic signal-vs-realized fill gap on LIVE sports trades. The 5/20
backfill ran a one-shot net delta (sports -$337 aggregate), but didn't
break the loss down per-strategy × per-sport — so we can't tell whether
a given bleeder (v32, NBA playoff, underdog_v31, soccer_draw) is dying
because the gate is dead or because aggression-cap slip (avg 1-4c) is
eating the edge on its own.

For each settled LIVE sports trade with realized_fill_price set, this
script computes:

  adverse_slip_c = (realized - signal) * 100         # cents per contract
  contracts      = round(size / realized)            # post-backfill rows
  paper_pnl      = calculate_pnl(dir, signal_price,
                                  contracts * signal_price, fee, result)
  slip_pnl       = pnl_actual - paper_pnl            # negative = adverse

Aggregated per (strategy, sport):

  - n
  - mean / median adverse slip cents/contract
  - total adverse slip $
  - actual gross PnL vs paper-equivalent PnL
  - slip share of paper-vs-actual gap

Then prints a per-cohort verdict tag:

  SLIP-DOMINATED   slip explains >70% of LIVE deficit vs paper-equiv
  EDGE-EROSION     paper-equiv also negative (gate dead independent of slip)
  MIXED            slip materially adds to a marginal gate

Read-only. Console-only.
"""
import argparse
import asyncio
import logging
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import Database
from core.season import is_playoff

logger = logging.getLogger("sports_fill_slip_audit")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


SPORT_SHORT_BY_TOKEN = {
    "WNBAGAME": "WNBA",  # MUST precede NBAGAME (substring)
    "NBAGAME": "NBA", "MLBGAME": "MLB", "NHLGAME": "NHL",
    "NPBGAME": "NPB", "KBOGAME": "KBO",
    "MLSGAME": "MLS", "EPLGAME": "EPL", "LALIGAGAME": "LAL",
    "UCLGAME": "UCL", "BUNDESLIGAGAME": "BUN",
    "ATPMATCH": "ATP", "WTAMATCH": "WTA", "UFCFIGHT": "UFC",
}
_TOKEN_ORDER = sorted(SPORT_SHORT_BY_TOKEN, key=len, reverse=True)


def extract_sport(ticker: str) -> str | None:
    for tok in _TOKEN_ORDER:
        if tok in ticker:
            return SPORT_SHORT_BY_TOKEN[tok]
    return None


def calculate_pnl(direction: str, price: float, size_usdc: float,
                  fee: float, result: str) -> float:
    """Mirror of SettlementChecker.calculate_pnl — no checker instance needed."""
    if direction == result:
        return round(size_usdc * ((1.0 - price) / price) - fee, 2)
    return round(-size_usdc, 2)


async def audit(since_iso: str, strategy_filter: str | None):
    db = Database()
    await db.initialize()

    import aiosqlite
    async with aiosqlite.connect(db.db_path, timeout=30.0) as conn:
        conn.row_factory = aiosqlite.Row
        sql = (
            "SELECT id, strategy, market_id, direction, price, size, fee, "
            "       pnl, realized_fill_price, timestamp "
            "FROM trades "
            "WHERE status = 'settled' "
            "  AND realized_fill_price IS NOT NULL "
            "  AND realized_fill_price > 0 "
            "  AND price > 0 "
            "  AND timestamp >= ? "
            "  AND strategy LIKE 'sports_%' "
        )
        params: list = [since_iso]
        if strategy_filter:
            sql += "  AND strategy = ? "
            params.append(strategy_filter)
        sql += "ORDER BY timestamp"
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()

    if not rows:
        print(f"No settled LIVE sports trades with realized_fill_price since {since_iso}.")
        return

    # cohort = (strategy, sport)
    cohorts: dict[tuple[str, str], list[dict]] = defaultdict(list)
    skipped_no_sport = 0
    for r in rows:
        sport = extract_sport(r["market_id"])
        if not sport:
            skipped_no_sport += 1
            continue
        old_price = r["price"]
        realized = r["realized_fill_price"]
        size = r["size"] or 0.0
        fee = r["fee"] or 0.0
        pnl = r["pnl"] or 0.0
        direction = r["direction"]
        if realized <= 0 or old_price <= 0 or size <= 0:
            continue
        # Post-backfill rows: size = contracts × realized. Reverse out contracts.
        contracts = round(size / realized)
        if contracts <= 0:
            continue
        # Result inference: same heuristic as backfill_live_pnl_realized.py —
        # a loss row has pnl ≈ -size; deep tails win ~$0 but pnl + size > 1.
        won = abs(pnl + size) > 1.0
        result = direction if won else ("no" if direction == "yes" else "yes")
        intended_stake = round(contracts * old_price, 2)
        paper_pnl = calculate_pnl(direction, old_price, intended_stake, fee, result)
        slip_pnl = round(pnl - paper_pnl, 2)
        adverse_slip_c = round((realized - old_price) * 100, 2)
        cohorts[(r["strategy"], sport)].append({
            "id": r["id"],
            "ticker": r["market_id"],
            "direction": direction,
            "signal": old_price,
            "realized": realized,
            "contracts": contracts,
            "size": size,
            "fee": fee,
            "pnl": pnl,
            "paper_pnl": paper_pnl,
            "slip_pnl": slip_pnl,
            "slip_c": adverse_slip_c,
            "won": won,
        })

    print(f"\n=== Sports Fill Slip Audit (since {since_iso}) ===")
    if skipped_no_sport:
        print(f"  (skipped {skipped_no_sport} rows with no detectable sport in ticker)")
    print()

    # Sort cohorts by total slip impact (most adverse first)
    cohort_summary = []
    for (strat, sport), trades in cohorts.items():
        n = len(trades)
        slips_c = [t["slip_c"] for t in trades]
        mean_slip_c = statistics.mean(slips_c)
        median_slip_c = statistics.median(slips_c)
        total_slip_pnl = sum(t["slip_pnl"] for t in trades)
        actual_pnl = sum(t["pnl"] for t in trades)
        paper_equiv_pnl = sum(t["paper_pnl"] for t in trades)
        total_contracts = sum(t["contracts"] for t in trades)
        total_stake = sum(t["size"] for t in trades)
        wins = sum(1 for t in trades if t["won"])
        wr = wins / n * 100 if n else 0
        # Verdict logic
        gap = paper_equiv_pnl - actual_pnl  # how much slip cost us (positive = adverse)
        if paper_equiv_pnl < 0:
            verdict = "EDGE-EROSION"
        elif gap > 0 and (gap / max(abs(paper_equiv_pnl), 1.0)) > 0.7:
            verdict = "SLIP-DOMINATED"
        elif gap > 0 and (gap / max(abs(paper_equiv_pnl), 1.0)) > 0.3:
            verdict = "MIXED"
        elif actual_pnl > 0:
            verdict = "PROFITABLE"
        else:
            verdict = "SLIP-MARGINAL"
        cohort_summary.append({
            "strategy": strat,
            "sport": sport,
            "n": n,
            "wr": wr,
            "mean_slip_c": mean_slip_c,
            "median_slip_c": median_slip_c,
            "total_slip_pnl": total_slip_pnl,
            "actual_pnl": actual_pnl,
            "paper_equiv_pnl": paper_equiv_pnl,
            "gap": gap,
            "total_contracts": total_contracts,
            "total_stake": total_stake,
            "verdict": verdict,
        })

    cohort_summary.sort(key=lambda c: c["actual_pnl"])

    print(f"{'strategy':30} {'sport':5} {'n':>4} {'WR':>5} "
          f"{'sigP$':>5} {'slipC':>6} {'medC':>5} "
          f"{'actual':>8} {'paper':>8} {'gap':>7}  verdict")
    print("-" * 110)
    for c in cohort_summary:
        avg_signal_dollar = (c["total_stake"] / max(c["total_contracts"], 1)) if c["total_contracts"] else 0
        print(
            f"{c['strategy']:30} {c['sport']:5} {c['n']:>4} "
            f"{c['wr']:>4.0f}% {avg_signal_dollar:>5.2f} "
            f"{c['mean_slip_c']:>+6.2f} {c['median_slip_c']:>+5.2f} "
            f"{c['actual_pnl']:>+8.2f} {c['paper_equiv_pnl']:>+8.2f} {c['gap']:>+7.2f}  "
            f"{c['verdict']}"
        )

    # Per-strategy totals
    print("\n--- per-strategy totals ---")
    by_strat: dict[str, dict] = defaultdict(lambda: {"n": 0, "actual": 0.0, "paper": 0.0, "stake": 0.0})
    for c in cohort_summary:
        s = by_strat[c["strategy"]]
        s["n"] += c["n"]
        s["actual"] += c["actual_pnl"]
        s["paper"] += c["paper_equiv_pnl"]
        s["stake"] += c["total_stake"]
    print(f"{'strategy':30} {'n':>4} {'stake':>8} {'actual':>8} {'paper':>8} "
          f"{'slip$':>7} {'actROI':>7} {'papROI':>7}")
    for strat, s in sorted(by_strat.items(), key=lambda x: x[1]["actual"]):
        slip = s["paper"] - s["actual"]
        act_roi = s["actual"] / s["stake"] * 100 if s["stake"] > 0 else 0
        pap_roi = s["paper"] / s["stake"] * 100 if s["stake"] > 0 else 0
        print(f"{strat:30} {s['n']:>4} {s['stake']:>8.0f} "
              f"{s['actual']:>+8.2f} {s['paper']:>+8.2f} {slip:>+7.2f} "
              f"{act_roi:>+6.1f}% {pap_roi:>+6.1f}%")

    # Grand total
    tot_actual = sum(s["actual"] for s in by_strat.values())
    tot_paper = sum(s["paper"] for s in by_strat.values())
    tot_stake = sum(s["stake"] for s in by_strat.values())
    tot_n = sum(s["n"] for s in by_strat.values())
    print("-" * 90)
    print(f"{'TOTAL':30} {tot_n:>4} {tot_stake:>8.0f} "
          f"{tot_actual:>+8.2f} {tot_paper:>+8.2f} {tot_paper - tot_actual:>+7.2f}")

    print("\nVerdict meanings:")
    print("  EDGE-EROSION    paper-equiv PnL also negative — gate dead independent of slip")
    print("  SLIP-DOMINATED  slip explains >70% of LIVE deficit vs paper-equiv")
    print("  MIXED           slip explains 30-70% of deficit")
    print("  SLIP-MARGINAL   actual ≤ 0 but slip share <30% (paper barely positive)")
    print("  PROFITABLE      actual PnL > 0")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=30,
                   help="Look-back window in days (default 30).")
    p.add_argument("--since", type=str, default=None,
                   help="Override --days with explicit ISO date (e.g. 2026-04-01).")
    p.add_argument("--strategy", type=str, default=None,
                   help="Filter to a single strategy name.")
    args = p.parse_args()

    if args.since:
        since_iso = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc).isoformat()
    else:
        since_iso = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat()

    asyncio.run(audit(since_iso, args.strategy))


if __name__ == "__main__":
    main()
