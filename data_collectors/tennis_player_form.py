"""Tennis player recent-form collector (ATP + WTA).

Computes each player's W-L over recent matches purely from our own
`ticker_settlements` + `odds_observations` history — no external API.

Tickers like KXATPMATCH-26MAY24SINTAR-SIN encode the trailing 3-letter
code = the player the ticker pays out on. Settlement result 'yes' on
-SIN means Sinner won that match.

For each player code in the last 90d window:
  - Sort all settled matches by date descending
  - Take most recent 10
  - Count W/L in last 5 / last 10
  - Compute days_since_last_match

Stored in `tennis_player_form` with one row per (tour, player_code).
Used by future tennis strategies wanting recent-form gates without
external API dependence.

Cadence: daily (player form doesn't change minute-to-minute).
"""
import json
import logging
import os
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, date, timezone

logger = logging.getLogger("tennis_player_form_collector")
DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")
LOOKBACK_DAYS = 90  # broader window so ATP/WTA tour rotation is captured

MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"])}

SCHEMA = """
CREATE TABLE IF NOT EXISTS tennis_player_form (
    fetched_at TEXT NOT NULL,
    tour TEXT NOT NULL,                 -- 'ATP' or 'WTA'
    player_code TEXT NOT NULL,          -- 3-letter trailing ticker code
    player_name TEXT,                   -- yes_sub_title from latest obs
    last_match_date TEXT,               -- ISO date of most recent settled match
    days_since_last INTEGER,            -- days from last_match_date to fetched_at
    l5_wins INTEGER, l5_losses INTEGER,
    l10_wins INTEGER, l10_losses INTEGER,
    last_opponents_json TEXT,           -- JSON list of {date, opponent, won}
    PRIMARY KEY (tour, player_code)
);
CREATE INDEX IF NOT EXISTS idx_tennis_form_tour ON tennis_player_form(tour);
"""


def parse_match_date(ticker):
    """KXATPMATCH-26MAY24SINTAR-SIN → date('2026-05-24')."""
    body = ticker.replace("KXATPMATCH-", "").replace("KXWTAMATCH-", "")
    head = body.split("-")[0]
    yy = head[:2]; mon = head[2:5]; dd = head[5:7]
    if mon not in MONTHS:
        return None
    try:
        return date(2000 + int(yy), MONTHS[mon], int(dd))
    except ValueError:
        return None


def parse_game_key(ticker):
    """KXATPMATCH-26MAY24SINTAR-SIN → ('ATP', '26MAY24SINTAR', 'SIN')."""
    if ticker.startswith("KXATPMATCH-"):
        tour = "ATP"; body = ticker.replace("KXATPMATCH-", "")
    elif ticker.startswith("KXWTAMATCH-"):
        tour = "WTA"; body = ticker.replace("KXWTAMATCH-", "")
    else:
        return None
    head, _, side = body.rpartition("-")
    if not side or len(side) != 3:
        return None
    return tour, head, side


def opponent_of(game_key, player_code):
    """game_key '26MAY24SINTAR' → opponent code 'TAR' if player='SIN'."""
    teams = game_key[7:]  # after date YYMMMDD
    if len(teams) != 6: return None
    p1, p2 = teams[:3], teams[3:]
    if player_code == p1: return p2
    if player_code == p2: return p1
    return None


def run():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.executescript(SCHEMA)
    conn.commit()

    # 1. Pull settled ATP+WTA tickers in lookback window.
    cur = conn.execute("""
        SELECT kalshi_ticker, result
        FROM ticker_settlements
        WHERE (kalshi_ticker LIKE 'KXATPMATCH-%' OR kalshi_ticker LIKE 'KXWTAMATCH-%')
          AND fetched_at >= datetime('now', ?)
          AND result IN ('yes', 'no')
    """, (f"-{LOOKBACK_DAYS} days",))
    settled = []
    for tk, res in cur.fetchall():
        parsed = parse_game_key(tk)
        d = parse_match_date(tk)
        if not parsed or not d: continue
        tour, gk, code = parsed
        settled.append({
            "tour": tour, "game_key": gk, "code": code,
            "date": d, "won": (res == "yes"),
        })
    logger.info(f"Settled ATP+WTA in {LOOKBACK_DAYS}d: {len(settled)}")

    # 2. Look up player names from latest odds_observations rows (yes_sub_title)
    name_map = {}  # (tour, code) → display name
    cur = conn.execute("""
        SELECT kalshi_ticker, yes_sub_title FROM odds_observations
        WHERE (kalshi_ticker LIKE 'KXATPMATCH-%' OR kalshi_ticker LIKE 'KXWTAMATCH-%')
          AND yes_sub_title IS NOT NULL
          AND timestamp >= datetime('now', ?)
        GROUP BY kalshi_ticker
    """, (f"-{LOOKBACK_DAYS} days",))
    for tk, sub_title in cur.fetchall():
        p = parse_game_key(tk)
        if not p: continue
        tour, gk, code = p
        name_map[(tour, code)] = sub_title

    # 3. Group settled matches by (tour, player_code), sort by date desc
    by_player = defaultdict(list)
    for s in settled:
        by_player[(s["tour"], s["code"])].append(s)

    today = datetime.now(timezone.utc).date()
    upserts = 0
    for (tour, code), matches in by_player.items():
        matches.sort(key=lambda m: m["date"], reverse=True)
        last10 = matches[:10]
        last5 = matches[:5]
        l5_w = sum(1 for m in last5 if m["won"])
        l5_l = len(last5) - l5_w
        l10_w = sum(1 for m in last10 if m["won"])
        l10_l = len(last10) - l10_w
        last_date = matches[0]["date"].isoformat()
        days_since = (today - matches[0]["date"]).days
        opp_list = []
        for m in last10:
            opp_code = opponent_of(m["game_key"], code)
            opp_list.append({
                "date": m["date"].isoformat(),
                "opp_code": opp_code,
                "won": m["won"],
            })
        conn.execute("""
            INSERT INTO tennis_player_form
            (fetched_at, tour, player_code, player_name, last_match_date,
             days_since_last, l5_wins, l5_losses, l10_wins, l10_losses,
             last_opponents_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tour, player_code) DO UPDATE SET
              fetched_at=excluded.fetched_at,
              player_name=excluded.player_name,
              last_match_date=excluded.last_match_date,
              days_since_last=excluded.days_since_last,
              l5_wins=excluded.l5_wins, l5_losses=excluded.l5_losses,
              l10_wins=excluded.l10_wins, l10_losses=excluded.l10_losses,
              last_opponents_json=excluded.last_opponents_json
        """, (
            datetime.now(timezone.utc).isoformat(), tour, code,
            name_map.get((tour, code)),
            last_date, days_since, l5_w, l5_l, l10_w, l10_l,
            json.dumps(opp_list),
        ))
        upserts += 1

    conn.commit()
    conn.close()
    logger.info(f"Tennis player form upserted: {upserts} (ATP+WTA)")


if __name__ == "__main__":
    run()
