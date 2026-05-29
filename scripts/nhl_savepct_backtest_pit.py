"""NHL save% backtest — point-in-time (bias-free).

Replaces the 2026-05-26 attempt that used current-state save% (look-ahead biased,
same pattern as MLB v1). For each settled NHL game in the lookback window:

  1. Parse Kalshi ticker → (date, away_code, home_code, winner_code)
  2. Fetch NHL boxscore for that date → identify actual starting goalie (≥30 min TOI)
  3. For each starter, fetch gameLog and compute cumulative save% from games
     BEFORE the prediction date only
  4. Pull pre-game Kalshi yes_ask from odds_observations (latest pre-game snapshot)
  5. Compute "buy better-save% team YES at pre-game ya" trade and realized PnL

Aggregates overall EV/$1 + EV by save%-diff band.

Run: python3 scripts/nhl_savepct_backtest_pit.py
"""
import json
import os
import sqlite3
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timezone

DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")
LOOKBACK_DAYS = 60
KALSHI_FEE = 0.07
HEADERS = {"User-Agent": "kalshi-arb-nhl-bt/1.0"}


def http_json(url, timeout=15, retries=2):
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception as e:
            if attempt == retries:
                return None
            time.sleep(0.5 * (attempt + 1))
    return None


# Month code in ticker uses 3-letter abbrev (JAN, FEB, ..., DEC).
MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"])}


def parse_ticker(t):
    """KXNHLGAME-26APR21BOSBUF-BOS → (date_iso, away, home, side).

    Year is 2-digit, month is 3-letter, day is 2-digit, then two 3-letter
    team codes concatenated (AWAY then HOME). The trailing -SIDE indicates
    which team this contract pays out on (YES = that team wins).
    """
    body = t.replace("KXNHLGAME-", "")
    head, _, side = body.rpartition("-")
    if not side: return None
    yy = head[:2]
    mon = head[2:5]
    dd = head[5:7]
    if mon not in MONTHS: return None
    teams = head[7:]
    if len(teams) != 6: return None
    away, home = teams[:3], teams[3:]
    try:
        date_iso = f"20{yy}-{MONTHS[mon]:02d}-{int(dd):02d}"
    except ValueError:
        return None
    return date_iso, away, home, side


def get_settled_games(conn):
    """Return list of dicts: {date_iso, away, home, winner}."""
    cur = conn.execute("""
        SELECT kalshi_ticker, result FROM ticker_settlements
        WHERE kalshi_ticker LIKE 'KXNHLGAME-%'
          AND fetched_at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', ?)
    """, (f"-{LOOKBACK_DAYS} days",))
    by_game = {}
    for tk, res in cur.fetchall():
        p = parse_ticker(tk)
        if not p: continue
        date_iso, away, home, side = p
        key = (date_iso, away, home)
        if key not in by_game:
            by_game[key] = {"date_iso": date_iso, "away": away, "home": home, "winner": None}
        if res == "yes":
            by_game[key]["winner"] = side
    out = [g for g in by_game.values() if g["winner"] in (g["away"], g["home"])]
    return out


def fetch_boxscore_for_date(date_iso):
    """Return list of (game_id, away, home, away_goalies, home_goalies)."""
    d = http_json(f"https://api-web.nhle.com/v1/score/{date_iso}")
    games = []
    if not d: return games
    for g in d.get("games", []):
        if g.get("gameState") not in ("OFF", "FINAL"): continue
        games.append({
            "id": g.get("id"),
            "away": g.get("awayTeam", {}).get("abbrev"),
            "home": g.get("homeTeam", {}).get("abbrev"),
        })
    return games


def find_starter_from_box(game_id, team_abbrev):
    """Return (goalie_id, name, season_year) — goalie with ≥30 min TOI on team."""
    box = http_json(f"https://api-web.nhle.com/v1/gamecenter/{game_id}/boxscore")
    if not box: return None, None, None
    side = "awayTeam" if box.get("awayTeam", {}).get("abbrev") == team_abbrev else "homeTeam"
    goalies = box.get("playerByGameStats", {}).get(side, {}).get("goalies", [])
    def toi_seconds(g):
        t = (g.get("toi") or "0:0").split(":")
        return int(t[0]) * 60 + (int(t[1]) if len(t) > 1 else 0)
    if not goalies: return None, None, None
    starter = max(goalies, key=toi_seconds)
    if toi_seconds(starter) < 30 * 60:
        return None, None, None
    season = box.get("season")
    return starter.get("playerId"), starter.get("name", {}).get("default"), season


def point_in_time_savepct(player_id, season, game_date_iso):
    """Cumulative save% from gameLog up to (but not including) game_date_iso.
    Combines regular season + playoffs for the season."""
    saves_total = 0
    shots_total = 0
    games_count = 0
    for gt in (2, 3):  # 2 = regular, 3 = playoffs
        d = http_json(
            f"https://api-web.nhle.com/v1/player/{player_id}/game-log/{season}/{gt}")
        if not d: continue
        for g in d.get("gameLog", []):
            g_date = g.get("gameDate")
            if not g_date or g_date >= game_date_iso:
                continue
            sa = g.get("shotsAgainst")
            ga = g.get("goalsAgainst")
            if sa is None or ga is None or sa == 0:
                continue
            saves_total += (sa - ga)
            shots_total += sa
            games_count += 1
    if shots_total == 0 or games_count < 3:
        return None, 0
    return saves_total / shots_total, games_count


