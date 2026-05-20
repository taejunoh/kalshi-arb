"""Daily silence watchdog for LIVE_STRATEGIES.

Catches the failure mode the tennis tuple bug + the
ladder_phase0_summary crash share: a strategy quietly stops firing
without producing errors. Compares each LIVE strategy's last 24h
fires against its prior-14d daily baseline and Telegrams any that
went silent despite a non-trivial baseline.

Fires daily at 13:00 UTC — after the WNBA window closes (01 UTC),
after US night-game settlement, before European afternoon sports
kick off. One Telegram per run.

CLI: python3 scripts/strategy_silence_watchdog.py [--db PATH] [--dry-run]

Memory: feedback_systemd_script_syspath_guard, strategy_priority.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

# Systemd runs us as `python scripts/foo.py` with WorkingDirectory at the
# repo root, but Python only puts scripts/ on sys.path automatically. Add
# the repo root so `from core.config import Config` resolves.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("strategy_silence_watchdog")

# Strategy flagged when prior-14d daily rate >= this AND last 24h fires == 0.
# 0.5 = "fires at least every other day on average" — below this, expected to
# be quiet sometimes.
MIN_BASELINE_DAILY_FIRES = 0.5

# Lookback windows.
BASELINE_DAYS = 14
RECENT_HOURS = 24

# Statuses that count as "the strategy fired" (signal generation + execution).
ACTIVITY_STATUSES = ("filled", "paper_filled")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    """SQLite-comparable ISO string with T separator (matches trades.timestamp)."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _audit_strategy(conn: sqlite3.Connection, strategy: str) -> dict:
    now = _now_utc()
    recent_cutoff = now - timedelta(hours=RECENT_HOURS)
    baseline_start = now - timedelta(days=BASELINE_DAYS)
    baseline_end = recent_cutoff  # baseline excludes the current 24h window

    placeholders = ",".join("?" * len(ACTIVITY_STATUSES))

    recent_n = conn.execute(
        f"""SELECT COUNT(*) FROM trades
            WHERE strategy = ? AND status IN ({placeholders})
              AND timestamp >= ?""",
        (strategy, *ACTIVITY_STATUSES, _iso(recent_cutoff)),
    ).fetchone()[0]

    baseline_n = conn.execute(
        f"""SELECT COUNT(*) FROM trades
            WHERE strategy = ? AND status IN ({placeholders})
              AND timestamp >= ? AND timestamp < ?""",
        (strategy, *ACTIVITY_STATUSES, _iso(baseline_start), _iso(baseline_end)),
    ).fetchone()[0]

    last_ts = conn.execute(
        f"""SELECT MAX(timestamp) FROM trades
            WHERE strategy = ? AND status IN ({placeholders})""",
        (strategy, *ACTIVITY_STATUSES),
    ).fetchone()[0]

    baseline_daily = baseline_n / BASELINE_DAYS
    silent = recent_n == 0 and baseline_daily >= MIN_BASELINE_DAILY_FIRES

    return {
        "strategy": strategy,
        "recent_24h": recent_n,
        "baseline_14d": baseline_n,
        "baseline_daily": baseline_daily,
        "last_ts": last_ts,
        "silent_anomaly": silent,
    }


def _hours_since(iso_ts: str | None) -> float | None:
    if not iso_ts:
        return None
    try:
        # trades.timestamp uses ISO with +00:00 or no tz
        ts = iso_ts.rstrip("Z").replace("+00:00", "")
        dt = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (_now_utc() - dt).total_seconds() / 3600.0


def _format_report(results: list[dict], live_strategies: list[str]) -> str:
    anomalies = [r for r in results if r["silent_anomaly"]]
    healthy = [r for r in results if not r["silent_anomaly"]]
    quiet_by_design = [r for r in healthy if r["baseline_daily"] < MIN_BASELINE_DAILY_FIRES]
    active = [r for r in healthy if r["baseline_daily"] >= MIN_BASELINE_DAILY_FIRES]

    lines = []
    if anomalies:
        lines.append(f"🚨 <b>Silence watchdog — {len(anomalies)} anomaly</b>")
    else:
        lines.append(f"✅ <b>Silence watchdog — all healthy</b>")
    lines.append(f"")
    lines.append(f"Window: last {RECENT_HOURS}h vs prior {BASELINE_DAYS}d baseline")
    lines.append(f"Threshold: ≥{MIN_BASELINE_DAILY_FIRES:.1f} fires/day baseline → 0 in 24h = flag")
    lines.append(f"")

    if anomalies:
        lines.append("<b>🚨 ANOMALIES</b>")
        for r in anomalies:
            hrs = _hours_since(r["last_ts"])
            hrs_s = f"{hrs:.0f}h ago" if hrs is not None else "never"
            lines.append(
                f"  <code>{r['strategy']}</code> "
                f"24h={r['recent_24h']} | baseline {r['baseline_daily']:.1f}/day "
                f"({r['baseline_14d']} in 14d) | last={hrs_s}"
            )
        lines.append("")

    if active:
        lines.append("<b>✅ Active LIVE</b>")
        for r in active:
            lines.append(
                f"  <code>{r['strategy']}</code> "
                f"24h={r['recent_24h']} | base {r['baseline_daily']:.1f}/day"
            )
        lines.append("")

    if quiet_by_design:
        lines.append("<b>💤 Quiet by design (baseline &lt; threshold)</b>")
        for r in quiet_by_design:
            lines.append(
                f"  <code>{r['strategy']}</code> "
                f"24h={r['recent_24h']} | base {r['baseline_daily']:.2f}/day"
            )

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
    await notifier.send(report, msg_type="strategy_silence_watchdog")
    logger.info(f"Sent Telegram message, {len(report)} chars.")


def _live_strategies() -> list[str]:
    """Read LIVE_STRATEGIES from Config (comma-separated env var)."""
    from core.config import Config
    cfg = Config()
    raw = getattr(cfg, "LIVE_STRATEGIES", None) or os.environ.get("LIVE_STRATEGIES", "")
    if isinstance(raw, (list, tuple, set)):
        return [s.strip() for s in raw if s.strip()]
    return [s.strip() for s in str(raw).split(",") if s.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/bot.db")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print to stdout, skip Telegram")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        logger.error(f"DB not found: {args.db}")
        sys.exit(1)

    live = _live_strategies()
    if not live:
        logger.error("LIVE_STRATEGIES env empty — nothing to watch.")
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    try:
        results = [_audit_strategy(conn, s) for s in live]
    finally:
        conn.close()

    report = _format_report(results, live)
    if args.dry_run:
        print(report)
        return
    asyncio.run(_send_telegram(report))


if __name__ == "__main__":
    main()
