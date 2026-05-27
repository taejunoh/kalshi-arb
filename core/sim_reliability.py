"""Sim reliability tier — wires Phase A+B utilities into a single verdict.

Implements the tier system from [[feedback_sim_reliability_tier_explicit]]:

  Tier ★★★★★  Multi-month LIVE-paper twin tracking, gap ≤3pp on n≥100
  Tier ★★★★   21+d paper deploy, n≥30, mid-price, no regime-fragility
  Tier ★★★    Paper 7-21d OR backtest n≥50 with PIT + mirror dedup
  Tier ★★     Backtest only, n=15-50, single sport/regime
  Tier ★      Tail-price OR untested gate OR regime-sensitive

Single API: `assess(entries, has_paper_twin_gap=None, deploy_age_days=None)`
returns the tier label + which checks pass/fail + actionable recommendation.

Caller responsibilities:
  - Provide entries as list of dicts with 'ts' (ISO) + 'pnl' (per-$1
    post-fee) at minimum. Optionally 'price' for wipe-zone check.
  - Pass paper-twin gap pp if known (or None to skip that check).
  - Pass deploy age in days if known.

This is the single point that ALL recommendations should pass through.
"""
from __future__ import annotations

from typing import Optional

from core.regime_stress import day_skew_report
from core.walk_forward import walk_forward_report
from core.execution_realism import is_wipe_zone


# Thresholds derived from observed sim-vs-LIVE gap history
N_FLOOR_TIER2 = 15
N_FLOOR_TIER3 = 30
N_FLOOR_TIER4 = 50
N_FLOOR_TIER5 = 100

PAPER_TWIN_GAP_TIGHT_PP = 5   # ≤5pp → LIVE-equivalent
PAPER_TWIN_GAP_OK_PP = 10     # 5-10pp → acceptable
# Above 10pp = sim diverges, downgrade

DEPLOY_DAYS_FOR_TIER4 = 21    # 21d paper minimum for ★★★★
DEPLOY_DAYS_FOR_TIER5 = 60    # ~2 months for ★★★★★


def assess(entries: list,
           has_paper_twin_gap_pp: Optional[float] = None,
           deploy_age_days: Optional[int] = None,
           sample_avg_price: Optional[float] = None,
           extra_notes: Optional[list[str]] = None) -> dict:
    """Run all sim reliability checks and return tier + verdict.

    Args:
        entries: list of dicts with at minimum 'ts' and 'pnl'.
                 Optionally 'price' (for wipe-zone), 'predicted', 'actual'.
        has_paper_twin_gap_pp: float ROI gap between LIVE and paper twin
                               (positive = LIVE worse). None if no LIVE.
        deploy_age_days: how many days the strategy has been deployed.
                         Affects tier ceiling.
        sample_avg_price: avg entry price across entries (computed if
                          None and 'price' is in entries).
        extra_notes: free-form notes to surface in output.

    Returns dict:
        {
          "tier": int (1-5),
          "tier_label": "★★★...",
          "checks": [{"name": "day_skew", "verdict": "PASS|WARN|FAIL", ...}],
          "passed_count": int,
          "failed_count": int,
          "overall_verdict": "PROMOTE | CONTINUE_PAPER | DRILL | SKIP | MOTHBALL",
          "recommendation": str,  # human-readable
          "notes": [str, ...],
        }
    """
    notes = list(extra_notes) if extra_notes else []
    checks = []

    # 1. Sample size (foundation)
    n = len(entries)
    if n < N_FLOOR_TIER2:
        checks.append({"name": "sample_size", "verdict": "FAIL",
                       "detail": f"n={n} below ★★ floor {N_FLOOR_TIER2}"})
        # Early exit — tier ★ at most
        return _build_result(1, checks, notes)

    if n < N_FLOOR_TIER3:
        checks.append({"name": "sample_size", "verdict": "WARN",
                       "detail": f"n={n} below ★★★ floor {N_FLOOR_TIER3}"})
    else:
        checks.append({"name": "sample_size", "verdict": "PASS",
                       "detail": f"n={n}"})

    # 2. Day-skew (regime fragility)
    ds = day_skew_report(entries)
    checks.append({"name": "day_skew", "verdict": ds["verdict"],
                   "detail": ds["verdict_reason"]})

    # 3. Walk-forward consistency
    wf = walk_forward_report(entries)
    checks.append({"name": "walk_forward", "verdict": wf["verdict"],
                   "detail": wf["verdict_reason"]})

    # 4. Wipe-zone exposure
    if sample_avg_price is None:
        # Derive from entries if 'price' present
        prices = [e.get("price") for e in entries
                  if isinstance(e, dict) and e.get("price") is not None]
        sample_avg_price = sum(prices) / len(prices) if prices else None
    if sample_avg_price is not None:
        if is_wipe_zone(sample_avg_price):
            checks.append({"name": "wipe_zone", "verdict": "FAIL",
                           "detail": f"avg price {sample_avg_price:.2f} in wipe zone "
                                     f"(≥0.85 or ≤0.15)"})
        else:
            checks.append({"name": "wipe_zone", "verdict": "PASS",
                           "detail": f"avg price {sample_avg_price:.2f} mid-band"})

    # 5. Paper-twin gap
    if has_paper_twin_gap_pp is not None:
        abs_gap = abs(has_paper_twin_gap_pp)
        if abs_gap <= PAPER_TWIN_GAP_TIGHT_PP:
            checks.append({"name": "paper_twin_gap", "verdict": "PASS",
                           "detail": f"|gap| {abs_gap:.1f}pp ≤ "
                                     f"{PAPER_TWIN_GAP_TIGHT_PP}pp"})
        elif abs_gap <= PAPER_TWIN_GAP_OK_PP:
            checks.append({"name": "paper_twin_gap", "verdict": "WARN",
                           "detail": f"|gap| {abs_gap:.1f}pp acceptable but watch"})
        else:
            checks.append({"name": "paper_twin_gap", "verdict": "FAIL",
                           "detail": f"|gap| {abs_gap:.1f}pp > "
                                     f"{PAPER_TWIN_GAP_OK_PP}pp — LIVE diverges"})

    # 6. Deploy age
    if deploy_age_days is not None:
        if deploy_age_days >= DEPLOY_DAYS_FOR_TIER5:
            checks.append({"name": "deploy_age", "verdict": "PASS",
                           "detail": f"{deploy_age_days}d ≥ "
                                     f"{DEPLOY_DAYS_FOR_TIER5}d (★★★★★ eligible)"})
        elif deploy_age_days >= DEPLOY_DAYS_FOR_TIER4:
            checks.append({"name": "deploy_age", "verdict": "WARN",
                           "detail": f"{deploy_age_days}d ≥ "
                                     f"{DEPLOY_DAYS_FOR_TIER4}d (★★★★ eligible)"})
        else:
            checks.append({"name": "deploy_age", "verdict": "FAIL",
                           "detail": f"{deploy_age_days}d < "
                                     f"{DEPLOY_DAYS_FOR_TIER4}d (★★★★ requires 21+d)"})

    # Tier scoring
    tier = _compute_tier(n, ds, wf, sample_avg_price, has_paper_twin_gap_pp,
                         deploy_age_days)

    return _build_result(tier, checks, notes)


