"""Morning report — overnight summary for new paper strategies + collectors.

Runs once per day (suggested 08:30 UTC). Captures:
  - Last 12h paper fires per new strategy + settle PnL
  - Collector health (rows added in last 12h per table)
  - Any errors/warnings in journalctl
  - WS measurement file existence
  - LIVE strategy 12h summary

Sends formatted Markdown to Telegram.
"""
import json
import logging
import os
import sqlite3
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("morning_report")

DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")


def query(sql, params=()):
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA query_only = 1")
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _tg_post(payload):
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data=data)
    return urllib.request.urlopen(req, timeout=10).read()


def send_telegram(msg):
    if not (TG_TOKEN and TG_CHAT):
        logger.warning("missing telegram credentials")
        print(msg)
        return
    try:
        _tg_post({"chat_id": TG_CHAT, "text": msg, "parse_mode": "Markdown"})
        return
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300] if hasattr(e, "read") else ""
        logger.warning(f"telegram markdown failed ({e.code}): {body}")
    except Exception as e:
        logger.warning(f"telegram markdown failed: {e}")
    try:
        _tg_post({"chat_id": TG_CHAT, "text": msg})
    except Exception as e:
        logger.warning(f"telegram plain-text fallback failed: {e}")
        print(msg)


def fmt_pnl(v):
    if v is None: return "$0"
    return f"+${v:.2f}" if v >= 0 else f"-${abs(v):.2f}"


def main():
    lines = []
    lines.append(f"🌅 *Morning Report* — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")

    # 1. New paper strategies — last 12h fires + settle PnL
    lines.append("*New paper strategies (last 12h)*")
    new_strats = ["sports_tennis_chalk_v1", "sports_mlb_pitcher_edge_v1"]
    for s in new_strats:
        fires = query(
            "SELECT COUNT(*), ROUND(AVG(price),3) FROM trades WHERE strategy=? AND timestamp>=strftime('%Y-%m-%dT%H:%M:%S', 'now','-12 hours') AND status IN ('paper_filled','filled')",
            (s,))[0]
        settles = query(
            "SELECT COUNT(*), ROUND(SUM(pnl),2), ROUND(100.0*SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END)/MAX(COUNT(*),1),1) FROM trades WHERE strategy=? AND timestamp>=strftime('%Y-%m-%dT%H:%M:%S', 'now','-12 hours') AND status IN ('paper_settled','settled')",
            (s,))[0]
        n_fire, avg_p = fires[0], fires[1]
        n_settle, pnl, wr = settles[0], settles[1], settles[2]
        line = (f"• `{s}`: {n_fire} fires (avg {avg_p}), {n_settle} settled "
                f"{fmt_pnl(pnl)} WR {wr or 0}%")
        lines.append(line)
    lines.append("")

    # 2. LIVE strategies last 12h
    lines.append("*LIVE strategies (last 12h)*")
    live_strats = ["sports_midshot_v31","sports_longshot_v31","sports_underdog_v31","sports_underdog_v37"]
    total_pnl = 0
    total_n = 0
    for s in live_strats:
        r = query(
            "SELECT COUNT(*), ROUND(SUM(pnl),2) FROM trades WHERE strategy=? AND timestamp>=strftime('%Y-%m-%dT%H:%M:%S', 'now','-12 hours') AND status='settled'",
            (s,))[0]
        n, pnl = r[0], r[1] or 0
        if n > 0:
            lines.append(f"• `{s}`: n={n} {fmt_pnl(pnl)}")
            total_pnl += pnl
            total_n += n
    lines.append(f"*LIVE 12h total*: {total_n} settled, {fmt_pnl(total_pnl)}")
    lines.append("")

    # 3. Collectors — rows added 12h
    lines.append("*Collectors (rows in last 12h)*")
    collector_tables = {
        "mlb_pitcher_observations": "fetched_at",
        "nhl_game_observations": "fetched_at",
        "nba_game_observations": "fetched_at",
        "wnba_game_observations": "fetched_at",
        "weather_observations": "fetched_at",
        "spot_observations": "fetched_at",
        "polymarket_quotes": None,  # different col
    }
    for tbl, col in collector_tables.items():
        if not col:
            r = query(f"SELECT COUNT(*) FROM {tbl} WHERE rowid > (SELECT MAX(rowid)-1000 FROM {tbl})")
            n = r[0][0] if r else 0
            lines.append(f"• `{tbl}`: {n} recent (recent_window)")
            continue
        try:
            r = query(f"SELECT COUNT(*) FROM {tbl} WHERE {col} >= datetime('now','-12 hours')")
            n = r[0][0] if r else 0
            lines.append(f"• `{tbl}`: {n}")
        except Exception as e:
            lines.append(f"• `{tbl}`: err {e}")
    lines.append("")

    # 4. WS metrics file (if exists)
    import glob
    ws_files = sorted(glob.glob("/home/ubuntu/kalshi-arb/data/ws_metrics_*.json"))
    if ws_files:
        latest = ws_files[-1]
        try:
            with open(latest) as f:
                m = json.load(f)
            lines.append("*WS measurement (latest)*")
            lines.append(f"• File: `{os.path.basename(latest)}`")
            lines.append(f"• Total msgs: {m.get('total_msgs')}, {m.get('avg_msgs_per_sec',0):.1f}/sec")
            lines.append(f"• Reconnects: {m.get('reconnects')}, errors: {len(m.get('errors',[]))}")
            lines.append("")
        except Exception as e:
            lines.append(f"WS file read err: {e}")

    # 5. Bot health
    lines.append("*Bot health*")
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "kalshi-bot"],
            capture_output=True, text=True, timeout=5)
        active = result.stdout.strip()
        lines.append(f"• kalshi-bot: `{active}`")
    except Exception as e:
        lines.append(f"• kalshi-bot status err: {e}")

    # 6. Errors in last 12h (excluding known noise)
    try:
        result = subprocess.run(
            ["journalctl", "-u", "kalshi-bot", "--since", "12 hours ago", "--no-pager",
             "-p", "warning", "-q"],
            capture_output=True, text=True, timeout=10)
        errors = result.stdout
        # Filter known noise
        filtered = [l for l in errors.split("\n")
                    if l and "404" not in l and "Cancel failed" not in l
                    and "ConnectTimeout" not in l]
        err_count = len(filtered)
        lines.append(f"• Recent warnings/errors (filtered): {err_count}")
        if err_count > 0 and err_count <= 5:
            lines.append("```")
            for l in filtered[:5]:
                lines.append(l[:120])
            lines.append("```")
    except Exception as e:
        lines.append(f"• journalctl err: {e}")

    msg = "\n".join(lines)
    send_telegram(msg)
    print(msg)


if __name__ == "__main__":
    main()
