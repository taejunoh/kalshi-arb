"""Fetch MLB starting pitcher data from MLB Stats API and store in DB.

Run via systemd timer (suggested every 30 min). Each invocation:
  1. Fetches today's + tomorrow's MLB schedule with probablePitcher hydration.
  2. For each pitcher_id, fetches current-season ERA.
  3. Upserts into `mlb_pitcher_observations` table.

The strategy `sports_mlb_pitcher_edge_v1` reads this table to score games.

Backtest 2026-05-26 (n=358 over 30d):
  ERA diff ≥ 2.0: WR 74%, EV +$0.11/$1 (buy better-pitcher team YES)
  ERA diff ≥ 3.0: WR 77%, EV +$0.14/$1

Look-ahead caveat: current ERA aggregates all season-to-date games incl.
ones we're predicting. Forward (paper) WR will be a more honest signal.
"""
import json
import logging
import os
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("mlb_pitchers_collector")

DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")
SEASON = int(os.getenv("MLB_SEASON", "2026"))

# MLB Stats API team_id → Kalshi ticker abbreviation (sampled from real
# tickers — note AZ for Diamondbacks, CWS for White Sox, KC for Royals,
# ATH for Athletics, not ARI/CHW/KCR/OAK).
TEAM_MAP = {
    108: "LAA", 109: "AZ",  110: "BAL", 111: "BOS", 112: "CHC", 113: "CIN",
    114: "CLE", 115: "COL", 116: "DET", 117: "HOU", 118: "KC",  119: "LAD",
    120: "WSH", 121: "NYM", 133: "ATH", 134: "PIT", 135: "SD",  136: "SEA",
    137: "SF",  138: "STL", 139: "TB",  140: "TEX", 141: "TOR", 142: "MIN",
    143: "PHI", 144: "ATL", 145: "CWS", 146: "MIA", 147: "NYY", 158: "MIL",
}

# Non-tradeable exhibition gameTypes to skip BEFORE the team-id lookup. The
# All-Star Game (gameType 'A') fields AL/NL All-Star squads (team ids 159/160)
# that aren't real franchises — mapping them would pollute observations with a
# game that has no KXMLBGAME ticker and a meaningless ERA-diff, and leaving
# them unmapped fired a false "UNKNOWN TEAM IDS" alert every 30 min through the
# 2026-07 break. Spring ('S') and exhibition ('E'/'I') are the same story.
# Regular season ('R') and postseason ('F'/'D'/'L'/'W') flow through unchanged,
# so a genuinely unknown id in a real game still alerts.
SKIP_GAME_TYPES = frozenset({"A", "S", "E", "I"})

SCHEMA = """
CREATE TABLE IF NOT EXISTS mlb_pitcher_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at TEXT NOT NULL,
    game_date_et TEXT NOT NULL,      -- YYMMMDD ET (e.g., 26MAY26)
    game_time_et TEXT NOT NULL,      -- HHMM ET (e.g., 1705)
    away_code TEXT NOT NULL,
    home_code TEXT NOT NULL,
    away_pitcher_id INTEGER,
    home_pitcher_id INTEGER,
    away_pitcher_name TEXT,
    home_pitcher_name TEXT,
    away_era REAL,
    home_era REAL,
    era_diff REAL,
    better_team TEXT,
    ticker_better_yes TEXT,          -- KXMLBGAME-...-BETTER (buy YES to win)
    raw_json TEXT,
    UNIQUE(game_date_et, game_time_et, away_code, home_code)
);
CREATE INDEX IF NOT EXISTS idx_mlb_pitcher_ticker
    ON mlb_pitcher_observations(ticker_better_yes);
CREATE INDEX IF NOT EXISTS idx_mlb_pitcher_date
    ON mlb_pitcher_observations(game_date_et);
"""


def http_get_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "kalshi-arb-mlb/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def fetch_schedule(date_str):
    return http_get_json(
        f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date_str}"
        f"&hydrate=probablePitcher"
    )


def fetch_pitcher_era(pid, season):
    try:
        d = http_get_json(
            f"https://statsapi.mlb.com/api/v1/people/{pid}"
            f"?hydrate=stats(group=pitching,type=season,season={season})"
        )
        stats = d["people"][0].get("stats", [])
        for s in stats:
            splits = s.get("splits", [])
            for sp in splits:
                era = sp.get("stat", {}).get("era")
                if era is not None and era != "-.--":
                    try:
                        return float(era)
                    except Exception:
                        pass
        return None
    except Exception as e:
        logger.warning(f"fetch_pitcher_era({pid}) failed: {e}")
        return None


def ticker_for(game_date_et, game_time_et, away, home, team):
    return f"KXMLBGAME-{game_date_et}{game_time_et}{away}{home}-{team}"


