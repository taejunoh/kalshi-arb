#!/usr/bin/env python3
"""Post-hoc audit: did the pitcher we used for sports_mlb_pitcher_edge_v1
fires match the actual game starter?

For each settled trade in the last 7 days:
  1. Look up the pitcher_id our collector had at fire time
     (mlb_pitcher_observations row matching the trade's ticker, closest
     fetched_at before the trade timestamp).
  2. Query MLB Stats API for the actual starting pitcher in the game.
  3. Compare → match / mismatch / unknown.

Output: mismatch rate. If >10%, tighten collector cadence (30min → 10min)
or add late-fire guard in strategy.

Telegram alert if mismatch_rate >= 15%.

Run weekly via kalshi-mlb-pitcher-audit.timer.
"""
import json
import logging
import os
import sqlite3
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("mlb_pitcher_audit")
DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")


def http_get_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "kalshi-arb-audit/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def actual_starter_ids(game_pk):
    """Returns (away_starter_id, home_starter_id) for a finished game via
    MLB Stats API boxscore. Returns (None, None) if not finished/unavailable.
    """
    try:
        d = http_get_json(f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore")
        teams = d.get("teams", {})
        def first_pitcher(team_block):
            pitchers = team_block.get("pitchers", [])
            return pitchers[0] if pitchers else None
        return first_pitcher(teams.get("away", {})), first_pitcher(teams.get("home", {}))
    except Exception as e:
        logger.warning(f"boxscore fetch {game_pk}: {e}")
        return None, None


def find_game_pk(date_iso, away_code, home_code):
    """Reverse-lookup game_pk from schedule given date + team codes."""
    try:
        from data_collectors.mlb_pitchers import TEAM_MAP
        rev = {v: k for k, v in TEAM_MAP.items()}
        a_id, h_id = rev.get(away_code), rev.get(home_code)
        sched = http_get_json(
            f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date_iso}"
        )
        for date_obj in sched.get("dates", []):
            for g in date_obj.get("games", []):
                if (g["teams"]["away"]["team"]["id"] == a_id
                        and g["teams"]["home"]["team"]["id"] == h_id):
                    return g["gamePk"]
        return None
    except Exception as e:
        logger.warning(f"schedule lookup: {e}")
        return None


def send_telegram(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        return
    try:
        import urllib.parse
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode()
        urllib.request.urlopen(url, data=data, timeout=10).read()
    except Exception:
        pass


def parse_ticker(ticker):
    """KXMLBGAME-26MAY261705AZSF-AZ →
       (game_date_et='26MAY26', game_time_et='1705', away='AZ', home='SF', team='AZ')
    """
    # Strip prefix
    if not ticker.startswith("KXMLBGAME-"):
        return None
    body = ticker[len("KXMLBGAME-"):]
    # Split on '-' (team is suffix)
    parts = body.rsplit("-", 1)
    if len(parts) != 2: return None
    coded, team = parts
    # coded = YYMMMDDHHMMAWAYHOME, where YY=2 digits, MMM=3 alpha, DD=2 digits,
    # HHMM=4 digits, AWAY+HOME=variable length team codes (2-3 chars each).
    # Length: 2+3+2+4+ = 11 chars before teams.
    if len(coded) < 11: return None
    date_part = coded[:7]   # YYMMMDD
    time_part = coded[7:11] # HHMM
    teams_part = coded[11:] # AWAYHOME
    # Try splitting teams: split at 'team' (suffix is one of them).
    # If suffix matches end of teams_part, the other prefix is the away/home.
    if teams_part.endswith(team):
        other = teams_part[:-len(team)]
        # 'team' could be the home OR the away. Hard to know without more info.
        # Convention: ticker code is {AWAY}{HOME}, so if team==HOME, away=other.
        # We don't know yet. Return both possibilities.
        return {"date": date_part, "time": time_part,
                "team_codes_concat": teams_part, "team": team,
                "other": other}
    return None


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA busy_timeout = 5000")
    cur = conn.cursor()

    # Trades from the last 7 days for our strategy. Only settled (so the game
    # is over and box score available).
    cur.execute("""
        SELECT t.id, t.timestamp, t.market_id, t.price, t.pnl,
               (SELECT away_pitcher_id FROM mlb_pitcher_observations m
                WHERE m.ticker_better_yes = t.market_id
                  AND m.fetched_at <= t.timestamp
                ORDER BY m.fetched_at DESC LIMIT 1) as our_away_pp,
               (SELECT home_pitcher_id FROM mlb_pitcher_observations m
                WHERE m.ticker_better_yes = t.market_id
                  AND m.fetched_at <= t.timestamp
                ORDER BY m.fetched_at DESC LIMIT 1) as our_home_pp,
               (SELECT away_code FROM mlb_pitcher_observations m
                WHERE m.ticker_better_yes = t.market_id LIMIT 1) as away_code,
               (SELECT home_code FROM mlb_pitcher_observations m
                WHERE m.ticker_better_yes = t.market_id LIMIT 1) as home_code,
               (SELECT game_date_et FROM mlb_pitcher_observations m
                WHERE m.ticker_better_yes = t.market_id LIMIT 1) as date_et
        FROM trades t
        WHERE t.strategy='sports_mlb_pitcher_edge_v1'
          AND t.status IN ('settled', 'paper_settled')
          AND t.timestamp >= strftime('%Y-%m-%dT%H:%M:%S', 'now','-7 days')
    """)
    rows = cur.fetchall()
    logger.info(f"settled trades for audit: {len(rows)}")

    matches, mismatches, unknown = 0, 0, 0
    detail = []

    for trade_id, ts, ticker, price, pnl, our_a, our_h, away_code, home_code, date_et in rows:
        if not (our_a or our_h):
            unknown += 1
            continue
        # Convert game_date_et (YYMMMDD) to ISO date (YYYY-MM-DD) for schedule lookup
        # NOTE: we use the YY+MMM+DD; assume current year = 20YY
        try:
            month_map = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
                         "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}
            yy = int(date_et[:2])
            month_abbr = date_et[2:5]
            dd = int(date_et[5:7])
            iso_date = f"20{yy:02d}-{month_map[month_abbr]:02d}-{dd:02d}"
        except Exception as e:
            unknown += 1
            continue
        game_pk = find_game_pk(iso_date, away_code, home_code)
        if not game_pk:
            unknown += 1
            continue
        actual_a, actual_h = actual_starter_ids(game_pk)
        if not (actual_a or actual_h):
            unknown += 1
            continue
        ok_a = (our_a == actual_a) if (our_a and actual_a) else True
        ok_h = (our_h == actual_h) if (our_h and actual_h) else True
        if ok_a and ok_h:
            matches += 1
        else:
            mismatches += 1
            detail.append({
                "ticker": ticker, "ts": ts, "pnl": pnl,
                "ours": (our_a, our_h), "actual": (actual_a, actual_h),
            })

    total = matches + mismatches
    rate = (100.0 * mismatches / total) if total else 0.0
    summary = (f"[mlb_pitcher_audit] 7d settled={len(rows)} "
               f"match={matches} mismatch={mismatches} unknown={unknown} "
               f"mismatch_rate={rate:.1f}%")
    logger.info(summary)
    if detail:
        for d in detail[:10]:
            logger.info(f"  MISMATCH: {d}")

    # Alert on high mismatch rate
    if rate >= 15.0 and total >= 5:
        send_telegram(
            f"⚠ MLB pitcher mismatch rate {rate:.1f}% (n={total}) — "
            f"consider tighter collector cadence."
        )


if __name__ == "__main__":
    main()
