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
import subprocess
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config
from core.database import Database

logger = logging.getLogger("daily_live_audit")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

CRIT_YESTERDAY = -30.0
CRIT_7D = -100.0
CRIT_30D_ABS = -300.0          # big absolute loss
CRIT_30D_ROI_PCT = -15.0       # OR persistent loser
CRIT_30D_ROI_MIN_N = 10        # only apply ROI criterion at this n+
WARN_LIVE_PAPER_ROI_GAP_PP = 5.0  # percentage points
MIN_N_FOR_COMPARISON = 20

# Per-strategy override thresholds + post-promo deploy anchors. Used to
# track accelerated-ladder strategies that need tighter watch than the
# generic CRIT_YESTERDAY/CRIT_7D/CRIT_30D bands. When present, the
# strategy-specific 3d + cumulative-since-deploy checks run IN ADDITION
# to generic checks; tightest violation wins.
#
# Schema per entry:
#   deploy_ts          — ISO UTC. Cumulative window starts here.
#   promote_3d_pnl     — 3d pnl at or above → "promote candidate" tag
#   warn_3d_pnl        — 3d pnl below this → warn
#   crit_3d_pnl        — 3d pnl at or below → critical (demote candidate)
#   crit_cum_pnl       — cumulative-since-deploy at or below → critical
#   note               — short label shown in audit output
STRATEGY_OVERRIDES = {
    "cme_basis_fade_v1": {
        "deploy_ts": "2026-05-21T16:11:31+00:00",
        "promote_3d_pnl": 30.0,
        "warn_3d_pnl": -30.0,
        "crit_3d_pnl": -60.0,
        "crit_cum_pnl": -100.0,
        "note": "J config (MAX_NO_ASK 0.85 + per-event cap 8)",
    },
}

# Kalshi 429 rate-limit thresholds (added 2026-05-21 after sports
# scan_loop race condition produced silent bursts undetected for ~5h).
# Surfaced in daily audit so any new burst class catches within 24h.
WARN_KALSHI_429_DAILY = 20      # recurring noise — investigate
CRIT_KALSHI_429_DAILY = 100     # real burst event — fix urgent