def run():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.executescript(SCHEMA)
    conn.commit()

    # Fetch today + next 2 days (covers upcoming games to score)
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    now_utc = datetime.now(timezone.utc)
    now_et = now_utc.astimezone(et)
    dates = [(now_et + timedelta(days=d)).date().isoformat() for d in range(0, 3)]

    pitcher_era_cache = {}
    games_processed = 0
    upserts = 0
    unknown_team_ids = set()

    for date_str in dates:
        try:
            sched = fetch_schedule(date_str)
        except Exception as e:
            logger.warning(f"schedule {date_str}: {e}")
            continue
        for date_obj in sched.get("dates", []):
            for g in date_obj.get("games", []):
                # Skip exhibition games (All-Star / spring / exhibition) before
                # the team-id lookup so their non-franchise ids never reach the
                # unknown-id alert. See SKIP_GAME_TYPES.
                if g.get("gameType") in SKIP_GAME_TYPES:
                    continue
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
                # Probable pitchers may not be announced yet; still record game
                # with NULL pitcher fields so the strategy knows to skip.

                # ET game date+time
                dt_utc = datetime.fromisoformat(g["gameDate"].replace("Z","+00:00"))
                dt_et = dt_utc.astimezone(et)
                ymd = dt_et.strftime("%y%b%d").upper()
                hm = dt_et.strftime("%H%M")

                # Get ERAs (cache to avoid duplicates)
                away_era = None
                home_era = None
                if away_pid is not None:
                    if away_pid in pitcher_era_cache:
                        away_era = pitcher_era_cache[away_pid]
                    else:
                        away_era = fetch_pitcher_era(away_pid, SEASON)
                        pitcher_era_cache[away_pid] = away_era
                        time.sleep(0.05)
                if home_pid is not None:
                    if home_pid in pitcher_era_cache:
                        home_era = pitcher_era_cache[home_pid]
                    else:
                        home_era = fetch_pitcher_era(home_pid, SEASON)
                        pitcher_era_cache[home_pid] = home_era
                        time.sleep(0.05)

                # Determine better team (lower ERA = better)
                era_diff = None
                better_team = None
                ticker_better = None
                if away_era is not None and home_era is not None:
                    era_diff = abs(away_era - home_era)
                    better_team = away_code if away_era < home_era else home_code
                    ticker_better = ticker_for(ymd, hm, away_code, home_code, better_team)

                conn.execute("""
                    INSERT INTO mlb_pitcher_observations
                    (fetched_at, game_date_et, game_time_et, away_code, home_code,
                     away_pitcher_id, home_pitcher_id, away_pitcher_name, home_pitcher_name,
                     away_era, home_era, era_diff, better_team, ticker_better_yes, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(game_date_et, game_time_et, away_code, home_code) DO UPDATE SET
                        fetched_at=excluded.fetched_at,
                        away_pitcher_id=excluded.away_pitcher_id,
                        home_pitcher_id=excluded.home_pitcher_id,
                        away_pitcher_name=excluded.away_pitcher_name,
                        home_pitcher_name=excluded.home_pitcher_name,
                        away_era=excluded.away_era,
                        home_era=excluded.home_era,
                        era_diff=excluded.era_diff,
                        better_team=excluded.better_team,
                        ticker_better_yes=excluded.ticker_better_yes
                """, (
                    now_utc.isoformat(), ymd, hm, away_code, home_code,
                    away_pid, home_pid,
                    away_pp.get("fullName"), home_pp.get("fullName"),
                    away_era, home_era, era_diff, better_team, ticker_better,
                    None,  # raw_json — skip for space; reconstruct from fields
                ))
                games_processed += 1
                upserts += 1

    conn.commit()
    conn.close()
    logger.info(f"games processed: {games_processed}, rows inserted: {upserts}")

    # Alert if any unknown team_ids encountered (rebrand/relocation/expansion).
    if unknown_team_ids:
        msg = (
            "[mlb_pitchers] UNKNOWN TEAM IDS — update TEAM_MAP in "
            f"data_collectors/mlb_pitchers.py:\n"
            + "\n".join(f"  {tid}: {tname}" for tid, tname in sorted(unknown_team_ids))
        )
        logger.warning(msg)
        _send_telegram_critical(msg)


def _send_telegram_critical(message: str):
    """Best-effort Telegram alert for critical collector issues. Uses same
    env vars as the bot (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID).
    """
    import os
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        return
    try:
        import urllib.parse
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": message,
        }).encode()
        urllib.request.urlopen(url, data=data, timeout=10).read()
    except Exception as e:
        logger.warning(f"telegram alert failed: {e}")


if __name__ == "__main__":
    run()
