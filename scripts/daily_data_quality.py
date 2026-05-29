"""Daily data quality monitor for the sports observer pipeline.

Reports per-sport health for `odds_observations` rows captured in the last 24h:
- row count + match rate proxy (rows / expected rows)
- n_books_eu p10 / p50 (coverage signal — low p10 means thin-coverage games slipped in)
- has_pinnacle % (Pinnacle's T-24h posting pattern means lower for far-future games)
- Kalshi median open_interest + volume_24h (proxy for tradability)
- Soccer 3-way leg balance (HOME/AWAY/TIE counts must match per game; mismatch = team alias gap)
- Settlement backfill lag (oldest unsettled row whose game has clearly ended)

Sends Telegram if any sport degrades >25% vs its 7-day rolling baseline.
Designed for systemd timer / cron after the 10:15 UTC settlement backfill.

Usage:
    venv/bin/python scripts/daily_data_quality.py            # read default DB, send Telegram
    venv/bin/python scripts/daily_data_quality.py --dry-run  # print to stdout only
    venv/bin/python scripts/daily_data_quality.py --db PATH  # custom DB path
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("daily_data_quality")

DEGRADATION_THRESHOLD = 0.25  # 25% drop vs 7d baseline triggers alert


def _percentile(values, p):
    if not values:
        return None
    s = sorted(values)
    k = int(len(s) * p)
    k = min(k, len(s) - 1)
    return s[k]


def _per_sport_stats(conn: sqlite3.Connection, hours: int) -> dict:
    """Return {sport: stats_dict} for rows captured in last `hours` hours."""
    cur = conn.execute(
        f"""SELECT sport, n_books_eu, n_books_us, has_pinnacle,
                  kalshi_open_interest, kalshi_volume_24h,
                  is_yes_home, is_yes_draw, kalshi_ticker, series
            FROM odds_observations
            WHERE timestamp >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-{int(hours)} hours')"""
    )
    rows = cur.fetchall()
    stats: dict = {}
    for r in rows:
        s = r[0]
        if s not in stats:
            stats[s] = {
                "n": 0, "books_eu": [], "books_us": [], "pin": 0,
                "oi": [], "vol24": [],
                "home_legs": 0, "away_legs": 0, "tie_legs": 0,
                "tickers": set(),
            }
        d = stats[s]
        d["n"] += 1
        if r[1] is not None:
            d["books_eu"].append(r[1])
        if r[2] is not None:
            d["books_us"].append(r[2])
        if r[3]:
            d["pin"] += 1
        if r[4] is not None:
            d["oi"].append(r[4])
        if r[5] is not None:
            d["vol24"].append(r[5])
        is_yes_home, is_yes_draw = r[6], r[7]
        if is_yes_draw == 1:
            d["tie_legs"] += 1
        elif is_yes_home == 1:
            d["home_legs"] += 1
        elif is_yes_home == 0:
            d["away_legs"] += 1
        d["tickers"].add(r[8])
    return stats


def _settlement_lag(conn: sqlite3.Connection) -> dict:
    """Tickers seen by observer but not in ticker_settlements where the game
    has clearly ended (commence_time + 6h < now). High count = backfill broken."""
    cur = conn.execute(
        """SELECT COUNT(DISTINCT o.kalshi_ticker)
           FROM odds_observations o
           LEFT JOIN ticker_settlements s ON o.kalshi_ticker = s.kalshi_ticker
           WHERE s.kalshi_ticker IS NULL
             AND o.timestamp <= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-6 hours')
             AND o.timestamp >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-7 days')"""
    )
    row = cur.fetchone()
    return {"unsettled_old_tickers": row[0] if row else 0}


def _format_stats_block(label: str, stats: dict) -> str:
    """Per-sport tabular summary (Telegram HTML-safe)."""
    if not stats:
        return f"<b>{label}</b>: (no rows)"
    lines = [f"<b>{label}</b>"]
    lines.append("<pre>")
    lines.append(f"{'sport':5s} {'rows':>5s} {'books_p10':>9s} {'p50':>4s} {'pin%':>5s} {'oi_med':>7s} {'leg_check':>10s}")
    for sport in sorted(stats):
        d = stats[sport]
        eu = d["books_eu"]
        eu_p10 = _percentile(eu, 0.10) if eu else 0
        eu_p50 = _percentile(eu, 0.50) if eu else 0
        pin_pct = (d["pin"] / d["n"] * 100) if d["n"] else 0
        oi_med = _percentile(d["oi"], 0.50) if d["oi"] else 0
        # Leg balance: for 3-way sports, home == away == tie counts ideally.
        # 2-way sports: home == away, tie should be 0.
        h, a, t = d["home_legs"], d["away_legs"], d["tie_legs"]
        if t > 0:
            # 3-way
            mn = min(h, a, t)
            mx = max(h, a, t)
            leg = "OK" if mn == mx else f"!{h}/{a}/{t}"
        else:
            # 2-way
            leg = "OK" if h == a else f"!{h}/{a}"
        lines.append(
            f"{sport:5s} {d['n']:>5d} {eu_p10 or 0:>9d} {eu_p50 or 0:>4d} "
            f"{pin_pct:>4.0f}% {int(oi_med or 0):>7d} {leg:>10s}"
        )
    lines.append("</pre>")
    return "\n".join(lines)


