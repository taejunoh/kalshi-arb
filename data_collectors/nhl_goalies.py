"""NHL game-level multi-signal collector (data-only, no strategy yet).

Captures for upcoming + recent NHL games:
  - Starting goalie save % (regular season, current playoff, career playoff)
  - ESPN injury list
  - Last 10 games W-L record (per team)

Stored in `nhl_game_observations`. After 4+ weeks of accumulation,
backtest-replay will determine which signal(s) drive +EV.

Backtest 2026-05-26 over 30d (n=29 only) was inconclusive — save% diff
mixed, n too small. Plan: collect 4 more weeks (Stanley Cup Final + early
2026-27 preseason) + October regular season, then decide.
"""
import json
import logging
import os
import sqlite3
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger("nhl_goalies_collector")
DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")

# Kalshi NHL team code map (from NHL abbrev → Kalshi ticker convention)
# All known abbrevs in 2025-26 — Kalshi uses standard 3-char NHL abbrevs.
# (No special remapping needed unlike MLB.)

SCHEMA = """
CREATE TABLE IF NOT EXISTS nhl_game_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at TEXT NOT NULL,
    game_date_et TEXT NOT NULL,       -- YYMMMDD ET
    away_code TEXT NOT NULL,
    home_code TEXT NOT NULL,
    nhl_game_id INTEGER,
    espn_event_id TEXT,
    -- Starting goalie projection (best guess: last starter from each team)
    away_goalie_id INTEGER,
    home_goalie_id INTEGER,
    away_goalie_name TEXT,
    home_goalie_name TEXT,
    -- 3 variants of save %
    away_savepct_reg REAL,
    home_savepct_reg REAL,
    away_savepct_po REAL,
    home_savepct_po REAL,
    away_savepct_pocar REAL,
    home_savepct_pocar REAL,
    -- Recent form (last 10 games at NHL Stats API level)
    away_wins_l10 INTEGER,
    home_wins_l10 INTEGER,
    -- ESPN injuries (count of "Out" + key player names; not used for decisions yet)
    away_injuries_out INTEGER,
    home_injuries_out INTEGER,
    away_injuries_json TEXT,
    home_injuries_json TEXT,
    ticker_better_savepct_reg TEXT,
    -- 2026-05-26: starter workload signals (gameLog-derived, point-in-time).
    away_starter_days_rest INTEGER,    -- days since starter's last appearance with TOI≥30min
    home_starter_days_rest INTEGER,
    away_starter_games_l7 INTEGER,     -- starts in last 7 days (workload pressure)
    home_starter_games_l7 INTEGER,
    UNIQUE(game_date_et, away_code, home_code)
);
CREATE INDEX IF NOT EXISTS idx_nhl_obs_ticker ON nhl_game_observations(ticker_better_savepct_reg);
CREATE INDEX IF NOT EXISTS idx_nhl_obs_date ON nhl_game_observations(game_date_et);
"""

# Idempotent column backfill for pre-existing tables.
WORKLOAD_MIGRATIONS = (
    "ALTER TABLE nhl_game_observations ADD COLUMN away_starter_days_rest INTEGER",
    "ALTER TABLE nhl_game_observations ADD COLUMN home_starter_days_rest INTEGER",
    "ALTER TABLE nhl_game_observations ADD COLUMN away_starter_games_l7 INTEGER",
    "ALTER TABLE nhl_game_observations ADD COLUMN home_starter_games_l7 INTEGER",
)


