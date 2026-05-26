"""MLB pitcher point-in-time ERA collector — bias-free version of mlb_pitchers.py.

Backtest 2026-05-26 revealed massive look-ahead bias in current-season ERA
(WR 82% biased → 57% point-in-time). This collector fetches each pitcher's
gameLog and computes cumulative ERA from ONLY games BEFORE the prediction
date — eliminating bias.

Used by `sports_mlb_pitcher_edge_v2`. Coexists with `mlb_pitchers.py` (v1)
for controlled A/B comparison.

Backtest signal: ERA diff ≥ 2.5 + AWAY_better → fade home (n=20, WR 65%,
EV +$0.076/$1). Also home_better at same threshold (n=20, WR 70%, +$0.042).
"""
import json
import logging
import os
import sqlite3
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger("mlb_pitcher_pointtime_collector")
DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")
SEASON = int(os.getenv("MLB_SEASON", "2026"))

# Reused from mlb_pitchers.py
TEAM_MAP = {
    108: "LAA", 109: "AZ",  110: "BAL", 111: "BOS", 112: "CHC", 113: "CIN",
    114: "CLE", 115: "COL", 116: "DET", 117: "HOU", 118: "KC",  119: "LAD",
    120: "WSH", 121: "NYM", 133: "ATH", 134: "PIT", 135: "SD",  136: "SEA",
    137: "SF",  138: "STL", 139: "TB",  140: "TEX", 141: "TOR", 142: "MIN",
    143: "PHI", 144: "ATL", 145: "CWS", 146: "MIA", 147: "NYY", 158: "MIL",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS mlb_pitcher_pointtime_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at TEXT NOT NULL,
    game_date_et TEXT NOT NULL,
    game_time_et TEXT NOT NULL,
    away_code TEXT NOT NULL,
    home_code TEXT NOT NULL,
    away_pitcher_id INTEGER,
    home_pitcher_id INTEGER,
    away_pitcher_name TEXT,
    home_pitcher_name TEXT,
    away_era_pit REAL,
    home_era_pit REAL,
    away_ip_total REAL,
    home_ip_total REAL,
    era_diff_pit REAL,
    better_team TEXT,
    ticker_home_yes TEXT,
    ticker_away_yes TEXT,
    UNIQUE(game_date_et, game_time_et, away_code, home_code)
);
CREATE INDEX IF NOT EXISTS idx_mlb_pit_home_yes
    ON mlb_pitcher_pointtime_observations(ticker_home_yes);
CREATE INDEX IF NOT EXISTS idx_mlb_pit_away_yes
    ON mlb_pitcher_pointtime_observations(ticker_away_yes);
