"""Backtest postprocessing - 1-line wrapper for any new backtest.

Goal: any new `scripts/*_backtest.py` should be able to add ONE function
call at the end of its analysis to get a complete sim-reliability report
attached, without copy-pasting Phase A-F integration.

Usage:

    from core.backtest_postproc import postproc_report

    # In your backtest script, after computing per-trade entries:
    entries = [{"ts": "2026-05-27T...", "pnl": 0.05, "price": 0.40,
                "sport": "MLB", "direction": "yes"}, ...]
    report = postproc_report(entries, name="my_strategy_v1")
    print(report)

The wrapper auto-runs:
  - sim_reliability.assess() -> tier + verdict
  - cohort_decomp.auto_decompose() -> sport/band/direction drill
  - regime_stress.day_skew_report() -> day-skew detail
  - walk_forward.train_test_report() -> train/test split

Caller doesn't need to know about Phase A-F machinery - just calls the
function and gets a complete pre-flight Markdown.
"""
from __future__ import annotations

from typing import Optional

from core.sim_reliability import assess, format_result
from core.regime_stress import day_skew_report, format_report as fmt_day_skew
from core.walk_forward import (train_test_report, walk_forward_report,
                                format_train_test, format_report as fmt_wf)
from core.cohort_decomp import auto_decompose, driver_summary, format_decomp


def postproc_report(entries: list, name: str = "strategy",
                    deploy_age_days: Optional[int] = None,
                    has_paper_twin_gap_pp: Optional[float] = None,
                    stale_ratio: Optional[float] = None,
                    cohort_dims: Optional[list] = None) -> str:
    """Run all Phase A-F postprocessing and return a Markdown report.

    Args:
        entries: list of dicts with at minimum {ts, pnl}.
                 Optional fields: price, sport, direction, band, etc.
        name: strategy name for header.
        deploy_age_days / paper_twin_gap_pp / stale_ratio: optional context
                 passed to assess().
        cohort_dims: list of dim_keys for cohort_decomp.
                     Default ["sport", "direction", "band"].

    Returns: Markdown string with full pre-flight breakdown.
    """
    if not entries:
        return f"# Backtest postproc: {name}\n\n_No entries — nothing to report._"

    sections = [f"# Backtest postproc: {name}",
                f"_n={len(entries)} entries, postproc applied via Phase A-F utilities_",
                ""]

    # Tier verdict (top-line summary)
    try:
        avg_price = None
        prices = [e.get("price") for e in entries if e.get("price") is not None]
        if prices:
            avg_price = sum(prices) / len(prices)
        result = assess(entries,
                        has_paper_twin_gap_pp=has_paper_twin_gap_pp,
                        deploy_age_days=deploy_age_days,
                        sample_avg_price=avg_price,
                        stale_ratio=stale_ratio)
        sections.append(format_result(result))
    except Exception as e:
        sections.append(f"_assess() error: {e}_")

    # Cohort decomposition (sport/direction/band)
    try:
        decomp = auto_decompose(entries, dim_keys=cohort_dims)
        if decomp:
            sections.append("")
            sections.append("## Cohort decomposition")
            flags = driver_summary(decomp)
            for flag in flags:
                sections.append(f"- {flag}")
            sections.append("")
            sections.append(format_decomp(decomp))
    except Exception as e:
        sections.append(f"_cohort_decomp error: {e}_")

    # Day-skew detail
    try:
        ds = day_skew_report(entries)
        if ds.get("total_n", 0) > 0:
            sections.append("")
            sections.append("## Day-skew detail")
            sections.append(fmt_day_skew(ds))
    except Exception as e:
        sections.append(f"_day_skew error: {e}_")

    # Walk-forward (4-window)
    try:
        wf = walk_forward_report(entries)
        if wf.get("pooled_n", 0) > 0:
            sections.append("")
            sections.append("## Walk-forward (4 windows)")
            sections.append(fmt_wf(wf))
    except Exception as e:
        sections.append(f"_walk_forward error: {e}_")

    # Train/test (true holdout)
    try:
        tt = train_test_report(entries, train_frac=0.7)
        if "train" in tt:
            sections.append("")
            sections.append("## Train/test holdout (70/30)")
            sections.append(format_train_test(tt))
    except Exception as e:
        sections.append(f"_train_test error: {e}_")

    return "\n".join(sections)


def quick_tier(entries: list, **kwargs) -> dict:
    """Even shorter: just return tier dict, no formatting."""
    avg_price = None
    prices = [e.get("price") for e in entries if e.get("price") is not None]
    if prices:
        avg_price = sum(prices) / len(prices)
    return assess(entries, sample_avg_price=avg_price, **kwargs)