def http_get_json(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": "kalshi-arb-nhl/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def fetch_nhl_schedule(date_iso):
    return http_get_json(f"https://api-web.nhle.com/v1/schedule/{date_iso}")


def fetch_team_recent_starter(team_abbrev):
    """Best guess of upcoming starting goalie: pick goalie with most recent
    appearance (max TOI in last finished game). Uses season-wide schedule
    so playoff teams (no upcoming weekly games before today) still resolve.
    Returns (player_id, name).
    """
    try:
        d = http_get_json(f"https://api-web.nhle.com/v1/club-schedule-season/{team_abbrev}/now")
        finished = [g for g in d.get("games", []) if g.get("gameState") in ("OFF", "FINAL")]
        # Iterate from most recent backwards
        for g in reversed(finished):
            box = http_get_json(f"https://api-web.nhle.com/v1/gamecenter/{g['id']}/boxscore")
            if box["awayTeam"]["abbrev"] == team_abbrev:
                goalies = box.get("playerByGameStats", {}).get("awayTeam", {}).get("goalies", [])
            else:
                goalies = box.get("playerByGameStats", {}).get("homeTeam", {}).get("goalies", [])
            if goalies:
                def toi(g):
                    t = (g.get("toi") or "0:0").split(":")
                    return int(t[0]) * 60 + (int(t[1]) if len(t) > 1 else 0)
                starter = max(goalies, key=toi)
                if toi(starter) >= 30 * 60:  # require ≥30 min TOI to call them "starter"
                    return starter.get("playerId"), starter.get("name", {}).get("default")
        return None, None
    except Exception as e:
        logger.debug(f"recent starter fetch {team_abbrev}: {e}")
        return None, None


def fetch_player_savepct(player_id):
    """(reg, current_playoff, career_playoff) save %, None if too few games."""
    try:
        d = http_get_json(f"https://api-web.nhle.com/v1/player/{player_id}/landing")
        fs = d.get("featuredStats", {})
        def sub(block, min_gp):
            s = block.get("subSeason", {})
            sp = s.get("savePctg")
            return float(sp) if sp is not None and s.get("gamesPlayed", 0) >= min_gp else None
        def career(block):
            c = block.get("career", {})
            sp = c.get("savePctg")
            return float(sp) if sp is not None and c.get("gamesPlayed", 0) >= 10 else None
        return (sub(fs.get("regularSeason", {}), 5),
                sub(fs.get("playoffs", {}), 3),
                career(fs.get("playoffs", {})))
    except Exception:
        return None, None, None


def _current_nhl_season():
    """NHL season ID format YYYYYYYY (e.g., 20252026). Season starts in October."""
    today = datetime.now(timezone.utc).date()
    yr = today.year
    if today.month >= 7:
        return yr * 10000 + (yr + 1)
    return (yr - 1) * 10000 + yr


def fetch_goalie_workload(player_id, season, game_date_iso):
    """Returns (days_rest, games_l7) for the goalie's prior starts.

    Pulls gameLog for both regular season and playoffs in `season`, filters
    to games strictly BEFORE game_date_iso, sorts by date, and computes:
      - days_rest: days since most recent start (TOI implied by row presence)
      - games_l7: count of starts in the 7 days preceding game_date_iso

    Returns (None, None) on API failure or no prior starts.
    """
    try:
        from datetime import date as _date
        starts = []
        for gt in (2, 3):  # regular, playoffs
            d = http_get_json(
                f"https://api-web.nhle.com/v1/player/{player_id}/game-log/{season}/{gt}"
            )
            for g in d.get("gameLog", []):
                gd = g.get("gameDate")
                if gd and gd < game_date_iso:
                    starts.append(gd)
        if not starts:
            return None, None
        starts.sort()
        last = starts[-1]
        last_dt = _date.fromisoformat(last)
        game_dt = _date.fromisoformat(game_date_iso)
        days_rest = (game_dt - last_dt).days
        cutoff_dt = (game_dt - timedelta(days=7)).isoformat()
        games_l7 = sum(1 for d in starts if d >= cutoff_dt)
        return days_rest, games_l7
    except Exception as e:
        logger.debug(f"workload fetch {player_id}: {e}")
        return None, None


def fetch_team_recent_wins(team_abbrev):
    """W-L in last 10 finished games."""
    try:
        # Use club season schedule + count W/L
        d = http_get_json(f"https://api-web.nhle.com/v1/club-schedule-season/{team_abbrev}/now")
        finished = [g for g in d.get("games", []) if g.get("gameState") in ("OFF","FINAL")]
        last10 = finished[-10:] if len(finished) >= 10 else finished
        wins = 0
        for g in last10:
            this_is_away = (g["awayTeam"]["abbrev"] == team_abbrev)
            a_s = g["awayTeam"].get("score", 0)
            h_s = g["homeTeam"].get("score", 0)
            if a_s is None or h_s is None: continue
            if this_is_away and a_s > h_s: wins += 1
            elif (not this_is_away) and h_s > a_s: wins += 1
        return wins, len(last10)
    except Exception:
        return None, None


def fetch_espn_injuries(espn_event_id):
    """Returns dict {team_abbrev: {out_count, names_json}}"""
    try:
        d = http_get_json(f"https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/summary?event={espn_event_id}")
        result = {}
        for ti in d.get("injuries", []):
            team_abbrev = ti.get("team", {}).get("abbreviation")
            inj_list = ti.get("injuries", [])
            out_count = sum(1 for i in inj_list if i.get("status") in ("Out", "Injured Reserve"))
            names = [{
                "name": i.get("athlete", {}).get("displayName"),
                "status": i.get("status"),
                "position": i.get("athlete", {}).get("position", {}).get("abbreviation")
            } for i in inj_list]
            result[team_abbrev] = {"out_count": out_count, "names_json": json.dumps(names)}
        return result
    except Exception as e:
        logger.debug(f"espn injuries: {e}")
        return {}


def fetch_espn_event_id(date_iso, away_abbrev, home_abbrev):
    """Best-effort ESPN event_id lookup by date + teams."""
    try:
        yyyymmdd = date_iso.replace("-", "")
        d = http_get_json(f"https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard?dates={yyyymmdd}")
        for e in d.get("events", []):
            comp = e.get("competitions", [{}])[0]
            comps = comp.get("competitors", [])
            abbrs = {c.get("homeAway"): c.get("team", {}).get("abbreviation") for c in comps}
            if abbrs.get("away") == away_abbrev and abbrs.get("home") == home_abbrev:
                return e.get("id")
        return None
    except Exception:
        return None


def run():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.executescript(SCHEMA)
    for stmt in WORKLOAD_MIGRATIONS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass
    conn.commit()

    et = ZoneInfo("America/New_York")
    now_utc = datetime.now(timezone.utc)
    today_et = now_utc.astimezone(et).date()
    dates = [(today_et + timedelta(days=d)).isoformat() for d in range(0, 3)]

    starter_cache = {}
    sv_cache = {}
    rec_cache = {}
    upserts = 0

    for date_iso in dates:
        try:
            sched = fetch_nhl_schedule(date_iso)
        except Exception as e:
            logger.warning(f"schedule {date_iso}: {e}")
            continue
        for week in sched.get("gameWeek", []):
            if week.get("date") != date_iso:
                continue  # gameWeek covers ~7 days; filter to target date
            for g in week.get("games", []):
                game_id = g.get("id")
                if g.get("gameState") in ("OFF", "FINAL"):
                    continue
                away_ab = g["awayTeam"]["abbrev"]
                home_ab = g["homeTeam"]["abbrev"]
                dt_utc = datetime.fromisoformat(g["startTimeUTC"].replace("Z","+00:00"))
                dt_et = dt_utc.astimezone(et)
                ymd = dt_et.strftime("%y%b%d").upper()

                # Cached fetches
                for team in (away_ab, home_ab):
                    if team not in starter_cache:
                        starter_cache[team] = fetch_team_recent_starter(team)
                        time.sleep(0.05)
                    if team not in rec_cache:
                        rec_cache[team] = fetch_team_recent_wins(team)
                        time.sleep(0.05)
                a_pid, a_name = starter_cache[away_ab]
                h_pid, h_name = starter_cache[home_ab]
                a_wins, a_n = rec_cache[away_ab]
                h_wins, h_n = rec_cache[home_ab]

                a_reg = a_po = a_pocar = None
                h_reg = h_po = h_pocar = None
                if a_pid is not None:
                    if a_pid not in sv_cache:
                        sv_cache[a_pid] = fetch_player_savepct(a_pid)
                        time.sleep(0.05)
                    a_reg, a_po, a_pocar = sv_cache[a_pid]
                if h_pid is not None:
                    if h_pid not in sv_cache:
                        sv_cache[h_pid] = fetch_player_savepct(h_pid)
                        time.sleep(0.05)
                    h_reg, h_po, h_pocar = sv_cache[h_pid]

                # Starter workload (PIT — gameLog filtered to dates before this game).
                wl_season = _current_nhl_season()
                a_drest = a_g7 = h_drest = h_g7 = None
                if a_pid is not None:
                    a_drest, a_g7 = fetch_goalie_workload(a_pid, wl_season, date_iso)
                    time.sleep(0.05)
                if h_pid is not None:
                    h_drest, h_g7 = fetch_goalie_workload(h_pid, wl_season, date_iso)
                    time.sleep(0.05)

                espn_eid = fetch_espn_event_id(date_iso, away_ab, home_ab)
                inj = fetch_espn_injuries(espn_eid) if espn_eid else {}
                a_inj = inj.get(away_ab, {})
                h_inj = inj.get(home_ab, {})

                # Compute ticker for better-savepct (reg season)
                ticker_better = None
                if a_reg is not None and h_reg is not None:
                    better = away_ab if a_reg > h_reg else home_ab
                    ticker_better = f"KXNHLGAME-{ymd}{away_ab}{home_ab}-{better}"

                conn.execute("""
                    INSERT INTO nhl_game_observations
                    (fetched_at, game_date_et, away_code, home_code, nhl_game_id, espn_event_id,
                     away_goalie_id, home_goalie_id, away_goalie_name, home_goalie_name,
                     away_savepct_reg, home_savepct_reg, away_savepct_po, home_savepct_po,
                     away_savepct_pocar, home_savepct_pocar,
                     away_wins_l10, home_wins_l10,
                     away_injuries_out, home_injuries_out, away_injuries_json, home_injuries_json,
                     ticker_better_savepct_reg,
                     away_starter_days_rest, home_starter_days_rest,
                     away_starter_games_l7, home_starter_games_l7)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(game_date_et, away_code, home_code) DO UPDATE SET
                      fetched_at=excluded.fetched_at,
                      nhl_game_id=excluded.nhl_game_id, espn_event_id=excluded.espn_event_id,
                      away_goalie_id=excluded.away_goalie_id, home_goalie_id=excluded.home_goalie_id,
                      away_goalie_name=excluded.away_goalie_name, home_goalie_name=excluded.home_goalie_name,
                      away_savepct_reg=excluded.away_savepct_reg, home_savepct_reg=excluded.home_savepct_reg,
                      away_savepct_po=excluded.away_savepct_po, home_savepct_po=excluded.home_savepct_po,
                      away_savepct_pocar=excluded.away_savepct_pocar, home_savepct_pocar=excluded.home_savepct_pocar,
                      away_wins_l10=excluded.away_wins_l10, home_wins_l10=excluded.home_wins_l10,
                      away_injuries_out=excluded.away_injuries_out, home_injuries_out=excluded.home_injuries_out,
                      away_injuries_json=excluded.away_injuries_json, home_injuries_json=excluded.home_injuries_json,
                      ticker_better_savepct_reg=excluded.ticker_better_savepct_reg,
                      away_starter_days_rest=excluded.away_starter_days_rest,
                      home_starter_days_rest=excluded.home_starter_days_rest,
                      away_starter_games_l7=excluded.away_starter_games_l7,
                      home_starter_games_l7=excluded.home_starter_games_l7
                """, (
                    now_utc.isoformat(), ymd, away_ab, home_ab, game_id, espn_eid,
                    a_pid, h_pid, a_name, h_name,
                    a_reg, h_reg, a_po, h_po, a_pocar, h_pocar,
                    a_wins, h_wins,
                    a_inj.get("out_count"), h_inj.get("out_count"),
                    a_inj.get("names_json"), h_inj.get("names_json"),
                    ticker_better,
                    a_drest, h_drest, a_g7, h_g7,
                ))
                upserts += 1

    conn.commit()
    conn.close()
    logger.info(f"NHL games upserted: {upserts}")


if __name__ == "__main__":
    run()
