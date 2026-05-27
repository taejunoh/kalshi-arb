"""Morning settle-digest — overnight wake-up report.

Runs once at 07:30 UTC (03:30 EDT), 1h before the existing morning_report.
Distinct purpose: post-overnight-settle snapshot of TODAY's deploys, with
a focus on:
  - Same-day paper strategy fires + which ones settled + WR/PnL so far
  - LIVE 24h PnL delta + position count
  - Silence checks (any LIVE strategy quiet >12h while sports are active)
  - Stablecoin collector accumulation (overnight events)
  - Tennis_pinnacle_fade / npb_sharp_follow dedup verification

Writes Markdown to /home/ubuntu/kalshi-arb/data/morning_digest.md.
Does NOT send Telegram (user is in quiet-hours window 02:00-09:00 UTC).
"""
import os
import sqlite3
from datetime import datetime, timezone

DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")
OUT = os.getenv("MORNING_DIGEST_PATH", "/home/ubuntu/kalshi-arb/data/morning_digest.md")


def q(sql, params=()):
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA query_only = 1")
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def fmt_money(v):
    if v is None or v == 0:
        return "$0"
    return f"+${v:.2f}" if v > 0 else f"-${abs(v):.2f}"


def section_today_deploys():
    """5/26 PM deploys: how did first ~12h go after settle?"""
    lines = ["## Today's deploys — overnight settle results", ""]
    strategies = [
        ("sports_tennis_pinnacle_fade_v1", "Tennis Pinnacle fade (deployed 14:41 UTC + dedup 20:01)"),
        ("sports_npb_sharp_follow_v1", "NPB sharp-follow (15:01 + dedup 20:01)"),
        ("sports_lal_sharp_follow_v1", "LAL sharp-follow (15:01, idle until 8월)"),
        ("sports_mlb_pitcher_edge_v2_1", "MLB pitcher v2.1 (tier sizing)"),
        ("sports_mlb_pitcher_edge_v2", "MLB pitcher v2"),
        ("sports_mlb_pitcher_edge_v1", "MLB pitcher v1 (look-ahead biased baseline)"),
        ("sports_tennis_chalk_v1", "Tennis chalk (35d run)"),
    ]
    lines.append("| Strategy | Fires | Settled | WR | PnL |")
    lines.append("|---|---:|---:|---:|---:|")
    for strat, label in strategies:
        rows = q("""
            SELECT
              COUNT(*) AS fires,
              SUM(CASE WHEN status IN ('paper_settled','settled') THEN 1 ELSE 0 END) AS settled,
              SUM(CASE WHEN status IN ('paper_settled','settled') AND pnl > 0 THEN 1 ELSE 0 END) AS wins,
              SUM(CASE WHEN status IN ('paper_settled','settled') AND pnl < 0 THEN 1 ELSE 0 END) AS losses,
              ROUND(SUM(CASE WHEN status IN ('paper_settled','settled') THEN pnl ELSE 0 END), 2) AS pnl
            FROM trades
            WHERE strategy = ? AND timestamp >= datetime('now', '-18 hours')
        """, (strat,))[0]
        fires, settled, wins, losses, pnl = rows
        if not fires:
            continue
        wl = (wins or 0) + (losses or 0)
        wr = f"{100 * (wins or 0) / wl:.0f}%" if wl else "—"
        lines.append(f"| {label} | {fires} | {settled or 0} | {wr} | {fmt_money(pnl)} |")
    return "\n".join(lines)