def _detect_degradation(today: dict, baseline: dict) -> list[str]:
    """Compare today's stats vs 7d baseline. Return alert lines."""
    alerts = []
    for sport, t in today.items():
        b = baseline.get(sport)
        if not b or b["n"] < 10:
            continue  # no useful baseline
        # 1. Row count drop
        expected = b["n"] / 7.0  # daily avg over 7d
        if expected >= 50 and t["n"] < expected * (1 - DEGRADATION_THRESHOLD):
            alerts.append(f"⚠️ {sport}: rows {t['n']} < {expected:.0f} (baseline daily avg) — coverage drop?")
        # 2. n_books p50 drop
        b_books_p50 = _percentile(b["books_eu"], 0.5) if b["books_eu"] else None
        t_books_p50 = _percentile(t["books_eu"], 0.5) if t["books_eu"] else None
        if b_books_p50 and t_books_p50 and t_books_p50 < b_books_p50 * (1 - DEGRADATION_THRESHOLD):
            alerts.append(f"⚠️ {sport}: n_books p50 {t_books_p50} < {b_books_p50} (baseline) — Odds API coverage drop")
        # 3. Pinnacle % drop
        b_pin_pct = (b["pin"] / b["n"] * 100) if b["n"] else 0
        t_pin_pct = (t["pin"] / t["n"] * 100) if t["n"] else 0
        if b_pin_pct >= 50 and t_pin_pct < b_pin_pct - 25:
            alerts.append(f"⚠️ {sport}: Pinnacle {t_pin_pct:.0f}% << {b_pin_pct:.0f}% (baseline)")
        # 4. Soccer leg mismatch — only flag if today's mismatch is clearly worse
        h, a, t_legs = t["home_legs"], t["away_legs"], t["tie_legs"]
        if t_legs > 0:  # 3-way sport
            mn, mx = min(h, a, t_legs), max(h, a, t_legs)
            if mx > 0 and (mx - mn) / mx > 0.10:  # >10% leg imbalance
                alerts.append(
                    f"⚠️ {sport}: 3-way leg imbalance H={h} A={a} T={t_legs} — likely team-name alias gap"
                )
    return alerts


def _build_report(db_path: str) -> tuple[str, list[str]]:
    conn = sqlite3.connect(db_path, timeout=30.0)
    today = _per_sport_stats(conn, hours=24)
    baseline = _per_sport_stats(conn, hours=24 * 7)
    settle = _settlement_lag(conn)
    conn.close()

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"📊 <b>Sports observer data quality — {now_iso}</b>", ""]
    lines.append(_format_stats_block("Last 24h per-sport", today))
    lines.append("")

    # Settlement backfill health
    lag = settle["unsettled_old_tickers"]
    if lag == 0:
        lines.append("✅ <b>Settlement backfill</b>: 0 unsettled tickers (>6h old)")
    elif lag < 20:
        lines.append(f"🟡 <b>Settlement backfill</b>: {lag} unsettled tickers (>6h old) — check next 10:15 UTC run")
    else:
        lines.append(f"🔴 <b>Settlement backfill</b>: {lag} unsettled tickers (>6h old) — backfill timer may be stuck")

    alerts = _detect_degradation(today, baseline)
    if alerts:
        lines.append("")
        lines.append("<b>🚨 Degradation alerts (vs 7d baseline)</b>")
        for a in alerts:
            lines.append(a)
    else:
        lines.append("")
        lines.append("✅ No degradation alerts vs 7d baseline.")

    return "\n".join(lines), alerts


async def send(report: str):
    from core.config import Config
    cfg = Config()
    if not cfg.TELEGRAM_BOT_TOKEN or not cfg.TELEGRAM_CHAT_ID:
        logger.warning("No TELEGRAM credentials — printing to stdout instead.")
        print(report)
        return
    from monitoring.notifier import TelegramNotifier
    notifier = TelegramNotifier(cfg.TELEGRAM_BOT_TOKEN, cfg.TELEGRAM_CHAT_ID)
    MAX = 3800
    parts = [report[i:i + MAX] for i in range(0, len(report), MAX)]
    for i, part in enumerate(parts):
        await notifier.send(part, msg_type=f"data_quality_{i}")
    logger.info(f"Sent {len(parts)} Telegram message(s), {len(report)} chars total.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/bot.db", help="Path to bot.db")
    parser.add_argument("--dry-run", action="store_true", help="Print to stdout, skip Telegram")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        logger.error(f"DB not found: {args.db}")
        sys.exit(1)

    report, alerts = _build_report(args.db)
    if args.dry_run:
        print(report)
        if alerts:
            sys.exit(2)
        return
    asyncio.run(send(report))
    if alerts:
        sys.exit(2)


if __name__ == "__main__":
    main()
