"""UFC fighter form collector — recent W-L + days_since_last per fighter.

Data-only at launch (no strategy). Builds 2-3 month forward dataset for
backtesting fighter-stats × Kalshi-edge signals.

Pulls upcoming UFC events from ESPN, extracts both fighters per fight,
then queries each fighter's history to compute recent form.

ESPN endpoints used:
  - /sports/mma/ufc/scoreboard?dates=YYYYMMDD-YYYYMMDD — upcoming events
  - /sports/mma/ufc/athletes/{id} — fighter profile (records, stats)
  - per-fight competition details for last-N results

Cadence: daily (UFC fights ~weekly, doesn't change minute-to-minute).
"""
import json
import logging
import os
import sqlite3
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger("ufc_fighter_form_collector")
DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS ufc_fighter_form (
    fetched_at TEXT NOT NULL,
    fighter_id INTEGER NOT NULL,
    fighter_name TEXT,
    record_wins INTEGER,            -- career W-L-D from ESPN profile
    record_losses INTEGER,
    record_draws INTEGER,
    last_fight_date TEXT,           -- ISO date of most recent fight
    days_since_last INTEGER,
    weight_class TEXT,
    height_inches REAL,
    reach_inches REAL,
    age INTEGER,
    stance TEXT,                    -- Orthodox / Southpaw / Switch
    PRIMARY KEY (fighter_id)
);
CREATE INDEX IF NOT EXISTS idx_ufc_form_name ON ufc_fighter_form(fighter_name);

