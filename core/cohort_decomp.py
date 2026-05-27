"""Cohort decomposition — auto-drill backtest +EV into sub-cohorts.

Pooled +EV can be driven by a specific subset (single sport, narrow band,
one direction). Manually drilling — like the cme_basis_fade FLIPPED case
where day-skew revealed regime fragility — should be automated.

Given entries with attributes (sport, band, direction), this module
splits the pooled stat by each attribute and reports which subsets
carry the +EV.

Used by:
  - check_sim_reliability.py (auto-decomp section)
  - observer_mass_backtest.py (per-observer cohort breakdown)
  - eval_2026_*.py
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from core.bootstrap import bootstrap_mean_ci


def decompose(entries: Iterable, dim_key: str, pnl_key: str = "pnl",
              min_n: int = 10) -> list:
    """Group entries by attribute dim_key, compute per-group stats.

    Returns sorted list (descending PnL) of dicts:
        [{"key": str, "n": int, "wins": int, "wr": float,
          "pnl": float, "ev": float, "ev_ci_lower": float,
          "ev_ci_upper": float, "ev_ci_overlap_zero": bool}, ...]

    Groups with n < min_n are filtered out (too noisy).
    """
    grouped: dict = defaultdict(list)
    for e in entries:
        if not isinstance(e, dict):
            continue
        key = e.get(dim_key)
        pnl = e.get(pnl_key)
        if key is None or pnl is None:
            continue
        grouped[key].append(pnl)
    results = []
    for key, pnls in grouped.items():
        if len(pnls) < min_n:
            continue
        ci = bootstrap_mean_ci(pnls, n_resamples=300)
        wins = sum(1 for p in pnls if p > 0)
        results.append({
            "key": str(key),
            "n": len(pnls),
            "wins": wins,
            "wr": round(wins / len(pnls), 3),
            "pnl": round(sum(pnls), 2),
            "ev": round(sum(pnls) / len(pnls), 4),
            "ev_ci_lower": ci["lower"],
            "ev_ci_upper": ci["upper"],
            "ev_ci_overlap_zero": ci["overlap_zero"],
        })
    results.sort(key=lambda r: -r["pnl"])
    return results


def auto_decompose(entries: Iterable, dim_keys: list = None,
                   min_n: int = 10) -> dict:
    """Run decompose() for each provided dimension.

    Default dims: 'sport', 'direction', 'band'.

    Returns dict {dim_key: [group_stats, ...]}.
    """
    if dim_keys is None:
        dim_keys = ["sport", "direction", "band"]
    out = {}
    for dim in dim_keys:
        rows = decompose(entries, dim, min_n=min_n)
        if rows:
            out[dim] = rows
    return out


def driver_summary(decomp: dict) -> list:
    """Identify dimensions where one cohort dominates the +EV.

    Returns list of strings flagging:
      - dimensions where top-1 group has |EV| 2x other groups (driver)
      - dimensions where ALL groups have CI overlapping zero (no edge anywhere)
    """
    flags = []
    for dim, rows in decomp.items():
        if not rows:
            continue
        # Top 1 cohort PnL share
        total_abs = sum(abs(r["pnl"]) for r in rows)
        if total_abs == 0:
            continue
        top1 = rows[0]
        top1_share = abs(top1["pnl"]) / total_abs
        if top1_share > 0.6 and len(rows) >= 3:
            flags.append(
                f"⚠️ {dim} dimension dominated by '{top1['key']}' "
                f"({top1_share:.0%} of |PnL|, n={top1['n']}, EV {top1['ev']:+.4f})"
            )
        # All CIs overlap zero?
        if all(r["ev_ci_overlap_zero"] for r in rows):
            flags.append(
                f"🚨 {dim}: ALL groups' EV CIs overlap zero — no statistically "
                f"significant edge in any subset"
            )
    return flags


def format_decomp(decomp: dict) -> str:
    """Pretty-print decomposition for eval report."""
    lines = []
    for dim, rows in decomp.items():
        if not rows:
            continue
        lines.append(f"### Decomposition by `{dim}`")
        lines.append("")
        lines.append("| Key | n | WR | PnL | EV/$1 | 95% CI |")
        lines.append("|---|---:|---:|---:|---:|---|")
        for r in rows:
            ci_str = f"[{r['ev_ci_lower']:+.4f}, {r['ev_ci_upper']:+.4f}]"
            if r["ev_ci_overlap_zero"]:
                ci_str += " ⚠️"
            lines.append(f"| {r['key']} | {r['n']} | {r['wr']:.0%} | "
                         f"${r['pnl']:+.2f} | ${r['ev']:+.4f} | {ci_str} |")
        lines.append("")
    return "\n".join(lines)
