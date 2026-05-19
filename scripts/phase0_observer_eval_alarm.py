"""1-week post-deploy eval of the 3 cross-venue Phase 0 observers.

Targets [[vol_skew_fade_roi_audit_2026-05-19]] — auto-triggered ~5/26 via
systemd timer. Computes WR + simulated ROI on the POST-FLIP cohort
(ts >= 2026-05-19T12:35:00 UTC) and sends a single Telegram message with
the verdict (promote / retire / extend) per observer.

CLI: python3 scripts/phase0_observer_eval_alarm.py [--db PATH] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import sqlite3
import sys
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("phase0_observer_eval")

# Anchor: deploy timestamp of commit 45d4953 (spread gate + vol_skew flip).
# Signals older than this are mixed-direction / pre-spread-gate and would
# bias the eval.
COHORT_START_ISO = "2026-05-19T12:35:00+00:00"

# Kalshi fee approximation (per-contract proportional). Real fee ~0.07 × P×(1-P)
# is more accurate but the proportional approx is conservative and matches
# the audit script that found the original ROI numbers.
FEE_RATIO = 0.07

# Promotion criteria (must hit ALL three for a green "promote" verdict):
PROMOTE_MIN_N_SETTLED = 30
PROMOTE_MIN_WR = 0.55  # arbitrary — true threshold depends on entry price (BE_WR)
PROMOTE_MIN_ROI_PCT = 5.0  # sim ROI > +5%

OBSERVERS = ("vol_skew_fade", "cme_basis_fade", "etf_flow_lag")


def _wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    margin = (z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * n)) / n)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def _eval_observer(conn: sqlite3.Connection, observer: str) -> dict:
    rows = conn.execute(
        """SELECT predicted_outcome, actual_outcome, features_json
           FROM crypto_observer_signals
           WHERE observer = ? AND ts >= ?""",
        (observer, COHORT_START_ISO),
    ).fetchall()
    n_total = len(rows)
    settled = [r for r in rows if r[1] is not None]
    n_settled = len(settled)
    if n_settled == 0:
        return {
            "observer": observer, "n_total": n_total, "n_settled": 0,
            "wr": None, "wr_ci": (None, None), "roi_pct": None,
            "verdict": "EXTEND (no settled)",
        }
    wins = sum(1 for r in settled if r[0] == r[1])
    wr = wins / n_settled
    ci_lo, ci_hi = _wilson_ci(wins, n_settled)
    # Simulated ROI on the predicted side at entry price
    pnl = 0.0
    stake = 0.0
    n_priced = 0
    for pred, actual, fj in settled:
        try:
            f = json.loads(fj)
        except Exception:
            continue
        if pred == "yes":
            e = f.get("ya") or f.get("yes_ask")
        else:
            e = f.get("na") or f.get("no_ask")
        if e is None:
            continue
        try:
            e = float(e)
        except (TypeError, ValueError):
            continue
        if not (0 < e < 1):
            continue
        payout = 1.0 if pred == actual else 0.0
        pnl += payout - e - FEE_RATIO * e
        stake += e
        n_priced += 1
    roi_pct = (100.0 * pnl / stake) if stake > 0 else None

    # Verdict
    if n_settled >= PROMOTE_MIN_N_SETTLED and roi_pct is not None and roi_pct >= PROMOTE_MIN_ROI_PCT and wr >= PROMOTE_MIN_WR:
        verdict = "PROMOTE candidate"
    elif n_settled >= PROMOTE_MIN_N_SETTLED and roi_pct is not None and roi_pct < 0:
        verdict = "RETIRE"
    else:
        verdict = "EXTEND"

    return {
        "observer": observer,
        "n_total": n_total,
        "n_settled": n_settled,
        "wr": wr, "wr_ci": (ci_lo, ci_hi),
        "roi_pct": roi_pct,
        "n_priced": n_priced,
        "verdict": verdict,
    }


def _format_report(results: list[dict]) -> str:
    lines = []
    lines.append("📊 Phase 0 cross-venue observer 1-week eval")
    lines.append(f"Cohort: ts ≥ {COHORT_START_ISO} (post-flip + spread-gate)")
    lines.append("Memory: [[vol_skew_fade_roi_audit_2026-05-19]]")
    lines.append("")
    for r in results:
        obs = r["observer"]
        if r["wr"] is None:
            lines.append(f"• {obs}: n_total={r['n_total']} n_settled=0 → {r['verdict']}")
            continue
        wr_pct = 100.0 * r["wr"]
        ci_lo_pct = 100.0 * r["wr_ci"][0]
        ci_hi_pct = 100.0 * r["wr_ci"][1]
        roi_str = f"{r['roi_pct']:+.1f}%" if r["roi_pct"] is not None else "n/a"
        lines.append(
            f"• {obs}: n={r['n_total']} settled={r['n_settled']} "
            f"WR={wr_pct:.1f}% CI=[{ci_lo_pct:.0f}, {ci_hi_pct:.0f}]% "
            f"sim_ROI={roi_str} → {r['verdict']}"
        )
    lines.append("")
    lines.append("Promotion bar: n_settled≥30 AND WR≥55% AND sim_ROI≥+5%.")
    lines.append("Retire bar: n_settled≥30 AND sim_ROI<0%.")
    return "\n".join(lines)


async def _send_telegram(report: str) -> None:
    from core.config import Config
    cfg = Config()
    if not cfg.TELEGRAM_BOT_TOKEN or not cfg.TELEGRAM_CHAT_ID:
        logger.warning("No TELEGRAM credentials — printing to stdout instead.")
        print(report)
        return
    from monitoring.notifier import TelegramNotifier
    notifier = TelegramNotifier(cfg.TELEGRAM_BOT_TOKEN, cfg.TELEGRAM_CHAT_ID)
    await notifier.send(report, msg_type="phase0_observer_eval")
    logger.info(f"Sent Telegram message, {len(report)} chars.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/bot.db")
    parser.add_argument("--dry-run", action="store_true", help="Print to stdout, skip Telegram")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        logger.error(f"DB not found: {args.db}")
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    try:
        results = [_eval_observer(conn, obs) for obs in OBSERVERS]
    finally:
        conn.close()

    report = _format_report(results)
    if args.dry_run:
        print(report)
        return
    asyncio.run(_send_telegram(report))


if __name__ == "__main__":
    main()
