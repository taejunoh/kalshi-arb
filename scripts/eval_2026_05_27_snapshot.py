"""2026-05-27 multi-cohort evaluation snapshot.

Single-shot Telegram alarm fired by systemd timer 2026-05-27 13:00 UTC.
Pulls a data snapshot for each of the four evaluations due that day:

  1. cross_asset_observer_suite (3 observers)
     [[cross_asset_observer_suite_2026-05-20]]
     btc_eth_lead_lag / cross_asset_corr_break / kalshi_book_lag

  2. cme_basis_fade_v1 paper trader
     [[cme_basis_fade_v1_promotion_2026-05-20]]
     Phase 1 paper, ya ∈ [0.17, 0.42] sub-cohort

  3. mid_otm_tail observer bucket-WR analysis
     [[mid_otm_tail_observer_2026-05-20]]
     Cross-tab WR by (mts_s, na) buckets

  4. universe_expansion T-leg fires
     [[universe_expansion_2026-05-20]]
     features_json.kind = 'cumulative' fires for late_close_tail +
     mid_otm_tail

Each section is read-only on the DB.  The script does not make
verdicts itself — the user decides PROMOTE / EXTEND / RETIRE based
on the snapshot + the linked memory criteria.

CLI: python3 scripts/eval_2026_05_27_snapshot.py [--db PATH] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("eval_2026_05_27")

COHORT_START_ISO = "2026-05-20T12:00:00+00:00"   # deploy day of all 4 cohorts

CROSS_ASSET_OBSERVERS = ("btc_eth_lead_lag", "cross_asset_corr_break", "kalshi_book_lag")
CME_BASIS_BAND = (0.17, 0.42)
MID_OTM_NA_BUCKETS = ((0.60, 0.80), (0.80, 0.93), (0.93, 1.00))   # fee-positive vs locked
MID_OTM_MTS_BUCKETS = ((600, 1800), (1800, 3600), (3600, 7200))   # 10-30m / 30-60m / 1-2h


def _wr(wins: int, n: int) -> float | None:
    return (100.0 * wins / n) if n > 0 else None


def _fmt_pct(x: float | None, w: int = 5) -> str:
    return f"{x:>{w}.1f}%" if x is not None else f"{'—':>{w}s}"


# ---------------------------------------------------------------------------
# Section 1 — cross-asset observers
# ---------------------------------------------------------------------------

def _section_cross_asset(conn: sqlite3.Connection) -> list[str]:
    lines = ["<b>1️⃣ Cross-asset observers</b>",
             "<i>Eval bar: n≥50 settled; lead_lag/corr_break WR≥70%, book_lag WR≥75%.</i>"]
    for obs in CROSS_ASSET_OBSERVERS:
        rows = conn.execute(
            """SELECT predicted_outcome, actual_outcome
               FROM crypto_observer_signals
               WHERE observer = ? AND ts >= ?""",
            (obs, COHORT_START_ISO),
        ).fetchall()
        n_total = len(rows)
        settled = [r for r in rows if r[1] is not None]
        n = len(settled)
        wins = sum(1 for p, a in settled if p == a)
        wr = _wr(wins, n)
        lines.append(
            f"  <code>{obs}</code>: total={n_total} settled={n} "
            f"WR={_fmt_pct(wr)}"
        )
    return lines


# ---------------------------------------------------------------------------
# Section 2 — cme_basis_fade_v1 paper trader
# ---------------------------------------------------------------------------

def _section_cme_basis(conn: sqlite3.Connection) -> list[str]:
    """cme_basis_fade_v1 strategy: paper_settled trades only count.

    Per [[cme_basis_fade_v1_promotion_2026-05-20]] eval bar:
      • WR ≥ 70% in ya 0.17-0.42 (entry band)
      • Avg PnL/contract ≥ +$0.03
      • Total paper PnL ≥ +$5
    """
    n_filled, n_settled, total_pnl, total_size = conn.execute(
        """SELECT
             SUM(CASE WHEN status = 'paper_filled' THEN 1 ELSE 0 END),
             SUM(CASE WHEN status = 'paper_settled' THEN 1 ELSE 0 END),
             COALESCE(SUM(CASE WHEN status = 'paper_settled' THEN pnl ELSE 0 END), 0),
             COALESCE(SUM(CASE WHEN status = 'paper_settled' THEN size ELSE 0 END), 0)
           FROM trades
           WHERE strategy = 'cme_basis_fade_v1'""",
    ).fetchone()
    wins = conn.execute(
        """SELECT COUNT(*) FROM trades
           WHERE strategy = 'cme_basis_fade_v1' AND status = 'paper_settled' AND pnl > 0""",
    ).fetchone()[0]
    n_filled = n_filled or 0
    n_settled = n_settled or 0
    total_pnl = float(total_pnl or 0.0)
    total_size = float(total_size or 0.0)
    wr = _wr(wins, n_settled)
    roi = (100.0 * total_pnl / total_size) if total_size > 0 else None
    lines = [
        "",
        "<b>2️⃣ cme_basis_fade_v1 (paper, ya 0.17-0.42)</b>",
        "<i>Promotion bar: WR≥70%, avg PnL/c≥+$0.03, total PnL≥+$5.</i>",
        f"  paper_filled={n_filled} | paper_settled={n_settled} | "
        f"wins={wins} WR={_fmt_pct(wr)} | "
        f"pnl=${total_pnl:+.2f} | ROI={_fmt_pct(roi)}",
    ]
    return lines


# ---------------------------------------------------------------------------
# Section 3 — mid_otm_tail bucket-WR
# ---------------------------------------------------------------------------

def _section_mid_otm_tail(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """SELECT predicted_outcome, actual_outcome, features_json
           FROM crypto_observer_signals
           WHERE observer = 'mid_otm_tail' AND ts >= ?""",
        (COHORT_START_ISO,),
    ).fetchall()
    n_total = len(rows)
    # Cross-tab WR by (mts_s bucket, na bucket)
    grid: dict[tuple[int, int], dict] = {}
    for mts_lo, mts_hi in MID_OTM_MTS_BUCKETS:
        for na_lo, na_hi in MID_OTM_NA_BUCKETS:
            grid[(mts_lo, na_lo)] = {"n": 0, "wins": 0}
    for pred, actual, fj in rows:
        if actual is None:
            continue
        try:
            f = json.loads(fj or "{}")
        except Exception:
            continue
        mts = f.get("mts_s")
        na = f.get("na")
        if mts is None or na is None:
            continue
        for mts_lo, mts_hi in MID_OTM_MTS_BUCKETS:
            if not (mts_lo < mts <= mts_hi):
                continue
            for na_lo, na_hi in MID_OTM_NA_BUCKETS:
                if na_lo <= na < na_hi:
                    cell = grid[(mts_lo, na_lo)]
                    cell["n"] += 1
                    if pred == actual:
                        cell["wins"] += 1
                    break
            break
    lines = [
        "",
        "<b>3️⃣ mid_otm_tail bucket-WR (mts_s × na)</b>",
        "<i>Phase 1 bar: ≥20 fires in na ∈ [0.80, 0.93] band with WR ≥ 95%.</i>",
        "<pre>",
        f"{'mts_s':>9s}  " + "  ".join(
            f"{int(na_lo*100):>2d}-{int(na_hi*100):>2d}¢" for na_lo, na_hi in MID_OTM_NA_BUCKETS
        ),
    ]
    for mts_lo, mts_hi in MID_OTM_MTS_BUCKETS:
        cells: list[str] = []
        for na_lo, _ in MID_OTM_NA_BUCKETS:
            c = grid[(mts_lo, na_lo)]
            if c["n"] == 0:
                cells.append(f"{'-':>7s}")
            else:
                wr = 100.0 * c["wins"] / c["n"]
                cells.append(f"{wr:>3.0f}%(n={c['n']:>3d})")
        lines.append(f"{mts_lo}-{mts_hi:>4d}  " + "  ".join(cells))
    lines.append(f"total fires (incl. unsettled): {n_total}")
    lines.append("</pre>")
    return lines


# ---------------------------------------------------------------------------
# Section 4 — universe expansion (T-leg fires)
# ---------------------------------------------------------------------------

def _section_universe_t_leg(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """SELECT observer, predicted_outcome, actual_outcome, features_json
           FROM crypto_observer_signals
           WHERE observer IN ('late_close_tail', 'mid_otm_tail')
             AND ts >= ?""",
        (COHORT_START_ISO,),
    ).fetchall()
    by_obs_kind: dict[tuple[str, str], dict] = {}
    for obs, pred, actual, fj in rows:
        try:
            f = json.loads(fj or "{}")
        except Exception:
            continue
        kind = f.get("kind") or "unknown"
        key = (obs, kind)
        cell = by_obs_kind.setdefault(key, {"n": 0, "settled": 0, "wins": 0})
        cell["n"] += 1
        if actual is not None:
            cell["settled"] += 1
            if pred == actual:
                cell["wins"] += 1
    lines = [
        "",
        "<b>4️⃣ Universe expansion T-leg fires</b>",
        "<i>Compare cumulative-kind WR vs bucketed-kind. T-legs should match B-legs.</i>",
        "<pre>",
        f"{'observer':>16s}  {'kind':>11s}  {'fires':>6s}  {'settled':>7s}  {'WR':>5s}",
    ]
    for (obs, kind) in sorted(by_obs_kind.keys()):
        c = by_obs_kind[(obs, kind)]
        wr = _wr(c["wins"], c["settled"])
        wr_s = f"{wr:>4.0f}%" if wr is not None else f"{'—':>5s}"
        lines.append(
            f"{obs:>16s}  {kind:>11s}  {c['n']:>6d}  {c['settled']:>7d}  {wr_s}"
        )
    lines.append("</pre>")
    return lines


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _build_report(conn: sqlite3.Connection) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = [
        f"<b>🗓️ 2026-05-27 eval snapshot — {now}</b>",
        f"<i>Cohort start: {COHORT_START_ISO}</i>",
        "",
    ]
    out += _section_cross_asset(conn)
    out += _section_cme_basis(conn)
    out += _section_mid_otm_tail(conn)
    out += _section_universe_t_leg(conn)
    out += [
        "",
        "<b>Next steps</b>",
        "• Promote / extend / retire per cohort's memory-defined bar",
        "• Disable this timer (one-shot at 2026-05-27 13:00 UTC) after review",
    ]
    return "\n".join(out)


async def _send_telegram(report: str) -> None:
    from core.config import Config
    cfg = Config()
    if not cfg.TELEGRAM_BOT_TOKEN or not cfg.TELEGRAM_CHAT_ID:
        logger.warning("No TELEGRAM credentials — printing instead.")
        print(report)
        return
    from monitoring.notifier import TelegramNotifier
    notifier = TelegramNotifier(cfg.TELEGRAM_BOT_TOKEN, cfg.TELEGRAM_CHAT_ID)
    # Telegram caps at ~4096 chars per message; split if needed.
    MAX = 3800
    parts = [report[i:i + MAX] for i in range(0, len(report), MAX)]
    for i, part in enumerate(parts):
        await notifier.send(part, msg_type=f"eval_2026_05_27_{i}")
    logger.info(f"Sent {len(parts)} Telegram message(s)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/bot.db")
    parser.add_argument("--dry-run", action="store_true", help="Print only")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        logger.error(f"DB not found: {args.db}")
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    try:
        report = _build_report(conn)
    finally:
        conn.close()

    plain = report
    for tag in ("<b>", "</b>", "<i>", "</i>", "<code>", "</code>", "<pre>", "</pre>"):
        plain = plain.replace(tag, "")
    print(plain)

    if not args.dry_run:
        asyncio.run(_send_telegram(report))


if __name__ == "__main__":
    main()
