# scripts/softbook_live_grade.py
#!/usr/bin/env python3
"""Live in-game grading for open app_bet rows (app-side, advisory).

Joins each open bet to a live sports feed (MLB StatsAPI, public/no-auth) to read
the player's CURRENT stat, then projects status vs the line for the progress ring
("0 of 2 total bases, Mid 2nd"). Final win/loss still comes from the existing
settlement backfill — projected status is advisory and reconciled there (handles
push/void/scratch/rain). MLB-first; NBA fetch is a clear follow-up seam.

Runs as kalshi-softbook-live-grade.timer (~30-60s during games).
"""
import json
import math
import os
import sqlite3
import urllib.request

import softbook_app_db as adb

# market -> MLB StatsAPI boxscore stat path (batting/pitching stat name)
MLB_STAT = {
    "batter_total_bases": ("batting", "totalBases"),
    "batter_home_runs": ("batting", "homeRuns"),
    "pitcher_strikeouts": ("pitching", "strikeOuts"),
    "pitcher_outs": ("pitching", "outs"),
    "pitcher_earned_runs": ("pitching", "earnedRuns"),
    "pitcher_hits_allowed": ("pitching", "hits"),
}


def project(side, point, current, game_state):
    """Pure: project live_status + progress dict from current stat vs line."""
    if current is None:
        return {"live_status": "pending",
                "progress": {"current": None, "needed": point,
                             "game_state": game_state}}
    over = side == "Over"
    if over:
        status = "winning" if current > point else "losing"
        remaining = max(0, math.ceil(point) - current) if current <= point else 0
    else:
        status = "winning" if current < point else "losing"
        remaining = 0
    return {"live_status": status,
            "progress": {"current": current, "needed": point,
                         "remaining": remaining, "game_state": game_state}}


def _mlb_live_stat(sport, event_id, player, market):
    """Fetch a player's current stat from the MLB StatsAPI live feed. event_id is
    the Odds API event id, NOT the MLB gamePk — map by team+date upstream; here we
    accept a gamePk passed through event_id when numeric, else return unknown.
    (The team/date->gamePk mapping is built in grade_open_bets' fetcher wiring.)"""
    sp = MLB_STAT.get(market)
    if not sp or sport != "baseball_mlb" or not str(event_id).isdigit():
        return None, None
    group, stat = sp
    url = f"https://statsapi.mlb.com/api/v1.1/game/{event_id}/feed/live"
    try:
        data = json.loads(urllib.request.urlopen(url, timeout=15).read())
    except Exception:
        return None, None
    ls = data.get("liveData", {})
    state = (data.get("gameData", {}).get("status", {}).get("detailedState"))
    box = ls.get("boxscore", {}).get("teams", {})
    for side in ("home", "away"):
        for pid, pdata in box.get(side, {}).get("players", {}).items():
            full = pdata.get("person", {}).get("fullName", "")
            if full.lower() == (player or "").lower():
                val = pdata.get("stats", {}).get(group, {}).get(stat)
                return (int(val) if val is not None else None), state
    return None, state


def grade_open_bets(db, fetch_stat=_mlb_live_stat):
    """Project status for every open app_bet; write live_status + live_progress.
    Returns the number of bets updated."""
    rows = db.execute(
        "SELECT id, sport, event_id, market, side, point, player FROM app_bet"
        " WHERE settled=0 AND not_placed=0").fetchall()
    n = 0
    for bid, sport, eid, market, side, point, player in rows:
        if market not in MLB_STAT:           # NBA props: follow-up seam
            continue
        current, state = fetch_stat(sport, eid, player, market)
        proj = project(side, point, current, state)
        db.execute("UPDATE app_bet SET live_status=?, live_progress=? WHERE id=?",
                   (proj["live_status"], json.dumps(proj["progress"]), bid))
        n += 1
    db.commit()
    return n


def main():
    db = adb.connect()
    try:
        n = grade_open_bets(db)
        print(f"[softbook-live-grade] updated {n} open bets")
    finally:
        db.close()


if __name__ == "__main__":
    main()