def _compute_tier(n: int, ds: dict, wf: dict,
                  avg_price: Optional[float],
                  paper_twin_gap_pp: Optional[float],
                  deploy_age_days: Optional[int]) -> int:
    """Score from 1 to 5 stars based on aggregated checks."""
    # Tier 1 floor — fail-fast on any FAIL
    has_fail = (ds["verdict"] == "FAIL" or wf["verdict"] == "FAIL"
                or (avg_price is not None and is_wipe_zone(avg_price))
                or (paper_twin_gap_pp is not None
                    and abs(paper_twin_gap_pp) > PAPER_TWIN_GAP_OK_PP))
    if has_fail:
        return 1

    # Tier 2 — passed FAIL gates, but n too small
    if n < N_FLOOR_TIER3:
        return 2

    # Tier 3 — n adequate, no FAILs, but possibly some WARNs / no LIVE data
    base = 3
    # Tier 4 — paper deploy 21+d OR paper-twin tight gap on substantial n
    if (deploy_age_days is not None and deploy_age_days >= DEPLOY_DAYS_FOR_TIER4
            and n >= N_FLOOR_TIER4
            and ds["verdict"] == "PASS"
            and wf["verdict"] == "PASS"):
        base = 4
    elif (paper_twin_gap_pp is not None
          and abs(paper_twin_gap_pp) <= PAPER_TWIN_GAP_TIGHT_PP
          and n >= N_FLOOR_TIER4):
        base = 4

    # Tier 5 — LIVE-paper twin tight gap on large sample over long deploy
    if (paper_twin_gap_pp is not None
            and abs(paper_twin_gap_pp) <= PAPER_TWIN_GAP_TIGHT_PP
            and n >= N_FLOOR_TIER5
            and deploy_age_days is not None
            and deploy_age_days >= DEPLOY_DAYS_FOR_TIER5
            and ds["verdict"] == "PASS"
            and wf["verdict"] == "PASS"):
        base = 5

    return base


def _build_result(tier: int, checks: list, notes: list) -> dict:
    tier_label = "★" * tier
    passed = sum(1 for c in checks if c["verdict"] == "PASS")
    failed = sum(1 for c in checks if c["verdict"] == "FAIL")

    if tier == 5:
        verdict = "PROMOTE"
        rec = "LIVE promo eligible. Confirm DB-replay sim parity before flipping flag."
    elif tier == 4:
        verdict = "CONTINUE_PAPER"
        rec = ("Paper trajectory healthy. Continue collecting data → re-eval at "
               "next checkpoint (n≥100 for ★★★★★).")
    elif tier == 3:
        verdict = "DRILL"
        rec = ("Backtest passes basic gates but sim reliability moderate. "
               "Recommended: small paper deploy ($4 stake) + 14d watch.")
    elif tier == 2:
        verdict = "BACKTEST_ONLY"
        rec = ("Backtest finding — insufficient evidence for paper deploy. "
               "Either gather more n or proceed with explicit \"experimental\" tag.")
    else:  # tier 1
        verdict = "SKIP"
        rec = ("FAIL gate hit — do not paper-deploy. Common causes: regime-fragile, "
               "wipe-zone exposure, walk-forward inconsistent. See checks.")

    return {
        "tier": tier,
        "tier_label": tier_label,
        "checks": checks,
        "passed_count": passed,
        "failed_count": failed,
        "overall_verdict": verdict,
        "recommendation": rec,
        "notes": notes,
    }


def format_result(result: dict) -> str:
    """Human-readable summary."""
    lines = [
        f"## Sim reliability tier: **{result['tier_label']}** "
        f"({result['tier']}/5)",
        "",
        f"**Verdict: {result['overall_verdict']}**",
        f"{result['recommendation']}",
        "",
        "### Checks",
        "| Check | Verdict | Detail |",
        "|---|---|---|",
    ]
    for c in result["checks"]:
        emoji = {"PASS": "✅", "WARN": "⚠️", "FAIL": "🚨"}.get(c["verdict"], "")
        lines.append(f"| {c['name']} | {emoji} {c['verdict']} | {c['detail']} |")
    if result.get("notes"):
        lines.append("")
        lines.append("### Notes")
        for n in result["notes"]:
            lines.append(f"- {n}")
    return "\n".join(lines)
