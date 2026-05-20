"""Weekly LIVE-vs-paper divergence + slow-decay alert.

Complement to scripts/daily_live_audit.py. The daily audit catches
sharp drops (1d/7d $ thresholds). This one catches the *slow-decay*
pattern that masked v32 for 18 days: each individual day fell below
the 1d critical threshold, but the cumulative slope was clearly
negative against the 4-week baseline.

Fires Mondays 09:00 UTC. One Telegram per run.

Two signal classes per LIVE strategy:

  • ROI slope: last 7d ROI vs trailing 4w ROI (excluding the last 7d).
    Flag when slope_pct <= -10pp AND n7 >= 5 — i.e. the strategy lost
    at least 10pp of ROI in the last week relative to its prior month.
    Catches v32-style slow-decay where each day's drop is below
    daily-audit thresholds but the trend is clear.

  • LIVE vs paper-twin: 4w live ROI - 4w paper ROI.  Flag when paper
    leads by >= 8pp AND both n >= 15.  Catches persistent slippage /
    regime drift (already half-covered by daily_live_audit's 30d gap,
    but with a tighter gap threshold to surface medium-confidence
    leaks earlier).

CLI: python3 scripts/weekly_live_paper_divergence.py [--send]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("weekly_live_paper_divergence")

# Slow-decay slope: last 7d ROI must be at least this much worse than
# the trailing 4w (excluding the last 7d). In percentage points.
ROI_SLOPE_DROP_PP = 10.0
SLOPE_MIN_N_RECENT = 5      # need at least this many fires in the last 7d

# LIVE vs paper divergence (4w window).
GAP_PP = 8.0
GAP_MIN_N = 15

LIVE_STATUSES = ("settled",)
PAPER_STATUSES = ("paper_settled",)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _window_stats(
    conn: sqlite3.Connection,
    strategy: str,
    statuses: tuple[str, ...],
    since: datetime,
    until: datetime | None = None,
) -> tuple[int, int, float, float]:
    placeholders = ",".join("?" * len(statuses))
    sql = (
        f"SELECT COUNT(*), "
        f"  SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END), "
        f"  COALESCE(SUM(pnl), 0), "
        f"  COALESCE(SUM(size), 0) "
        f"FROM trades WHERE strategy = ? AND status IN ({placeholders}) "
        f"  AND timestamp >= ?"
    )
    params: list = [strategy, *statuses, _iso(since)]
    if until is not None:
        sql += " AND timestamp < ?"
        params.append(_iso(until))
    n, wins, pnl, stake = conn.execute(sql, params).fetchone()
    return (n or 0, wins or 0, float(pnl or 0.0), float(stake or 0.0))


def _roi(pnl: float, stake: float) -> float:
    return (pnl / stake * 100.0) if stake > 0 else 0.0


def _audit(conn: sqlite3.Connection, strategy: str) -> dict:
    now = _now_utc()
    last_7d = now - timedelta(days=7)
    last_28d = now - timedelta(days=28)

    # Last 7d (recent window)
    n7, w7, pnl7, stake7 = _window_stats(conn, strategy, LIVE_STATUSES, last_7d)
    # Trailing 21d (4w baseline excluding last 7d)
    n21, w21, pnl21, stake21 = _window_stats(conn, strategy, LIVE_STATUSES, last_28d, last_7d)

    # 4w totals (live + paper) for gap check
    n_live_4w, _, pnl_live_4w, stake_live_4w = _window_stats(conn, strategy, LIVE_STATUSES, last_28d)
    n_paper_4w, _, pnl_paper_4w, stake_paper_4w = _window_stats(conn, strategy, PAPER_STATUSES, last_28d)

    roi7 = _roi(pnl7, stake7)
    roi21 = _roi(pnl21, stake21)
    roi_live_4w = _roi(pnl_live_4w, stake_live_4w)
    roi_paper_4w = _roi(pnl_paper_4w, stake_paper_4w)

    slope_drop_pp = roi21 - roi7  # positive = recent worse than baseline
    paper_lead_pp = roi_paper_4w - roi_live_4w

    slope_flag = (n7 >= SLOPE_MIN_N_RECENT) and (slope_drop_pp >= ROI_SLOPE_DROP_PP)
    gap_flag = (
        n_live_4w >= GAP_MIN_N
        and n_paper_4w >= GAP_MIN_N
        and paper_lead_pp >= GAP_PP
    )

    return {
        "strategy": strategy,
        "n7": n7, "pnl7": pnl7, "roi7": roi7,
        "n21": n21, "pnl21": pnl21, "roi21": roi21,
        "slope_drop_pp": slope_drop_pp,
        "slope_flag": slope_flag,
        "n_live_4w": n_live_4w, "pnl_live_4w": pnl_live_4w, "roi_live_4w": roi_live_4w,
        "n_paper_4w": n_paper_4w, "pnl_paper_4w": pnl_paper_4w, "roi_paper_4w": roi_paper_4w,
        "paper_lead_pp": paper_lead_pp,
        "gap_flag": gap_flag,
    }


def _live_strategies() -> list[str]:
    from core.config import Config
    cfg = Config()
    raw = getattr(cfg, "LIVE_STRATEGIES", None) or os.environ.get("LIVE_STRATEGIES", "")
    if isinstance(raw, (list, tuple, set)):
        items = [s.strip() for s in raw if s.strip()]
    else:
        items = [s.strip() for s in str(raw).split(",") if s.strip()]
    return sorted(s for s in items if s != "sports_trader")


def _format_report(results: list[dict]) -> str:
    now = _now_utc()
    slope_alerts = [r for r in results if r["slope_flag"]]
    gap_alerts = [r for r in results if r["gap_flag"] and not r["slope_flag"]]
    ok = [r for r in results if not r["slope_flag"] and not r["gap_flag"]]

    lines: list[str] = [
        f"<b>📈 Weekly LIVE divergence — {now.strftime('%Y-%m-%d %H:%M UTC')}</b>",
        "",
    ]

    if slope_alerts:
        lines.append(f"🚨 <b>SLOPE DROP ({len(slope_alerts)})</b> — 7d ROI &lt;&lt; trailing 21d")
        for r in slope_alerts:
            lines.append(
                f"  <b>{r['strategy']}</b>: 7d ROI {r['roi7']:+.1f}% (n={r['n7']}, "
                f"${r['pnl7']:+.0f}) vs 21d {r['roi21']:+.1f}% (n={r['n21']}) "
                f"→ Δ {r['slope_drop_pp']:+.1f}pp"
            )
        lines.append("")

    if gap_alerts:
        lines.append(f"⚠️ <b>PAPER LEAD ({len(gap_alerts)})</b> — paper twin &gt; LIVE by ≥{GAP_PP:.0f}pp over 4w")
        for r in gap_alerts:
            lines.append(
                f"  <b>{r['strategy']}</b>: LIVE {r['roi_live_4w']:+.1f}% (n={r['n_live_4w']}) "
                f"vs paper {r['roi_paper_4w']:+.1f}% (n={r['n_paper_4w']}) "
                f"→ gap {r['paper_lead_pp']:+.1f}pp"
            )
        lines.append("")

    if not slope_alerts and not gap_alerts:
        lines.append(f"✅ All {len(ok)} LIVE strategies within weekly thresholds.")
    elif ok:
        ok_names = ", ".join(r["strategy"] for r in ok)
        lines.append(f"✅ <i>OK ({len(ok)}):</i> {ok_names}")

    lines.append("")
    lines.append(
        f"<i>Thresholds: slope drop ≥{ROI_SLOPE_DROP_PP:.0f}pp (n7≥{SLOPE_MIN_N_RECENT}), "
        f"paper lead ≥{GAP_PP:.0f}pp (both n≥{GAP_MIN_N}).</i>"
    )
    return "\n".join(lines)


async def _send_telegram(report: str) -> None:
    from core.config import Config
    cfg = Config()
    if not cfg.TELEGRAM_BOT_TOKEN or not cfg.TELEGRAM_CHAT_ID:
        logger.warning("No TELEGRAM credentials — printing instead.")
        print(report)
        return
    from monitoring.notifier import TelegramNotifier
    notifier = TelegramNotifier(cfg.TELEGRAM_BOT_TOKEN, cfg.TELEGRAM_CHAT_ID)
    await notifier.send(report, msg_type="weekly_divergence")
    logger.info(f"Sent Telegram, {len(report)} chars")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/bot.db")
    parser.add_argument("--send", action="store_true",
                        help="Send Telegram (default: print only)")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        logger.error(f"DB not found: {args.db}")
        sys.exit(1)

    live = _live_strategies()
    if not live:
        logger.error("LIVE_STRATEGIES empty — nothing to audit.")
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    try:
        results = [_audit(conn, s) for s in live]
    finally:
        conn.close()

    report = _format_report(results)
    # Always echo the rendered report (with tags stripped) for journald.
    plain = report
    for tag in ("<b>", "</b>", "<i>", "</i>", "<code>", "</code>"):
        plain = plain.replace(tag, "")
    print(plain)

    if args.send:
        asyncio.run(_send_telegram(report))


if __name__ == "__main__":
    main()
