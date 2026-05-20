"""Daily LIVE PnL health audit + Telegram alert.

Built 2026-05-20 night after discovering ~18 days of silent v32 LIVE bleed
(-$513) plus 4+ other strategies underperforming paper twin for weeks
(NBA playoff -$473, underdog_v31 -$264, soccer_draw -$72, atm_hold -$74).
The PnL bug in [[live-pnl-realized-bug-2026-05-20]] masked some of it on
the dashboard; the rest was simply never reviewed.

This script runs daily and surfaces:

  • 1-day per-strategy LIVE PnL (yesterday's settlements)
  • 7-day rolling LIVE PnL + WR
  • 30-day rolling LIVE PnL + WR + ROI
  • LIVE-vs-paper twin gap (30d, n>=20) — slip / regime divergence detector

Threshold-based flags:

  🚨  CRITICAL  yesterday < -$30 OR 7d < -$100 OR 30d < -$300
  ⚠️  WARNING   LIVE 30d ROI < paper 30d ROI - 5pp (with both n>=20)
  ✅  OK        otherwise

Telegram-formatted summary sent via TelegramNotifier (msg_type='live_audit').

Idempotent. Read-only on the DB.

Run modes:
  --console  Print to stdout only (no Telegram send). Default for dry-test.
  --send     Send Telegram and print. Used by the systemd timer.
"""
import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config
from core.database import Database

logger = logging.getLogger("daily_live_audit")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

CRIT_YESTERDAY = -30.0
CRIT_7D = -100.0
CRIT_30D = -300.0
WARN_LIVE_PAPER_ROI_GAP_PP = 5.0  # percentage points
MIN_N_FOR_COMPARISON = 20


def _window_query(strategy: str, status: str, since_iso: str) -> str:
    return (
        f"SELECT COUNT(*) AS n, "
        f"  SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins, "
        f"  ROUND(COALESCE(SUM(pnl), 0), 2) AS pnl, "
        f"  ROUND(COALESCE(SUM(size), 0), 2) AS stake "
        f"FROM trades WHERE strategy = '{strategy}' AND status = '{status}' "
        f"  AND timestamp >= '{since_iso}'"
    )


async def audit(send: bool):
    config = Config()
    live_strategies = sorted(set(config.LIVE_STRATEGIES) - {"sports_trader"})
    if not live_strategies:
        logger.info("LIVE_STRATEGIES empty — nothing to audit.")
        return

    db = Database()
    await db.initialize()

    import aiosqlite
    now = datetime.now(timezone.utc)
    cutoffs = {
        "1d": (now - timedelta(days=1)).isoformat(),
        "7d": (now - timedelta(days=7)).isoformat(),
        "30d": (now - timedelta(days=30)).isoformat(),
    }

    rows: dict[str, dict] = {}
    async with aiosqlite.connect(db.db_path, timeout=30.0) as conn:
        for strat in live_strategies:
            rec = {"strategy": strat}
            for win_name, since in cutoffs.items():
                for status_label, status in (("live", "settled"), ("paper", "paper_settled")):
                    cur = await conn.execute(_window_query(strat, status, since))
                    n, wins, pnl, stake = await cur.fetchone()
                    rec[f"{status_label}_{win_name}_n"] = n or 0
                    rec[f"{status_label}_{win_name}_wins"] = wins or 0
                    rec[f"{status_label}_{win_name}_pnl"] = pnl or 0.0
                    rec[f"{status_label}_{win_name}_stake"] = stake or 0.0
            rows[strat] = rec

    critical: list[tuple[str, str]] = []   # (strategy, reason)
    warning: list[tuple[str, str]] = []
    ok: list[str] = []

    for strat, r in rows.items():
        flags: list[str] = []
        if r["live_1d_pnl"] < CRIT_YESTERDAY:
            flags.append(f"1d=${r['live_1d_pnl']:+.0f} (<${CRIT_YESTERDAY:+.0f})")
        if r["live_7d_pnl"] < CRIT_7D:
            flags.append(f"7d=${r['live_7d_pnl']:+.0f} (<${CRIT_7D:+.0f})")
        if r["live_30d_pnl"] < CRIT_30D:
            flags.append(f"30d=${r['live_30d_pnl']:+.0f} (<${CRIT_30D:+.0f})")
        if flags:
            critical.append((strat, " ".join(flags)))
            continue

        # LIVE vs paper divergence check (n>=20 both sides)
        if r["live_30d_n"] >= MIN_N_FOR_COMPARISON and r["paper_30d_n"] >= MIN_N_FOR_COMPARISON:
            live_roi = (r["live_30d_pnl"] / r["live_30d_stake"] * 100) if r["live_30d_stake"] > 0 else 0
            paper_roi = (r["paper_30d_pnl"] / r["paper_30d_stake"] * 100) if r["paper_30d_stake"] > 0 else 0
            gap = paper_roi - live_roi
            if gap > WARN_LIVE_PAPER_ROI_GAP_PP:
                warning.append((
                    strat,
                    f"LIVE ROI {live_roi:+.1f}% vs paper {paper_roi:+.1f}% (gap {gap:.1f}pp)"
                ))
                continue
        ok.append(strat)

    # Format Telegram message
    lines = [f"<b>📊 Daily LIVE Audit — {now.strftime('%Y-%m-%d %H:%M UTC')}</b>", ""]

    if critical:
        lines.append(f"🚨 <b>CRITICAL ({len(critical)})</b>")
        for strat, reason in critical:
            r = rows[strat]
            wr = 100 * r["live_30d_wins"] / r["live_30d_n"] if r["live_30d_n"] else 0
            lines.append(
                f"  <b>{strat}</b>: {reason}"
                f"\n    30d n={r['live_30d_n']} WR={wr:.0f}% stake=${r['live_30d_stake']:.0f}"
            )
        lines.append("")

    if warning:
        lines.append(f"⚠️ <b>WARNING ({len(warning)})</b>")
        for strat, reason in warning:
            r = rows[strat]
            lines.append(
                f"  <b>{strat}</b>: {reason}"
                f"\n    LIVE 30d n={r['live_30d_n']} pnl=${r['live_30d_pnl']:+.0f}"
                f" / paper n={r['paper_30d_n']} pnl=${r['paper_30d_pnl']:+.0f}"
            )
        lines.append("")

    if not critical and not warning:
        lines.append(f"✅ All {len(ok)} LIVE strategies within thresholds.")
    elif ok:
        lines.append(f"✅ <i>OK ({len(ok)}):</i> {', '.join(ok)}")

    lines.append("")
    lines.append(
        f"<i>Thresholds: 1d<${CRIT_YESTERDAY:+.0f}, 7d<${CRIT_7D:+.0f}, "
        f"30d<${CRIT_30D:+.0f}, LIVE-paper gap >{WARN_LIVE_PAPER_ROI_GAP_PP}pp.</i>"
    )

    msg = "\n".join(lines)
    print(msg.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", ""))

    if not send:
        return

    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        logger.warning("Telegram credentials missing — skipping send.")
        return

    from monitoring.notifier import TelegramNotifier
    notifier = TelegramNotifier(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
    await notifier.send(msg, msg_type="live_audit")
    logger.info(f"Telegram sent: {len(critical)} critical / {len(warning)} warning / {len(ok)} OK")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--send", action="store_true",
                   help="Send Telegram (default: console-only dry print).")
    args = p.parse_args()
    asyncio.run(audit(send=args.send))


if __name__ == "__main__":
    main()
