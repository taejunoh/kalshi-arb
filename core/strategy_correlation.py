"""Cross-strategy correlation matrix — guards against diversification illusion.

Portfolios with multiple LIVE strategies assume independence. But if two
strategies fire on overlapping signals (e.g., both on WNBA underdog), their
daily PnL will be correlated — a bad WNBA week sinks both, defeating the
"diversification" rationale for running multiple strategies.

This module computes per-strategy daily PnL series and Pearson correlations
across pairs. High correlation (>0.7) → effective single-strategy exposure.

Used by:
  - check_sim_reliability.py portfolio-level summary
  - new strategy assessments (does it correlate with existing LIVE?)
"""
from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from typing import Optional


def daily_pnl_series(conn: sqlite3.Connection, strategy: str,
                     days_back: int = 30,
                     status_in: tuple = ("settled", "paper_settled")) -> dict:
    """Return {YYYY-MM-DD: pnl} dict per strategy from trades table."""
    placeholders = ",".join("?" for _ in status_in)
    rows = conn.execute(
        f"""SELECT substr(timestamp, 1, 10) AS day,
                   SUM(pnl) AS daily_pnl
            FROM trades
            WHERE strategy = ?
              AND status IN ({placeholders})
              AND timestamp >= strftime('%Y-%m-%dT%H:%M:%S', 'now', ?)
            GROUP BY day
            ORDER BY day""",
        (strategy, *status_in, f"-{days_back} days"),
    ).fetchall()
    return {day: pnl for day, pnl in rows if day and pnl is not None}


def pearson(xs: list, ys: list) -> Optional[float]:
    """Pearson correlation coefficient. None if not enough data."""
    n = len(xs)
    if n < 5 or n != len(ys):
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    denom_x = math.sqrt(sum((xs[i] - mean_x) ** 2 for i in range(n)))
    denom_y = math.sqrt(sum((ys[i] - mean_y) ** 2 for i in range(n)))
    if denom_x == 0 or denom_y == 0:
        return None
    return num / (denom_x * denom_y)


def correlation_matrix(conn: sqlite3.Connection, strategies: list,
                        days_back: int = 30) -> dict:
    """Compute pairwise Pearson correlation of daily PnL.

    Returns dict:
      {
        "strategies": [str, ...],
        "matrix": {(s1, s2): float | None},
        "flagged_pairs": [(s1, s2, corr), ...],  # |corr| > 0.7
        "days_overlap": {(s1, s2): int},
      }
    """
    series = {s: daily_pnl_series(conn, s, days_back) for s in strategies}
    flagged = []
    matrix = {}
    overlaps = {}
    for i, s1 in enumerate(strategies):
        for j, s2 in enumerate(strategies):
            if j <= i:
                continue
            days1 = series.get(s1, {})
            days2 = series.get(s2, {})
            common_days = sorted(set(days1.keys()) & set(days2.keys()))
            overlaps[(s1, s2)] = len(common_days)
            if len(common_days) < 5:
                matrix[(s1, s2)] = None
                continue
            xs = [days1[d] for d in common_days]
            ys = [days2[d] for d in common_days]
            corr = pearson(xs, ys)
            matrix[(s1, s2)] = corr
            if corr is not None and abs(corr) > 0.70:
                flagged.append((s1, s2, corr))
    return {
        "strategies": list(strategies),
        "matrix": matrix,
        "flagged_pairs": flagged,
        "days_overlap": overlaps,
    }


def format_matrix(report: dict) -> str:
    """Pretty-print correlation matrix."""
    lines = []
    flagged = report.get("flagged_pairs", [])
    if flagged:
        lines.append("### High-correlation pairs (|r| > 0.70)")
        lines.append("")
        for s1, s2, corr in flagged:
            n_overlap = report["days_overlap"].get((s1, s2), 0)
            direction = "POSITIVELY" if corr > 0 else "NEGATIVELY"
            lines.append(f"- 🚨 `{s1}` ↔ `{s2}`: r = {corr:+.3f} "
                         f"({direction} correlated, n={n_overlap}d overlap)")
        lines.append("")
        lines.append("_Diversification rationale weakened — effective single-strategy exposure._")
    else:
        lines.append("✅ No pair exceeds |r| = 0.70 — diversification intact.")
    lines.append("")
    if report.get("matrix"):
        lines.append("### Pearson correlation matrix")
        lines.append("")
        strats = report["strategies"]
        # Header
        lines.append("| | " + " | ".join(s.replace("sports_", "") for s in strats) + " |")
        lines.append("|---|" + "---:|" * len(strats))
        for s1 in strats:
            row = [s1.replace("sports_", "")]
            for s2 in strats:
                if s1 == s2:
                    row.append("—")
                elif (s1, s2) in report["matrix"]:
                    c = report["matrix"][(s1, s2)]
                    row.append(f"{c:+.2f}" if c is not None else "·")
                elif (s2, s1) in report["matrix"]:
                    c = report["matrix"][(s2, s1)]
                    row.append(f"{c:+.2f}" if c is not None else "·")
                else:
                    row.append("·")
            lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)