def section_live_24h():
    """LIVE strategy 24h activity + PnL delta."""
    lines = ["## LIVE (real money) — last 24h", ""]
    rows = q("""
        SELECT
          strategy,
          COUNT(*) AS fires,
          SUM(CASE WHEN status='settled' THEN 1 ELSE 0 END) AS settled,
          SUM(CASE WHEN status='filled' THEN 1 ELSE 0 END) AS open_now,
          SUM(CASE WHEN status='settled' AND pnl > 0 THEN 1 ELSE 0 END) AS wins,
          SUM(CASE WHEN status='settled' AND pnl < 0 THEN 1 ELSE 0 END) AS losses,
          ROUND(SUM(CASE WHEN status='settled' THEN pnl ELSE 0 END), 2) AS pnl
        FROM trades
        WHERE timestamp >= datetime('now', '-24 hours')
          AND status NOT LIKE 'paper%'
          AND strategy IN ('sports_longshot_v31','sports_midshot_v31','sports_underdog_v31','sports_underdog_v37')
        GROUP BY strategy ORDER BY strategy
    """)
    if not rows:
        lines.append("_No LIVE activity in the last 24h._")
        return "\n".join(lines)
    lines.append("| Strategy | Fires | Settled | Open | WR | PnL |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    total_pnl = 0.0
    for strat, fires, settled, open_now, wins, losses, pnl in rows:
        wl = (wins or 0) + (losses or 0)
        wr = f"{100 * (wins or 0) / wl:.0f}%" if wl else "—"
        total_pnl += pnl or 0
        lines.append(f"| {strat.replace('sports_', '')} | {fires} | {settled or 0} | {open_now or 0} | {wr} | {fmt_money(pnl)} |")
    lines.append(f"| **Total** | | | | | **{fmt_money(total_pnl)}** |")
    return "\n".join(lines)


def section_dedup_check():
    """Verify tennis_pinnacle_fade + npb_sharp_follow dedup is working post-fix."""
    lines = ["## Dedup verification — post 20:01 UTC fix", ""]
    # Tennis: count fires by match_key derived from ticker
    # NPB ticker pattern: KXNPBGAME-XXX-AAA / KXNPBGAME-XXX-BBB (mirror)
    # Tennis pattern: similar mirror
    # If dedup working: no two fires for same date with same teams pre-game.
    for strat in ("sports_tennis_pinnacle_fade_v1", "sports_npb_sharp_follow_v1"):
        rows = q("""
            SELECT market_id, timestamp
            FROM trades
            WHERE strategy = ? AND timestamp >= '2026-05-26T20:01:00'
            ORDER BY timestamp
        """, (strat,))
        if not rows:
            lines.append(f"- **{strat}**: 0 fires post-dedup ✓")
            continue
        # Group by inferred match_key (everything before last "-XXX" suffix)
        import re
        match_groups = {}
        for mid, ts in rows:
            mk = re.sub(r"-[A-Z0-9]{2,5}$", "", mid)
            match_groups.setdefault(mk, []).append((mid, ts))
        dupes = [mk for mk, fires in match_groups.items() if len(fires) > 1]
        status = "✓" if not dupes else f"🚨 {len(dupes)} duplicate match_keys"
        lines.append(f"- **{strat}**: {len(rows)} fires across {len(match_groups)} matches {status}")
        if dupes:
            for mk in dupes[:3]:
                lines.append(f"  - {mk}: fires {[t for _, t in match_groups[mk]]}")
    return "\n".join(lines)


def section_silence():
    """LIVE strategies that haven't fired in >12h while related sports are active."""
    lines = ["## Silence check (LIVE strategies)", ""]
    # Just check LIVE strategies haven't gone fully silent overnight.
    # MLB games run through 04 UTC, sports activity expected within last 12h.
    rows = q("""
        SELECT strategy, MAX(timestamp) AS last_fire
        FROM trades
        WHERE strategy IN ('sports_longshot_v31','sports_midshot_v31','sports_underdog_v31','sports_underdog_v37')
          AND status NOT LIKE 'paper%'
        GROUP BY strategy
    """)
    now = datetime.now(timezone.utc)
    issues = []
    for strat, last in rows:
        if not last:
            issues.append(f"- 🚨 {strat}: never fired LIVE")
            continue
        # Parse timestamp — drop microseconds and tz
        try:
            tt = datetime.fromisoformat(last.replace("Z", "+00:00"))
            hours = (now - tt).total_seconds() / 3600
        except Exception:
            continue
        # Thresholds tuned for 07:30 UTC wake-up: most US sports games end
        # 02-04 UTC, settle 05-06 UTC, next-day prep starts 12 UTC — so
        # 7-12h-since-fire is the natural lull at digest time. >18h is
        # unusual; >24h likely indicates a real silence event.
        if hours >= 24:
            issues.append(f"- 🚨 {strat}: last fire {hours:.1f}h ago ({last})")
        elif hours >= 18:
            issues.append(f"- ⚠️ {strat}: last fire {hours:.1f}h ago ({last})")
    if not issues:
        lines.append("All 4 LIVE strategies fired within last 18h ✓")
    else:
        lines.extend(issues)
    return "\n".join(lines)


def section_stablecoin():
    """Stablecoin collector overnight progress."""
    lines = ["## Stablecoin collector — overnight", ""]
    rows = q("""
        SELECT direction, COUNT(*) AS n, ROUND(SUM(amount_usd)/1e6, 1) AS sum_M
        FROM stablecoin_mint_observations
        WHERE detected_at >= datetime('now', '-12 hours')
        GROUP BY direction
    """)
    if not rows:
        lines.append("_No events in last 12h._")
    else:
        mint_sum = burn_sum = 0
        mint_n = burn_n = 0
        for direction, n, sum_m in rows:
            if direction == "mint":
                mint_n, mint_sum = n, sum_m or 0
            else:
                burn_n, burn_sum = n, sum_m or 0
        net = mint_sum - burn_sum
        lines.append(f"- Mints: **{mint_n}** events / **${mint_sum:.0f}M**")
        lines.append(f"- Burns: **{burn_n}** events / **${burn_sum:.0f}M**")
        lines.append(f"- Net flow (mint − burn): **${net:+.0f}M**")
        if net > 50:
            lines.append("  - 📈 Large net mint: bullish bias 4-24h per thesis")
        elif net < -50:
            lines.append("  - 📉 Large net burn: bearish bias 4-24h per thesis")
    total_rows = q("SELECT COUNT(*), MIN(detected_at), MAX(detected_at) FROM stablecoin_mint_observations")
    if total_rows:
        total, first, last = total_rows[0]
        lines.append(f"\n_Cumulative since 5/26 17:55 UTC start: {total} events. Last event: {last}._")
    return "\n".join(lines)


def section_daily_loss_status():
    """Daily loss breaker status — silent breaker is the #1 culprit per memory."""
    lines = ["## Daily loss breaker", ""]
    rows = q("""
        SELECT ROUND(SUM(pnl), 2)
        FROM trades
        WHERE status = 'settled'
          AND timestamp >= datetime('now', 'start of day')
    """)
    daily = rows[0][0] if rows else 0
    cap = 200  # MAX_DAILY_LOSS from .env
    if daily is None or daily >= 0:
        lines.append(f"LIVE realized today: {fmt_money(daily)} (no risk of breaker) ✓")
    else:
        used = abs(daily)
        pct = used / cap * 100
        flag = "🚨 BREAKER TRIPPED — all strategies silent" if used >= cap else \
               "⚠️ Approaching cap" if pct >= 75 else "✓"
        lines.append(f"LIVE realized today: {fmt_money(daily)} / cap -${cap} ({pct:.0f}% used) {flag}")
    return "\n".join(lines)


def main():
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    sections = [
        f"# Morning settle-digest — {now_utc}",
        f"_Generated 07:30 UTC (03:30 EDT) for wake-up at ~05:30 EDT._",
        "",
        section_today_deploys(),
        "",
        section_live_24h(),
        "",
        section_dedup_check(),
        "",
        section_silence(),
        "",
        section_stablecoin(),
        "",
        section_daily_loss_status(),
        "",
        "---",
        "_Next checkpoint: existing morning_report at 08:30 UTC + daily_live_audit at 13:00 UTC._",
    ]
    out = "\n".join(sections)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        f.write(out)
    print(f"Wrote {OUT} ({len(out)} bytes)")
    print(out)


if __name__ == "__main__":
    main()