def _count_kalshi_429s(hours: int) -> tuple[int, dict[str, int]]:
    """Return (total_count, per_series_count) of Kalshi 429s in last N hours.
    Reads systemd journal for kalshi-bot.service. Returns (0, {}) if
    journalctl is unavailable (e.g. running off-host) or permission
    denied (user not in adm/systemd-journal group).
    """
    try:
        result = subprocess.run(
            ["journalctl", "-u", "kalshi-bot",
             f"--since={hours} hours ago", "--no-pager", "-q"],
            capture_output=True, text=True, timeout=45,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning(f"journalctl unavailable for 429 count: {e}")
        return 0, {}
    if result.returncode != 0:
        logger.warning(
            f"journalctl returncode={result.returncode} "
            f"stderr={result.stderr[:200]!r}"
        )
        return 0, {}
    total = 0
    per_series: dict[str, int] = {}
    for line in result.stdout.splitlines():
        if "429 Too Many Requests" not in line:
            continue
        total += 1
        i = line.find("series_ticker=")
        if i < 0:
            continue
        tail = line[i + len("series_ticker="):]
        # Strip at first non-ticker char (& or quote).
        series = tail.split("&")[0].split("'")[0].split('"')[0]
        per_series[series] = per_series.get(series, 0) + 1
    return total, per_series


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
        "3d": (now - timedelta(days=3)).isoformat(),
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
            override = STRATEGY_OVERRIDES.get(strat)
            if override:
                cur = await conn.execute(
                    _window_query(strat, "settled", override["deploy_ts"])
                )
                n, wins, pnl, stake = await cur.fetchone()
                rec["live_cum_n"] = n or 0
                rec["live_cum_wins"] = wins or 0
                rec["live_cum_pnl"] = pnl or 0.0
                rec["live_cum_stake"] = stake or 0.0
            rows[strat] = rec

    critical: list[tuple[str, str]] = []   # (strategy, reason)
    warning: list[tuple[str, str]] = []
    promote: list[tuple[str, str]] = []    # (strategy, reason) — size-up candidates
    ok: list[str] = []

    for strat, r in rows.items():
        flags: list[str] = []
        override = STRATEGY_OVERRIDES.get(strat)
        if override:
            # Tighter strategy-specific bands replace generic 1d/7d/30d.
            if r["live_3d_pnl"] <= override["crit_3d_pnl"]:
                flags.append(
                    f"3d=${r['live_3d_pnl']:+.0f} ≤ ${override['crit_3d_pnl']:+.0f} (demote)"
                )
            elif r["live_cum_pnl"] <= override["crit_cum_pnl"]:
                flags.append(
                    f"cum=${r['live_cum_pnl']:+.0f} ≤ ${override['crit_cum_pnl']:+.0f} "
                    f"since {override['deploy_ts'][:10]}"
                )
            if flags:
                critical.append((strat, " ".join(flags) + f" [{override['note']}]"))
                continue
            if (override["warn_3d_pnl"] < r["live_3d_pnl"]
                    < override["promote_3d_pnl"]):
                pass  # hold band — no flag
            elif r["live_3d_pnl"] <= override["warn_3d_pnl"]:
                warning.append((
                    strat,
                    f"3d=${r['live_3d_pnl']:+.0f} ≤ ${override['warn_3d_pnl']:+.0f} (warn) "
                    f"[{override['note']}]"
                ))
                continue
            elif (r["live_3d_pnl"] >= override["promote_3d_pnl"]
                  and r["live_cum_n"] >= 20):
                promote.append((
                    strat,
                    f"3d=${r['live_3d_pnl']:+.0f} ≥ ${override['promote_3d_pnl']:+.0f}, "
                    f"cum n={r['live_cum_n']} ${r['live_cum_pnl']:+.0f} "
                    f"[{override['note']}]"
                ))
                continue
            ok.append(strat)
            continue
        if r["live_1d_pnl"] < CRIT_YESTERDAY:
            flags.append(f"1d=${r['live_1d_pnl']:+.0f}")
        if r["live_7d_pnl"] < CRIT_7D:
            flags.append(f"7d=${r['live_7d_pnl']:+.0f}")
        live_30d_roi = (
            r["live_30d_pnl"] / r["live_30d_stake"] * 100
            if r["live_30d_stake"] > 0 else 0
        )
        if r["live_30d_pnl"] < CRIT_30D_ABS:
            flags.append(f"30d=${r['live_30d_pnl']:+.0f}")
        elif (
            r["live_30d_n"] >= CRIT_30D_ROI_MIN_N
            and live_30d_roi < CRIT_30D_ROI_PCT
        ):
            flags.append(
                f"30d_ROI={live_30d_roi:+.1f}% (n={r['live_30d_n']}, "
                f"pnl=${r['live_30d_pnl']:+.0f})"
            )
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

    if promote:
        lines.append(f"📈 <b>PROMOTE CANDIDATE ({len(promote)})</b>")
        for strat, reason in promote:
            r = rows[strat]
            cum_wr = (
                100 * r["live_cum_wins"] / r["live_cum_n"]
                if r["live_cum_n"] else 0
            )
            lines.append(
                f"  <b>{strat}</b>: {reason}"
                f"\n    cum WR={cum_wr:.0f}% n={r['live_cum_n']} stake=${r['live_cum_stake']:.0f}"
            )
        lines.append("")

    if not critical and not warning and not promote:
        lines.append(f"✅ All {len(ok)} LIVE strategies within thresholds.")
    elif ok:
        lines.append(f"✅ <i>OK ({len(ok)}):</i> {', '.join(ok)}")

    # ── Kalshi 429 rate-limit audit (added 2026-05-21) ────────────
    rl_total, rl_per_series = _count_kalshi_429s(24)
    lines.append("")
    if rl_total >= CRIT_KALSHI_429_DAILY:
        rl_icon = "🚨"
        rl_label = "CRITICAL"
    elif rl_total >= WARN_KALSHI_429_DAILY:
        rl_icon = "⚠️"
        rl_label = "WARN"
    else:
        rl_icon = "✅"
        rl_label = "OK"
    lines.append(f"{rl_icon} <b>Kalshi rate-limit:</b> {rl_total} × 429 in last 24h [{rl_label}]")
    if rl_total > 0 and rl_per_series:
        top = sorted(rl_per_series.items(), key=lambda x: -x[1])[:5]
        breakdown = ", ".join(f"{s}={n}" for s, n in top)
        lines.append(f"  top series: {breakdown}")

    lines.append("")
    lines.append(
        f"<i>Thresholds: 1d&lt;${CRIT_YESTERDAY:+.0f}, 7d&lt;${CRIT_7D:+.0f}, "
        f"30d&lt;${CRIT_30D_ABS:+.0f} or 30d ROI&lt;{CRIT_30D_ROI_PCT}% (n≥{CRIT_30D_ROI_MIN_N}), "
        f"LIVE-paper gap >{WARN_LIVE_PAPER_ROI_GAP_PP}pp, "
        f"Kalshi 429 ≥{WARN_KALSHI_429_DAILY}/day (warn) / ≥{CRIT_KALSHI_429_DAILY}/day (crit).</i>"
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
    logger.info(
        f"Telegram sent: {len(critical)} critical / {len(warning)} warning / "
        f"{len(promote)} promote / {len(ok)} OK"
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--send", action="store_true",
                   help="Send Telegram (default: console-only dry print).")
    args = p.parse_args()
    asyncio.run(audit(send=args.send))


if __name__ == "__main__":
    main()