def get_pregame_ya(conn, date_iso, away, home, side):
    """Latest pre-game (mts>30) yes_ask for the -SIDE ticker."""
    yy = date_iso[2:4]
    mon = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"][int(date_iso[5:7]) - 1]
    dd = date_iso[8:10]
    prefix = f"KXNHLGAME-{yy}{mon}{dd}{away}{home}-{side}"
    cur = conn.execute("""
        SELECT kalshi_yes_ask, minutes_to_start FROM odds_observations
        WHERE kalshi_ticker = ?
          AND minutes_to_start > 30
          AND kalshi_yes_ask IS NOT NULL
        ORDER BY minutes_to_start ASC
        LIMIT 1
    """, (prefix,))
    row = cur.fetchone()
    return row[0] if row else None


def main():
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA query_only = 1")

    games = get_settled_games(conn)
    print(f"NHL games last {LOOKBACK_DAYS}d: {len(games)}")

    # group games by date for batched boxscore fetch
    by_date = defaultdict(list)
    for g in games:
        by_date[g["date_iso"]].append(g)

    enriched = []
    for di in sorted(by_date):
        boxes = fetch_boxscore_for_date(di)
        box_by_pair = {(b["away"], b["home"]): b for b in boxes}
        for g in by_date[di]:
            b = box_by_pair.get((g["away"], g["home"]))
            if not b:
                continue
            g["game_id"] = b["id"]
            enriched.append(g)
        time.sleep(0.1)
    print(f"Games with NHL game_id resolved: {len(enriched)}")

    rows = []
    for i, g in enumerate(enriched):
        if i % 5 == 0:
            print(f"  processing {i}/{len(enriched)}...")
        di = g["date_iso"]
        away, home = g["away"], g["home"]
        away_id, _, season_a = find_starter_from_box(g["game_id"], away)
        home_id, _, season_h = find_starter_from_box(g["game_id"], home)
        time.sleep(0.1)
        if not away_id or not home_id:
            continue
        season = season_a or season_h
        a_pct, a_n = point_in_time_savepct(away_id, season, di)
        h_pct, h_n = point_in_time_savepct(home_id, season, di)
        if a_pct is None or h_pct is None:
            continue
        diff = abs(a_pct - h_pct)
        better_side = away if a_pct > h_pct else home
        # Pull pre-game ya for the BETTER-save% team
        ya = get_pregame_ya(conn, di, away, home, better_side)
        if ya is None:
            continue
        won = g["winner"] == better_side
        # Buy YES at ya, settles at $1 if won else $0; fee deducted both ways
        pnl = (1.0 - ya - KALSHI_FEE) if won else (-ya - KALSHI_FEE)
        rows.append({
            "date": di, "away": away, "home": home,
            "a_pct": a_pct, "h_pct": h_pct, "diff": diff,
            "better": better_side, "ya": ya, "won": won, "pnl": pnl,
            "a_n": a_n, "h_n": h_n,
        })

    if not rows:
        print("\nNo rows produced — check API access / lookback window.")
        return

    print(f"\n=== Results: n={len(rows)} ===\n")
    total_pnl = sum(r["pnl"] for r in rows)
    wins = sum(1 for r in rows if r["won"])
    print(f"Total: n={len(rows)}  pnl=${total_pnl:.2f}  EV/$1=${total_pnl/len(rows):.3f}  WR={100*wins/len(rows):.1f}%")
    avg_ya = sum(r["ya"] for r in rows) / len(rows)
    avg_diff = sum(r["diff"] for r in rows) / len(rows)
    print(f"  avg ya={avg_ya:.3f}  avg |diff|={avg_diff:.4f}")

    bands = [
        ("<0.005", 0.0, 0.005),
        ("0.005-0.010", 0.005, 0.010),
        ("0.010-0.020", 0.010, 0.020),
        ("0.020-0.050", 0.020, 0.050),
        (">=0.050", 0.050, 1.0),
    ]
    print(f"\nBy |save%-diff| band:")
    print(f"{'band':<14} {'n':>4} {'WR%':>6} {'EV/$1':>9} {'avg_ya':>8}")
    for name, lo, hi in bands:
        sub = [r for r in rows if lo <= r["diff"] < hi]
        if not sub:
            print(f"{name:<14} {0:>4} {'—':>6} {'—':>9} {'—':>8}")
            continue
        n = len(sub)
        w = sum(1 for r in sub if r["won"])
        p = sum(r["pnl"] for r in sub)
        y = sum(r["ya"] for r in sub) / n
        print(f"{name:<14} {n:>4} {100*w/n:>5.1f}% {p/n:>+8.3f} {y:>8.3f}")

    print(f"\nSample rows (first 8):")
    for r in rows[:8]:
        print(f"  {r['date']} {r['away']}@{r['home']} "
              f"a={r['a_pct']:.3f}/h={r['h_pct']:.3f} diff={r['diff']:.4f} "
              f"→ {r['better']} ya={r['ya']:.3f} "
              f"{'W' if r['won'] else 'L'} pnl={r['pnl']:+.3f}")


if __name__ == "__main__":
    main()