CREATE TABLE IF NOT EXISTS ufc_match_observations (
    fetched_at TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_date_utc TEXT NOT NULL,
    fighter_a_id INTEGER NOT NULL,
    fighter_b_id INTEGER NOT NULL,
    fighter_a_name TEXT,
    fighter_b_name TEXT,
    weight_class TEXT,
    PRIMARY KEY (event_id, fighter_a_id, fighter_b_id)
);
CREATE INDEX IF NOT EXISTS idx_ufc_match_date ON ufc_match_observations(event_date_utc);
"""


def http_get_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "kalshi-arb-ufc/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def fetch_upcoming_events(days_ahead=14):
    today = datetime.now(timezone.utc).date()
    end = today + timedelta(days=days_ahead)
    range_str = today.strftime("%Y%m%d") + "-" + end.strftime("%Y%m%d")
    return http_get_json(
        f"https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard?dates={range_str}"
    )


def fetch_event_detail(event_id):
    return http_get_json(
        f"https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard/{event_id}"
    )


def fetch_athlete_profile(athlete_id):
    """ESPN MMA athlete overview — career record + bio."""
    try:
        return http_get_json(
            f"https://site.web.api.espn.com/apis/common/v3/sports/mma/ufc/athletes/{athlete_id}"
        )
    except Exception:
        return None


def parse_record(record_str):
    """'14-3-0' → (14, 3, 0). Returns (None, None, None) on parse failure."""
    try:
        parts = (record_str or "").split("-")
        return int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) >= 3 else 0
    except Exception:
        return None, None, None


def upsert_fighter(conn, now_iso, fighter_id, fighter_name):
    """Fetch profile + upsert one fighter."""
    prof = fetch_athlete_profile(fighter_id)
    if not prof:
        return False
    athlete = prof.get("athlete", {})
    # Record: statsSummary.statistics[ name='wins-losses-draws' ].displayValue
    wins = losses = draws = None
    ss = athlete.get("statsSummary", {}) or {}
    for stat in ss.get("statistics", []) or []:
        if stat.get("name") == "wins-losses-draws":
            rec_str = stat.get("displayValue") or ""
            if "-" in rec_str:
                wins, losses, draws = parse_record(rec_str)
            break

    # Weight class either weightClass.text or displayWeight
    wc_field = athlete.get("weightClass")
    if isinstance(wc_field, dict):
        weight = wc_field.get("text") or wc_field.get("name")
    else:
        weight = wc_field or athlete.get("displayWeight")
    height_disp = athlete.get("displayHeight") or ""
    height_in = None
    if "'" in height_disp:
        try:
            ft, inch = height_disp.replace('"', '').split("'")
            height_in = int(ft.strip()) * 12 + int(inch.strip())
        except Exception:
            pass
    reach = None
    reach_disp = athlete.get("displayReach") or ""
    if reach_disp and '"' in reach_disp:
        try:
            reach = float(reach_disp.replace('"', '').strip())
        except Exception:
            pass

    age = athlete.get("age")
    stance_raw = athlete.get("displayStance") or athlete.get("stance")
    if isinstance(stance_raw, dict):
        stance = (stance_raw.get("text") or stance_raw.get("value")
                  or stance_raw.get("displayValue"))
    else:
        stance = stance_raw

    # Latest fight: ESPN athlete payload may not include directly; we leave
    # last_fight_date null for v1 (will derive from match_observations + outcomes).
    conn.execute("""
        INSERT INTO ufc_fighter_form
        (fetched_at, fighter_id, fighter_name, record_wins, record_losses,
         record_draws, last_fight_date, days_since_last,
         weight_class, height_inches, reach_inches, age, stance)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(fighter_id) DO UPDATE SET
          fetched_at=excluded.fetched_at,
          record_wins=excluded.record_wins, record_losses=excluded.record_losses,
          record_draws=excluded.record_draws,
          weight_class=excluded.weight_class,
          height_inches=excluded.height_inches, reach_inches=excluded.reach_inches,
          age=excluded.age, stance=excluded.stance
    """, (
        now_iso, fighter_id, fighter_name, wins, losses, draws,
        None, None, weight, height_in, reach, age, stance,
    ))
    return True


def run():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.executescript(SCHEMA)
    conn.commit()

    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()

    try:
        sb = fetch_upcoming_events(days_ahead=14)
    except Exception as e:
        logger.error(f"scoreboard fetch: {e}")
        return

    upcoming_events = sb.get("events", []) or []
    logger.info(f"Upcoming UFC events: {len(upcoming_events)}")

    fighters_seen = set()
    match_upserts = 0
    for event in upcoming_events:
        eid = event.get("id")
        if not eid: continue
        event_date = event.get("date")
        for comp in event.get("competitions", []):
            competitors = comp.get("competitors", [])
            if len(competitors) < 2: continue
            a, b = competitors[0], competitors[1]
            # ESPN MMA puts athlete ID at competitor level, not inside athlete{}.
            a_id = a.get("id")
            b_id = b.get("id")
            a_name = a.get("athlete", {}).get("displayName")
            b_name = b.get("athlete", {}).get("displayName")
            wc = comp.get("type", {}).get("abbreviation")
            if not (a_id and b_id):
                continue
            try:
                a_id = int(a_id); b_id = int(b_id)
            except Exception:
                continue

            conn.execute("""
                INSERT INTO ufc_match_observations
                (fetched_at, event_id, event_date_utc,
                 fighter_a_id, fighter_b_id, fighter_a_name, fighter_b_name,
                 weight_class)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(event_id, fighter_a_id, fighter_b_id) DO UPDATE SET
                  fetched_at=excluded.fetched_at,
                  event_date_utc=excluded.event_date_utc,
                  fighter_a_name=excluded.fighter_a_name,
                  fighter_b_name=excluded.fighter_b_name,
                  weight_class=excluded.weight_class
            """, (now_iso, str(eid), event_date, a_id, b_id, a_name, b_name, wc))
            match_upserts += 1
            fighters_seen.add((a_id, a_name))
            fighters_seen.add((b_id, b_name))

    conn.commit()
    logger.info(f"Match upserts: {match_upserts}, unique fighters: {len(fighters_seen)}")

    # Now fetch each fighter's profile + upsert
    fighter_upserts = 0
    for fid, fname in fighters_seen:
        try:
            ok = upsert_fighter(conn, now_iso, fid, fname)
            if ok:
                fighter_upserts += 1
            time.sleep(0.1)  # be gentle to ESPN
        except Exception as e:
            logger.warning(f"fighter {fid} {fname}: {e}")
    conn.commit()
    conn.close()
    logger.info(f"Fighter form upserts: {fighter_upserts}")


if __name__ == "__main__":
    run()
