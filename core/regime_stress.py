"""Day-skew / regime-stress automation — flags backtests where pooled +EV
is driven by a few good days, with regime fragility revealed by per-day
breakdown.

The cme_basis_fade FLIPPED case (2026-05-27) showed pooled +$224 over
30d, but 3 consecutive days (5/22-24) had WR 0-18% — catastrophic regime
fluke days disguised by aggregation. The user's [[feedback_filter_based_roi_skepticism]]
rule mandates day-skew drill-down before trusting any filter-subset +EV.

This module automates that drill: take a list of (timestamp, pnl) tuples,
group by day, return stats that flag concentration / wipe-day risk.

Used by:
  - observer_mass_backtest.py (Phase 0 candidate screening)
  - eval_2026_*.py (paper-deploy promo evaluation)
  - any new backtest reporting EV findings

Outputs are concrete numbers + a PASS/WARN/FAIL gate per the user's
sim-reliability-tier system.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Iterable, Optional


def day_skew_report(entries: Iterable, ts_key: str = "ts",
                    pnl_key: str = "pnl", n_key: Optional[str] = None) -> dict:
    """Aggregate by day and compute regime-fragility metrics.

    Args:
        entries: iterable of dicts (or 2-tuples interpreted as (ts, pnl)).
        ts_key: dict key for ISO timestamp string.
        pnl_key: dict key for per-row PnL (per-$1 stake, post-fee).
        n_key: optional dict key for row count if entries are already
               aggregated; otherwise each entry counts as n=1.

    Returns dict:
        {
          "total_pnl": float,
          "total_n": int,
          "unique_days": int,
          "avg_pnl_per_day": float,
          "max_day_pnl": (day, pnl),
          "min_day_pnl": (day, pnl),
          "best_day_share": float,   # max_day_pnl / total_pnl (0..1)
          "worst_day_share": float,  # |min_day_pnl| / total_pnl
          "wipe_days": int,          # days with WR < 25%
          "wipe_day_pnl": float,     # sum of pnl on wipe days
          "concentration_top1": float,  # share of top-1 day in total +PnL
          "concentration_top3": float,
          "days_above_median_pnl": int,
          "verdict": "PASS" | "WARN" | "FAIL",
          "verdict_reason": str,
          "by_day": [(day, n, wins, pnl), ...]  # detailed for reporting
        }
    """
    # Aggregate by day
    by_day: dict = defaultdict(lambda: {"n": 0, "wins": 0, "pnl": 0.0})
    for entry in entries:
        if isinstance(entry, (tuple, list)):
            ts, pnl = entry[0], entry[1]
            n = 1
        elif isinstance(entry, dict):
            ts = entry.get(ts_key)
            pnl = entry.get(pnl_key)
            n = entry.get(n_key, 1) if n_key else 1
        else:
            continue
        if ts is None or pnl is None:
            continue
        # Extract date portion of ISO timestamp
        day = ts[:10] if isinstance(ts, str) else str(ts)[:10]
        by_day[day]["n"] += n
        by_day[day]["pnl"] += pnl
        if pnl > 0:
            by_day[day]["wins"] += 1

    if not by_day:
        return {"total_n": 0, "total_pnl": 0.0, "unique_days": 0,
                "verdict": "PASS", "verdict_reason": "no entries"}

    rows = [(day, info["n"], info["wins"], info["pnl"])
            for day, info in sorted(by_day.items())]

    total_pnl = sum(r[3] for r in rows)
    total_n = sum(r[1] for r in rows)
    unique_days = len(rows)

    sorted_by_pnl = sorted(rows, key=lambda r: r[3])
    min_day = (sorted_by_pnl[0][0], sorted_by_pnl[0][3])
    max_day = (sorted_by_pnl[-1][0], sorted_by_pnl[-1][3])

    # Concentration: share of best 1 / 3 days in TOTAL pnl
    pos_total = sum(r[3] for r in rows if r[3] > 0)
    best_pnls = sorted([r[3] for r in rows if r[3] > 0], reverse=True)
    top1 = best_pnls[0] / pos_total if best_pnls and pos_total > 0 else 0
    top3 = sum(best_pnls[:3]) / pos_total if best_pnls and pos_total > 0 else 0

    # Wipe days: WR < 25%, n >= 5 (enough sample to be meaningful)
    wipe_days = [r for r in rows if r[1] >= 5 and (r[2] / r[1]) < 0.25]
    wipe_pnl = sum(r[3] for r in wipe_days)

    # Shares vs total
    best_share = max_day[1] / total_pnl if total_pnl > 0 else 0
    worst_share = abs(min_day[1]) / total_pnl if total_pnl != 0 else 0

    avg_per_day = total_pnl / unique_days

    # Median split
    median_pnl = sorted([r[3] for r in rows])[unique_days // 2]
    above_median = sum(1 for r in rows if r[3] > median_pnl)

    # Verdict gate
    # FAIL: >50% of positive PnL from single day (catastrophic
    #       concentration — pattern of cme_basis_fade FLIPPED 5/26).
    # FAIL: ≥3 wipe days with combined PnL > 30% of total positive PnL.
    # WARN: top-1 day share > 30% OR ≥1 wipe day.
    # PASS: otherwise.
    verdict = "PASS"
    reason = "regime distribution acceptable"
    if total_pnl > 0:
        if top1 > 0.50:
            verdict = "FAIL"
            reason = (f"top-1 day = {top1:.0%} of total positive PnL "
                      f"(concentration too high — likely regime fluke)")
        elif len(wipe_days) >= 3 and pos_total > 0 and abs(wipe_pnl) > 0.30 * pos_total:
            verdict = "FAIL"
            reason = (f"{len(wipe_days)} wipe days lost "
                      f"{abs(wipe_pnl)/pos_total:.0%} of total positive PnL")
        elif top1 > 0.30 or len(wipe_days) >= 1:
            verdict = "WARN"
            reason = (f"top-1 day = {top1:.0%}, {len(wipe_days)} wipe day(s) "
                      f"— review for regime sensitivity")
    elif total_pnl < 0 and len(wipe_days) >= 1:
        verdict = "FAIL"
        reason = f"negative pooled PnL with {len(wipe_days)} wipe days"

    return {
        "total_pnl": round(total_pnl, 2),
        "total_n": total_n,
        "unique_days": unique_days,
        "avg_pnl_per_day": round(avg_per_day, 2),
        "max_day_pnl": (max_day[0], round(max_day[1], 2)),
        "min_day_pnl": (min_day[0], round(min_day[1], 2)),
        "best_day_share": round(best_share, 3),
        "worst_day_share": round(worst_share, 3),
        "wipe_days": len(wipe_days),
        "wipe_day_pnl": round(wipe_pnl, 2),
        "concentration_top1": round(top1, 3),
        "concentration_top3": round(top3, 3),
        "days_above_median_pnl": above_median,
        "verdict": verdict,
        "verdict_reason": reason,
        "by_day": [(day, n, wins, round(pnl, 2)) for day, n, wins, pnl in rows],
    }


def format_report(report: dict) -> str:
    """Pretty-print a day_skew_report for inclusion in eval/digest output."""
    if report.get("total_n", 0) == 0:
        return "_No entries._"
    lines = [
        f"**Verdict: {report['verdict']}** — {report['verdict_reason']}",
        "",
        f"- Total: n={report['total_n']}, PnL=${report['total_pnl']:+.2f}, "
        f"{report['unique_days']} days",
        f"- Max day: {report['max_day_pnl'][0]} = ${report['max_day_pnl'][1]:+.2f} "
        f"({report['best_day_share']:.0%} of total)",
        f"- Min day: {report['min_day_pnl'][0]} = ${report['min_day_pnl'][1]:+.2f}",
        f"- Concentration: top-1 day {report['concentration_top1']:.0%}, "
        f"top-3 days {report['concentration_top3']:.0%}",
        f"- Wipe days (WR<25%, n>=5): {report['wipe_days']} "
        f"(combined PnL ${report['wipe_day_pnl']:+.2f})",
    ]
    if report.get("by_day"):
        lines.append("")
        lines.append("| Day | n | wins | PnL |")
        lines.append("|---|---:|---:|---:|")
        for day, n, wins, pnl in report["by_day"]:
            lines.append(f"| {day} | {n} | {wins} | ${pnl:+.2f} |")
    return "\n".join(lines)