"""


def http_get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "kalshi-arb-mlb-pit/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def parse_ip(ip_str):
    """MLB notation: '5.2' = 5 + 2/3 IP."""
    try:
        s = str(ip_str)
        if "." in s:
            w, f = s.split(".")
            return float(w) + float(f) / 3.0
        return float(s)
    except Exception:
        return 0.0


def fetch_gamelog(pitcher_id):
    """Returns sorted list of (date, IP, ER) tuples for this pitcher this season."""
    try:
        d = http_get(
            f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats?"
            f"stats=gameLog&season={SEASON}&group=pitching"
        )
        out = []
        for s in d.get("stats", [{}])[0].get("splits", []):
            date = s.get("date")
            stat = s.get("stat", {})
            ip = parse_ip(stat.get("inningsPitched", 0))
            er = int(stat.get("earnedRuns", 0))
            if date and ip > 0:
                out.append((date, ip, er))
        return sorted(out)
    except Exception as e:
        logger.warning(f"gamelog {pitcher_id}: {e}")
        return []


def era_as_of(gamelog, before_date_iso):
    """Cumulative ERA from games BEFORE before_date.

    Returns (era, total_ip). era is None if total_ip < 5 (insufficient sample).
    """
    total_ip = 0.0
    total_er = 0
    for d, ip, er in gamelog:
        if d < before_date_iso:
            total_ip += ip
            total_er += er
    if total_ip < 5:
        return None, total_ip
    return (total_er * 9.0) / total_ip, total_ip


def fetch_schedule(date_iso):
    return http_get(
        f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date_iso}"
        f"&hydrate=probablePitcher"
    )


def run():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.executescript(SCHEMA)
    conn.commit()

    et = ZoneInfo("America/New_York")
    now_utc = datetime.now(timezone.utc)
    today_et = now_utc.astimezone(et).date()
    dates = [(today_et + timedelta(days=d)).isoformat() for d in range(0, 3)]

    gamelog_cache = {}
    upserts = 0
    unknown_team_ids = set()

    for date_iso in dates:
        try:
            sched = fetch_schedule(date_iso)
        except Exception as e:
            logger.warning(f"schedule {date_iso}: {e}")
            continue
        for dobj in sched.get("dates", []):
            for g in dobj.get("games", []):
                away = g["teams"]["away"]
                home = g["teams"]["home"]
                away_id, home_id = away["team"]["id"], home["team"]["id"]
                away_code = TEAM_MAP.get(away_id)
                home_code = TEAM_MAP.get(home_id)
                if not away_code:
                    unknown_team_ids.add((away_id, away.get("team", {}).get("name", "?")))
                if not home_code:
                    unknown_team_ids.add((home_id, home.get("team", {}).get("name", "?")))
                if not (away_code and home_code):
                    continue
                away_pp = away.get("probablePitcher", {}) or {}
                home_pp = home.get("probablePitcher", {}) or {}
                away_pid = away_pp.get("id")
                home_pid = home_pp.get("id")

                # ET ticker
                dt_utc = datetime.fromisoformat(g["gameDate"].replace("Z","+00:00"))
                dt_et = dt_utc.astimezone(et)
                ymd = dt_et.strftime("%y%b%d").upper()
                hm = dt_et.strftime("%H%M")
                game_iso = dt_et.date().isoformat()  # ISO date for ERA cutoff

                # Get gameLog per pitcher (cached)
                a_era = h_era = None
                a_ip = h_ip = 0.0
                if away_pid is not None:
                    if away_pid not in gamelog_cache:
                        gamelog_cache[away_pid] = fetch_gamelog(away_pid)
                        time.sleep(0.05)
                    a_era, a_ip = era_as_of(gamelog_cache[away_pid], game_iso)
                if home_pid is not None:
                    if home_pid not in gamelog_cache:
                        gamelog_cache[home_pid] = fetch_gamelog(home_pid)
                        time.sleep(0.05)
                    h_era, h_ip = era_as_of(gamelog_cache[home_pid], game_iso)

                era_diff = None
                better_team = None
                if a_era is not None and h_era is not None:
                    era_diff = abs(a_era - h_era)
                    better_team = away_code if a_era < h_era else home_code

                ticker_home_yes = f"KXMLBGAME-{ymd}{hm}{away_code}{home_code}-{home_code}"
                ticker_away_yes = f"KXMLBGAME-{ymd}{hm}{away_code}{home_code}-{away_code}"

                conn.execute("""
                    INSERT INTO mlb_pitcher_pointtime_observations
                    (fetched_at, game_date_et, game_time_et, away_code, home_code,
                     away_pitcher_id, home_pitcher_id,
                     away_pitcher_name, home_pitcher_name,
                     away_era_pit, home_era_pit,
                     away_ip_total, home_ip_total,
                     era_diff_pit, better_team,
                     ticker_home_yes, ticker_away_yes)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(game_date_et, game_time_et, away_code, home_code) DO UPDATE SET
                        fetched_at=excluded.fetched_at,
                        away_pitcher_id=excluded.away_pitcher_id,
                        home_pitcher_id=excluded.home_pitcher_id,
                        away_pitcher_name=excluded.away_pitcher_name,
                        home_pitcher_name=excluded.home_pitcher_name,
                        away_era_pit=excluded.away_era_pit,
                        home_era_pit=excluded.home_era_pit,
                        away_ip_total=excluded.away_ip_total,
                        home_ip_total=excluded.home_ip_total,
                        era_diff_pit=excluded.era_diff_pit,
                        better_team=excluded.better_team
                """, (
                    now_utc.isoformat(), ymd, hm, away_code, home_code,
                    away_pid, home_pid,
                    away_pp.get("fullName"), home_pp.get("fullName"),
                    a_era, h_era, a_ip, h_ip,
                    era_diff, better_team,
                    ticker_home_yes, ticker_away_yes,
                ))
                upserts += 1

    conn.commit()
    conn.close()
    logger.info(f"MLB point-in-time games upserted: {upserts}, gamelog API calls: {len(gamelog_cache)}")

    if unknown_team_ids:
        msg = ("[mlb_pitcher_pointtime] UNKNOWN TEAM IDS:\n"
               + "\n".join(f"  {tid}: {tn}" for tid, tn in sorted(unknown_team_ids)))
        logger.warning(msg)


if __name__ == "__main__":
    run()
