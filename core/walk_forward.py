"""Walk-forward consistency check — guards against single-window flukes.

A backtest with positive pooled EV can still be overfit to a specific
regime/period. The honest check: split the timespan into N
non-overlapping windows and verify the signal is +EV in MOST of them
(not just driven by 1-2 lucky windows).

This complements core/regime_stress.py:
  - regime_stress = day-level concentration (single-day wipes)
  - walk_forward  = window-level consistency (period-level fluke)

Both should pass for a strategy to earn ★★★★ sim reliability tier.

Used by:
  - observer_mass_backtest.py
  - eval_2026_*.py
  - any backtest reporting EV > 30d

The 4-window default mimics roughly weekly rotation in 30d data —
each window's n is comparable to a paper deploy's first week. So this
walk-forward gives a "would a 7d paper deploy in EACH of these windows
have caught the signal?" answer.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Optional

from core.bootstrap import bootstrap_mean_ci


def _parse_ts(ts):
    if isinstance(ts, datetime):
        return ts
    if isinstance(ts, str):
        clean = ts.replace("Z", "").split("+")[0].split(".")[0]
        return datetime.fromisoformat(clean)
    return None


def walk_forward_report(entries: Iterable, ts_key: str = "ts",
                        pnl_key: str = "pnl", n_windows: int = 4,
                        min_n_per_window: int = 10) -> dict:
    """Split entries into N equal-time windows; compute per-window stats.

    Args:
        entries: list of dicts/tuples (timestamp, pnl).
        ts_key/pnl_key: dict accessors.
        n_windows: how many non-overlapping windows (default 4).
        min_n_per_window: ignore window stats if sample below this
                          (still reports n=0, marked "skipped").

    Returns dict:
        {
          "n_windows": int,
          "window_size_days": float,
          "pooled_pnl": float,
          "pooled_n": int,
          "pooled_wr": float,
          "windows": [
            {"label": "1/4", "start": iso, "end": iso, "n": int,
             "wins": int, "wr": float, "pnl": float, "ev": float,
             "skipped": bool},
            ...
          ],
          "positive_windows": int,  # how many windows had +PnL
          "win_consistency_ratio": float,  # positive_windows / n_evaluated
          "verdict": "PASS" | "WARN" | "FAIL",
          "verdict_reason": str,
        }
    """
    # Normalize input
    rows = []
    for e in entries:
        if isinstance(e, (tuple, list)):
            ts, pnl = e[0], e[1]
        elif isinstance(e, dict):
            ts, pnl = e.get(ts_key), e.get(pnl_key)
        else:
            continue
        dt = _parse_ts(ts)
        if dt is None or pnl is None:
            continue
        rows.append((dt, pnl))

    if not rows:
        return {"n_windows": n_windows, "pooled_n": 0,
                "verdict": "PASS", "verdict_reason": "no entries"}

    rows.sort(key=lambda r: r[0])
    t_min, t_max = rows[0][0], rows[-1][0]
    total_span = t_max - t_min
    window_size = total_span / n_windows if n_windows > 0 else total_span
    window_size_days = window_size.total_seconds() / 86400

    # Bucket into windows
    windows = []
    for w in range(n_windows):
        w_start = t_min + window_size * w
        w_end = t_min + window_size * (w + 1) if w < n_windows - 1 else t_max + timedelta(microseconds=1)
        in_window = [(dt, pnl) for dt, pnl in rows if w_start <= dt < w_end]
        n = len(in_window)
        wins = sum(1 for _, p in in_window if p > 0)
        pnl_sum = sum(p for _, p in in_window)
        skipped = n < min_n_per_window
        # Per-window CI on mean EV — only computed when not skipped
        window_pnls = [p for _, p in in_window]
        ci = bootstrap_mean_ci(window_pnls, n_resamples=300) if not skipped and window_pnls else None
        windows.append({
            "label": f"{w+1}/{n_windows}",
            "start": w_start.isoformat()[:10],
            "end": w_end.isoformat()[:10],
            "n": n,
            "wins": wins,
            "wr": wins / n if n else 0.0,
            "pnl": round(pnl_sum, 2),
            "ev": round(pnl_sum / n, 4) if n else 0.0,
            "ci_lower": ci["lower"] if ci else None,
            "ci_upper": ci["upper"] if ci else None,
            "ci_overlap_zero": ci["overlap_zero"] if ci else None,
            "skipped": skipped,
        })

    # Consistency over EVALUATED windows (skipping low-n)
    evaluated = [w for w in windows if not w["skipped"]]
    positive_windows = sum(1 for w in evaluated if w["pnl"] > 0)
    # Statistical positives: window with CI clearly above zero
    statistically_positive = sum(
        1 for w in evaluated
        if w.get("ci_lower") is not None and w["ci_lower"] > 0
    )
    n_evaluated = len(evaluated)
    consistency = positive_windows / n_evaluated if n_evaluated else 0.0
    stat_consistency = statistically_positive / n_evaluated if n_evaluated else 0.0

    pooled_pnl = sum(p for _, p in rows)
    pooled_n = len(rows)
    pooled_wins = sum(1 for _, p in rows if p > 0)

    # Verdict — at least 75% of evaluated windows should agree with
    # pooled-direction. Anything <50% directly contradicts pooled stat.
    if n_evaluated < 2:
        verdict = "WARN"
        reason = f"only {n_evaluated} window(s) had enough samples to evaluate"
    elif pooled_pnl > 0:
        if consistency >= 0.75:
            verdict = "PASS"
            reason = f"{positive_windows}/{n_evaluated} windows positive"
        elif consistency >= 0.50:
            verdict = "WARN"
            reason = f"only {positive_windows}/{n_evaluated} windows positive — borderline"
        else:
            verdict = "FAIL"
            reason = (f"pooled +EV but only {positive_windows}/{n_evaluated} windows positive "
                      f"— pooled stat overfit to fluke windows")
    elif pooled_pnl < 0:
        if consistency <= 0.25:
            verdict = "PASS"  # consistently -EV is also "consistent" for kill decision
            reason = f"consistently -EV ({positive_windows}/{n_evaluated} windows positive)"
        else:
            verdict = "WARN"
            reason = f"pooled -EV but {positive_windows}/{n_evaluated} windows positive — regime-dependent"
    else:
        verdict = "WARN"
        reason = "pooled PnL ≈ 0, regime-uncertain"

    return {
        "n_windows": n_windows,
        "window_size_days": round(window_size_days, 1),
        "pooled_pnl": round(pooled_pnl, 2),
        "pooled_n": pooled_n,
        "pooled_wr": round(pooled_wins / pooled_n, 3) if pooled_n else 0.0,
        "windows": windows,
        "positive_windows": positive_windows,
        "statistically_positive_windows": statistically_positive,
        "evaluated_windows": n_evaluated,
        "win_consistency_ratio": round(consistency, 3),
        "statistical_consistency_ratio": round(stat_consistency, 3),
        "verdict": verdict,
        "verdict_reason": reason,
    }


def train_test_report(entries: Iterable, ts_key: str = "ts",
                      pnl_key: str = "pnl", price_key: str = "price",
                      train_frac: float = 0.7) -> dict:
    """TRUE walk-forward: estimate parameters on early data, evaluate on holdout.

    Splits entries chronologically:
      - First train_frac (default 70%) = estimation window
      - Last 1-train_frac = holdout test

    Reports EV in both windows. Strategy validates if HOLDOUT also positive
    (signal generalized) rather than just train period (in-sample fit).

    Returns dict:
        {
          "train": {"n", "pnl", "ev", "wr"},
          "test":  {"n", "pnl", "ev", "wr", "ci_lower", "ci_upper", "ci_overlap_zero"},
          "generalization_ratio": test_ev / train_ev (1.0 = perfect),
          "verdict": PASS|WARN|FAIL,
          "verdict_reason": str,
        }

    Verdict:
      PASS:  train +EV AND test +EV AND test CI excludes zero
      WARN:  train +EV but test EV/sign uncertain (CI overlaps zero)
      FAIL:  train +EV but test -EV (overfit) OR no holdout (low n)
    """
    rows = []
    for e in entries:
        if isinstance(e, (tuple, list)):
            ts, pnl = e[0], e[1]
        elif isinstance(e, dict):
            ts, pnl = e.get(ts_key), e.get(pnl_key)
        else:
            continue
        dt = _parse_ts(ts)
        if dt is None or pnl is None:
            continue
        rows.append((dt, pnl))

    if len(rows) < 20:  # need enough for meaningful split
        return {"verdict": "FAIL",
                "verdict_reason": f"n={len(rows)} < 20 — insufficient for train/test"}

    rows.sort(key=lambda r: r[0])
    split_idx = int(len(rows) * train_frac)
    train = rows[:split_idx]
    test = rows[split_idx:]

    def _stats(window: list) -> dict:
        n = len(window)
        if n == 0:
            return {"n": 0}
        pnls = [p for _, p in window]
        wins = sum(1 for p in pnls if p > 0)
        ci = bootstrap_mean_ci(pnls, n_resamples=300) if n >= 10 else None
        return {
            "n": n,
            "pnl": round(sum(pnls), 2),
            "ev": round(sum(pnls) / n, 4),
            "wr": round(wins / n, 3),
            "ci_lower": ci["lower"] if ci else None,
            "ci_upper": ci["upper"] if ci else None,
            "ci_overlap_zero": ci["overlap_zero"] if ci else None,
        }

    train_s = _stats(train)
    test_s = _stats(test)

    # Generalization ratio
    train_ev = train_s.get("ev", 0)
    test_ev = test_s.get("ev", 0)
    gen_ratio = (test_ev / train_ev) if train_ev else 0.0

    # Verdict
    if test_s["n"] < 10:
        verdict = "FAIL"
        reason = f"holdout n={test_s['n']} < 10 — cannot validate generalization"
    elif train_ev <= 0 and test_ev <= 0:
        verdict = "PASS"  # consistent -EV is consistent
        reason = "consistently -EV in both train and holdout"
    elif train_ev > 0 and test_ev > 0 and not test_s["ci_overlap_zero"]:
        verdict = "PASS"
        reason = (f"train EV {train_ev:+.4f} → test EV {test_ev:+.4f} "
                  f"(generalization {gen_ratio:.1%}), holdout CI excludes 0")
    elif train_ev > 0 > test_ev:
        verdict = "FAIL"
        reason = (f"OVERFIT: train +EV {train_ev:+.4f} → test -EV {test_ev:+.4f} "
                  f"(signal does not generalize)")
    else:
        verdict = "WARN"
        reason = (f"train EV {train_ev:+.4f} → test EV {test_ev:+.4f}, "
                  f"holdout CI overlaps zero (n={test_s['n']})")

    return {
        "train": train_s,
        "test": test_s,
        "train_frac": train_frac,
        "generalization_ratio": round(gen_ratio, 3),
        "verdict": verdict,
        "verdict_reason": reason,
    }


def format_train_test(report: dict) -> str:
    """Pretty-print train/test report."""
    if "verdict" not in report:
        return "_No data._"
    lines = [
        f"**Train/test verdict: {report['verdict']}** — {report['verdict_reason']}",
        "",
    ]
    if "train" in report and report["train"].get("n", 0) > 0:
        t = report["train"]
        h = report["test"]
        lines.append(f"- Train ({int(report['train_frac']*100)}%): "
                     f"n={t['n']}, EV {t['ev']:+.4f}, WR {t['wr']:.1%}")
        lines.append(f"- Test  ({100 - int(report['train_frac']*100)}%): "
                     f"n={h['n']}, EV {h['ev']:+.4f}, WR {h['wr']:.1%}")
        if h.get("ci_lower") is not None:
            lines.append(f"  - Holdout 95% CI: [{h['ci_lower']:+.4f}, {h['ci_upper']:+.4f}]")
        lines.append(f"- Generalization: {report['generalization_ratio']:.1%} "
                     f"(1.0 = perfect)")
    return "\n".join(lines)


def format_report(report: dict) -> str:
    """Pretty-print walk-forward report for inclusion in eval output."""
    if report.get("pooled_n", 0) == 0:
        return "_No entries._"
    lines = [
        f"**Walk-forward verdict: {report['verdict']}** — {report['verdict_reason']}",
        "",
        f"- Pooled: n={report['pooled_n']}, PnL=${report['pooled_pnl']:+.2f}, "
        f"WR {report['pooled_wr']:.1%}",
        f"- {report['n_windows']} windows × ~{report['window_size_days']:.1f} days",
        f"- Positive windows: {report['positive_windows']}/{report['evaluated_windows']} "
        f"({report['win_consistency_ratio']:.0%})",
        "",
        "| Window | Range | n | WR | PnL | Notes |",
        "|---|---|---:|---:|---:|---|",
    ]
    for w in report["windows"]:
        notes = "skipped (low n)" if w["skipped"] else ""
        lines.append(
            f"| {w['label']} | {w['start']}→{w['end']} | {w['n']} "
            f"| {w['wr']:.0%} | ${w['pnl']:+.2f} | {notes} |"
        )
    return "\n".join(lines)
