"""WNBA game-level multi-signal collector (data-only).

Captures per-game via ESPN summary endpoint:
  - Player injuries (Out / IR / Questionable / Doubtful)
  - Last 5 game results
  - ESPN does NOT provide predictor for WNBA (verified 2026-05-26) —
    same situation as NHL. Field captured but expected None.

WNBA is our main +EV anchor across midshot_v31 / underdog_v31 /
underdog_v37 / longshot_v31. This collector adds injury / form data
that may strengthen the cohort selection.

Future strategy idea: when WNBA team has top-2 player Out + opponent
healthy, fade Kalshi pricing on the healthy team.
"""
import json
import logging
import os
import sqlite3
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger("wnba_injuries_collector")
DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS wnba_game_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at TEXT NOT NULL,
    game_date_et TEXT NOT NULL,
    away_code TEXT NOT NULL,
    home_code TEXT NOT NULL,
    espn_event_id TEXT,
    espn_predict_home_pct REAL,
    espn_predict_away_pct REAL,
    away_injuries_out INTEGER,
    home_injuries_out INTEGER,
    away_injuries_quest INTEGER,
    home_injuries_quest INTEGER,
    away_injuries_json TEXT,
    home_injuries_json TEXT,
    away_l5_wins INTEGER,
    home_l5_wins INTEGER,
    ticker_home_yes TEXT,
    ticker_away_yes TEXT,
    UNIQUE(game_date_et, away_code, home_code)
);
CREATE INDEX IF NOT EXISTS idx_wnba_obs_date ON wnba_game_observations(game_date_et);
CREATE INDEX IF NOT EXISTS idx_wnba_obs_ticker_home ON wnba_game_observations(ticker_home_yes);
CREATE INDEX IF NOT EXISTS idx_wnba_obs_ticker_away ON wnba_game_observations(ticker_away_yes);
"""


def http_get_json(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": "kalshi-arb-wnba/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def parse_injuries(injuries_list):
    out_count, quest_count = 0, 0
    names = []
    for i in injuries_list:
        st = i.get("status", "")
        ath = i.get("athlete", {})
        names.append({"name": ath.get("displayName"), "position": ath.get("position", {}).get("abbreviation"), "status": st})
        if st in ("Out", "Injured Reserve", "Suspended"):
            out_count += 1
        elif st in ("Doubtful", "Questionable", "Day-To-Day"):
            quest_count += 1
    return out_count, quest_count, names


def parse_recent_wins(events_list):
    return sum(1 for e in events_list if e.get("gameResult") == "W")


def run():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.executescript(SCHEMA)
    conn.commit()

    et = ZoneInfo("America/New_York")
    now_utc = datetime.now(timezone.utc)
    today_et = now_utc.astimezone(et).date()
    # WNBA games span MOST days during season — 3 day window
    dates = [(today_et + timedelta(days=d)).isoformat() for d in range(0, 3)]

    upserts = 0
    for date_iso in dates:
        try:
            yyyymmdd = date_iso.replace("-", "")
            sb = http_get_json(f"https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard?dates={yyyymmdd}")
        except Exception as e:
            logger.warning(f"scoreboard {date_iso}: {e}")
            continue
        for event in sb.get("events", []):
            eid = event.get("id")
            if not eid: continue
            comp = event.get("competitions", [{}])[0]
            away_ab = home_ab = None
            for c in comp.get("competitors", []):
                if c.get("homeAway") == "away":
                    away_ab = c.get("team", {}).get("abbreviation")
                elif c.get("homeAway") == "home":
                    home_ab = c.get("team", {}).get("abbreviation")
            if not (away_ab and home_ab): continue
            game_dt_utc = datetime.fromisoformat(event["date"].replace("Z","+00:00"))
            game_dt_et = game_dt_utc.astimezone(et)
            ymd = game_dt_et.strftime("%y%b%d").upper()

            try:
                summ = http_get_json(f"https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/summary?event={eid}")
            except Exception as e:
                logger.warning(f"summary {eid}: {e}")
                continue
            time.sleep(0.1)

            p = summ.get("predictor", {}) or {}
            phome = p.get("homeTeam", {}).get("gameProjection")
            paway = p.get("awayTeam", {}).get("gameProjection")
            phome = float(phome) if phome is not None else None
            paway = float(paway) if paway is not None else None

            a_out=a_q=h_out=h_q=0
            a_names_j=h_names_j=None
            for ti in summ.get("injuries", []):
                team_ab = ti.get("team", {}).get("abbreviation")
                inj_list = ti.get("injuries", [])
                out_c, q_c, names = parse_injuries(inj_list)
                if team_ab == away_ab:
                    a_out, a_q, a_names_j = out_c, q_c, json.dumps(names)
                elif team_ab == home_ab:
                    h_out, h_q, h_names_j = out_c, q_c, json.dumps(names)

            a_l5w = h_l5w = None
            for tb in summ.get("lastFiveGames", []):
                team_ab = tb.get("team", {}).get("abbreviation")
                if team_ab == away_ab:
                    a_l5w = parse_recent_wins(tb.get("events", []))
                elif team_ab == home_ab:
                    h_l5w = parse_recent_wins(tb.get("events", []))

            # Kalshi WNBA tickers use KXWNBAGAME prefix (parallel to KXNBAGAME)
            ticker_home = f"KXWNBAGAME-{ymd}{away_ab}{home_ab}-{home_ab}"
            ticker_away = f"KXWNBAGAME-{ymd}{away_ab}{home_ab}-{away_ab}"

            conn.execute("""
                INSERT INTO wnba_game_observations
                (fetched_at, game_date_et, away_code, home_code, espn_event_id,
                 espn_predict_home_pct, espn_predict_away_pct,
                 away_injuries_out, home_injuries_out,
                 away_injuries_quest, home_injuries_quest,
                 away_injuries_json, home_injuries_json,
                 away_l5_wins, home_l5_wins,
                 ticker_home_yes, ticker_away_yes)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(game_date_et, away_code, home_code) DO UPDATE SET
                  fetched_at=excluded.fetched_at, espn_event_id=excluded.espn_event_id,
                  espn_predict_home_pct=excluded.espn_predict_home_pct,
                  espn_predict_away_pct=excluded.espn_predict_away_pct,
                  away_injuries_out=excluded.away_injuries_out,
                  home_injuries_out=excluded.home_injuries_out,
                  away_injuries_quest=excluded.away_injuries_quest,
                  home_injuries_quest=excluded.home_injuries_quest,
                  away_injuries_json=excluded.away_injuries_json,
                  home_injuries_json=excluded.home_injuries_json,
                  away_l5_wins=excluded.away_l5_wins, home_l5_wins=excluded.home_l5_wins
            """, (
                now_utc.isoformat(), ymd, away_ab, home_ab, eid,
                phome, paway,
                a_out, h_out, a_q, h_q,
                a_names_j, h_names_j,
                a_l5w, h_l5w,
                ticker_home, ticker_away,
            ))
            upserts += 1

    conn.commit()
    conn.close()
    logger.info(f"WNBA games upserted: {upserts}")


if __name__ == "__main__":
    run()
