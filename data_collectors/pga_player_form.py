"""PGA player form collector — daily ESPN leaderboard snapshot.

US Open is 6/18-21, Travelers Championship 6/25-28. We target post-US-Open
backtest of KXPGAH2H markets using player form metrics derived here.

What this collects (per tournament + round + player):
  - finishing position (live during play, final after settle)
  - score-to-par cumulative
  - made-cut flag
  - rounds completed
  - player metadata (display name, ID for joining to past collections)

ESPN endpoint used:
  /sports/golf/leaderboard?league=pga — current active tournament
  /sports/golf/leaderboard?league=pga&dates=YYYYMMDD — specific date

Cadence: daily 12:00 UTC. PGA tournaments run Thu-Sun; we capture each
round's state once per day. Data-only at launch; no strategy until ≥6
tournaments of data + backtest confirms +EV signal vs KXPGAH2H markets.

Schema is athlete-keyed so form aggregations roll up cleanly (recent 5
finishes, made-cut rate, rolling avg score, etc.) once we have history.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import urllib.request
from datetime import datetime, timezone

logger = logging.getLogger("pga_player_form_collector")
DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS pga_event_observations (
    fetched_at TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_name TEXT,
    event_status TEXT,           -- STATUS_SCHEDULED / IN_PROGRESS / FINAL
    event_start_date TEXT,
    PRIMARY KEY (fetched_at, event_id)
);

CREATE TABLE IF NOT EXISTS pga_player_observations (
    fetched_at TEXT NOT NULL,
    event_id TEXT NOT NULL,
    athlete_id INTEGER NOT NULL,
    athlete_name TEXT,
    position_id TEXT,            -- ESPN position id (e.g. T1, T2, CUT, WD, DQ)
    position_display TEXT,       -- '1', 'T2', 'CUT', 'WD'
    score_to_par REAL,           -- cumulative -10..+15 range
    score_display TEXT,          -- '-10' / 'E' / '+3' / 'CUT'
    rounds_played INTEGER,
    made_cut INTEGER,            -- 1 / 0 / NULL (NULL when cut hasn't happened)
    PRIMARY KEY (fetched_at, event_id, athlete_id)
);
CREATE INDEX IF NOT EXISTS idx_pga_player_athlete
    ON pga_player_observations(athlete_id, fetched_at);
CREATE INDEX IF NOT EXISTS idx_pga_player_event
    ON pga_player_observations(event_id, fetched_at);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def http_get_json(url: str, timeout: float = 15.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "kalshi-arb-pga/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def fetch_pga_leaderboard() -> dict:
    return http_get_json(
        "https://site.api.espn.com/apis/site/v2/sports/golf/leaderboard?league=pga"
    )


def parse_score_to_par(score_obj) -> float | None:
    if score_obj is None:
        return None
    if isinstance(score_obj, dict):
        v = score_obj.get("value")
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    try:
        return float(score_obj)
    except (TypeError, ValueError):
        return None


def parse_position(player: dict) -> tuple[str | None, str | None]:
    status = player.get("status", {})
    pos = status.get("position", {}) or {}
    return pos.get("id"), pos.get("displayValue")


def parse_made_cut(position_display: str | None, status_obj: dict) -> int | None:
    """ESPN encodes cut state in position_display ('CUT', 'WD', 'DQ') or
    state field. Returns 1/0/None."""
    if not position_display:
        return None
    pd = position_display.upper()
    if pd in {"CUT", "MC"}:
        return 0
    if pd in {"WD", "DQ", "DNF"}:
        return None
    # Numeric position (1, T2, T15) → made cut
    if pd[0].isdigit() or (pd.startswith("T") and len(pd) > 1 and pd[1:].isdigit()):
        return 1
    return None


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    c = sqlite3.connect(DB, timeout=30.0)
    c.execute("PRAGMA busy_timeout=30000")
    c.executescript(SCHEMA)

    try:
        data = fetch_pga_leaderboard()
    except Exception as e:
        logger.error(f"ESPN fetch failed: {e}")
        c.close()
        return

    events = data.get("events", [])
    if not events:
        logger.info("No active PGA event in leaderboard")
        c.close()
        return

    fetched_at = _now_iso()
    total_players = 0

    for event in events:
        event_id = str(event.get("id", ""))
        event_name = event.get("name", "")
        event_status = (event.get("status", {}).get("type", {}) or {}).get("name", "")
        event_start = event.get("date", "")

        c.execute("""
            INSERT OR REPLACE INTO pga_event_observations
            (fetched_at, event_id, event_name, event_status, event_start_date)
            VALUES (?, ?, ?, ?, ?)
        """, (fetched_at, event_id, event_name, event_status, event_start))

        for comp in event.get("competitions", []):
            for player in comp.get("competitors", []):
                athlete = player.get("athlete", {}) or {}
                athlete_id_raw = athlete.get("id")
                try:
                    athlete_id = int(athlete_id_raw) if athlete_id_raw is not None else None
                except (TypeError, ValueError):
                    athlete_id = None
                if athlete_id is None:
                    continue
                athlete_name = athlete.get("displayName")
                position_id, position_display = parse_position(player)
                score_to_par = parse_score_to_par(player.get("score"))
                # Rounds played: look at linescores list (per-round score)
                line_scores = player.get("linescores", []) or []
                rounds_played = sum(
                    1 for ls in line_scores
                    if (ls.get("value") is not None and ls.get("value") > 0)
                    or (ls.get("displayValue") and ls.get("displayValue") not in ("-", ""))
                )
                made_cut = parse_made_cut(position_display, player.get("status", {}))

                c.execute("""
                    INSERT OR REPLACE INTO pga_player_observations
                    (fetched_at, event_id, athlete_id, athlete_name, position_id,
                     position_display, score_to_par, score_display, rounds_played, made_cut)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    fetched_at, event_id, athlete_id, athlete_name,
                    position_id, position_display,
                    score_to_par,
                    (player.get("score") or {}).get("displayValue") if isinstance(player.get("score"), dict) else None,
                    rounds_played, made_cut,
                ))
                total_players += 1

    c.commit()
    logger.info(f"PGA collector: {len(events)} event(s), {total_players} player rows")
    c.close()


if __name__ == "__main__":
    main()
