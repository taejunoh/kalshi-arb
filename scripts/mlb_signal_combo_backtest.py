"""MLB 7-signal × 127-combo backtest.

For each settled MLB game in last 60 days, computes 7 binary signals using
point-in-time data, then evaluates every non-empty subset of the 7 signals
as a "fire only when ALL signals in subset are true" gate. Direction is
fixed: bet YES on team with LOWER point-in-time ERA (the "better" pitcher).

Signals:
  1. ERA_DIFF_2: era_diff_pit ≥ 2.0
  2. WEATHER_COLD: game-time temp < 60°F (outdoor stadium only; dome = False)
  3. PIN_AGREE: |pinnacle_yes_prob(better team) - kalshi_ya(better team)| < 0.05
  4. YA_BAND: 0.40 ≤ kalshi_ya(better team) ≤ 0.75
  5. BETTER_IS_HOME: better-ERA team is the home team
  6. MTS_LATE: minutes_to_start < 60 at observation snapshot
  7. BOOKS_DEEP: (n_books_eu + n_books_us) ≥ 10

PnL: bet YES on better team's ticker at observation snapshot ya; if better
team won, +(1 - ya - fee) else -(ya + fee), fee = $0.07/ct.

Run: python3 scripts/mlb_signal_combo_backtest.py
"""
import itertools
import json
import os
import sqlite3
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

DB = os.getenv("KALSHI_BOT_DB", "/home/ubuntu/kalshi-arb/data/bot.db")
STADIUMS_FILE = "/home/ubuntu/kalshi-arb/data/mlb_stadiums.json"
LOOKBACK_DAYS = 60
KALSHI_FEE = 0.07
HEADERS = {"User-Agent": "kalshi-arb-mlb-bt/1.0"}

TEAM_MAP = {
    108: "LAA", 109: "AZ",  110: "BAL", 111: "BOS", 112: "CHC", 113: "CIN",
    114: "CLE", 115: "COL", 116: "DET", 117: "HOU", 118: "KC",  119: "LAD",
    120: "WSH", 121: "NYM", 133: "ATH", 134: "PIT", 135: "SD",  136: "SEA",
    137: "SF",  138: "STL", 139: "TB",  140: "TEX", 141: "TOR", 142: "MIN",
    143: "PHI", 144: "ATL", 145: "CWS", 146: "MIA", 147: "NYY", 158: "MIL",
}
MONTHS = {m: i+1 for i, m in enumerate(
    ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"])}


def http_json(url, timeout=15, retries=2):
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception:
            if attempt == retries:
                return None
            time.sleep(0.6 * (attempt + 1))
    return None


def parse_ticker(t):
    """KXMLBGAME-26APR21BOSBAL-BOS → (date_iso, away, home, side)."""
    body = t.replace("KXMLBGAME-", "")
    head, _, side = body.rpartition("-")
    if not side: return None
    yy = head[:2]; mon = head[2:5]; dd = head[5:7]
    if mon not in MONTHS: return None
    hhmm = head[7:11]
    teams = head[11:]
    if len(teams) != 6: return None
    away, home = teams[:3], teams[3:]
    try:
        date_iso = f"20{yy}-{MONTHS[mon]:02d}-{int(dd):02d}"
    except ValueError:
        return None
    return date_iso, away, home, side


def parse_ip(ip_str):
    try:
        s = str(ip_str)
        if "." in s:
            w, f = s.split(".")
            return float(w) + float(f) / 3.0
        return float(s)
    except Exception:
        return 0.0


def get_settled_games(conn):
    cur = conn.execute("""
        SELECT kalshi_ticker, result FROM ticker_settlements
        WHERE kalshi_ticker LIKE 'KXMLBGAME-%'
          AND fetched_at >= datetime('now', ?)
    """, (f"-{LOOKBACK_DAYS} days",))
    by_game = {}
    for tk, res in cur.fetchall():
        p = parse_ticker(tk)
        if not p: continue
        date_iso, away, home, side = p
        key = (date_iso, away, home)
        if key not in by_game:
            by_game[key] = {"date_iso": date_iso, "away": away, "home": home,
                            "ticker_home": None, "ticker_away": None,
                            "winner": None}
        prefix = tk.rsplit("-", 1)[0]
        if side == home:
            by_game[key]["ticker_home"] = tk
        elif side == away:
            by_game[key]["ticker_away"] = tk
        if res == "yes":
            by_game[key]["winner"] = side
    out = [g for g in by_game.values()
           if g["winner"] in (g["away"], g["home"])
           and g["ticker_home"] and g["ticker_away"]]
    return out


def fetch_schedule(date_iso):
    return http_json(
        f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date_iso}"
    )


def fetch_boxscore(game_pk):
    return http_json(
        f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"
    )


def get_starter_id(box, side):
    """side ∈ {home, away}. Returns (player_id, name) of starting pitcher."""
    try:
        boxscore = box.get("liveData", {}).get("boxscore", {})
        team = boxscore.get("teams", {}).get(side, {})
        # Pitchers array — first entry is starter
        pitchers = team.get("pitchers", [])
        if not pitchers: return None, None
        starter_pid = pitchers[0]
        players = team.get("players", {})
        p = players.get(f"ID{starter_pid}", {})
        name = p.get("person", {}).get("fullName")
        return starter_pid, name
    except Exception:
        return None, None


def fetch_gamelog(pitcher_id, season):
    d = http_json(
        f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats?"
        f"stats=gameLog&season={season}&group=pitching"
    )
    out = []
    if not d: return out
    for s in d.get("stats", [{}])[0].get("splits", []):
        date = s.get("date")
        stat = s.get("stat", {})
        ip = parse_ip(stat.get("inningsPitched", 0))
        er = int(stat.get("earnedRuns", 0))
        if date and ip > 0:
            out.append((date, ip, er))
    return sorted(out)


def era_before(gamelog, game_date):
    total_ip, total_er = 0.0, 0
    for d, ip, er in gamelog:
        if d < game_date:
            total_ip += ip; total_er += er
    if total_ip < 5:
        return None, total_ip
    return (total_er * 9.0) / total_ip, total_ip


def load_stadiums():
    with open(STADIUMS_FILE) as f:
        data = json.load(f)
    return {s["team"]: s for s in data["stadiums"]}


_weather_cache = {}
def fetch_weather_archive(lat, lon, start_date, end_date):
    """Returns dict {datetime_iso: (temp_c, wind_kmh)}."""
    key = (round(lat, 3), round(lon, 3), start_date, end_date)
    if key in _weather_cache:
        return _weather_cache[key]
    d = http_json(
        f"https://archive-api.open-meteo.com/v1/archive?"
        f"latitude={lat}&longitude={lon}"
        f"&start_date={start_date}&end_date={end_date}"
        f"&hourly=temperature_2m,wind_speed_10m&timezone=UTC"
    )
    out = {}
    if d:
        h = d.get("hourly", {})
        for i, t in enumerate(h.get("time", [])):
            temp = h.get("temperature_2m", [None]*999)[i]
            wind = h.get("wind_speed_10m", [None]*999)[i]
            out[t] = (temp, wind)
    _weather_cache[key] = out
    return out


def get_pregame_obs(conn, kalshi_ticker):
    """Pre-game odds snapshot — pick latest with mts in (30, 240)."""
    cur = conn.execute("""
        SELECT minutes_to_start, kalshi_yes_ask, pinnacle_yes_prob,
               is_yes_home, n_books_eu, n_books_us, has_pinnacle
        FROM odds_observations
        WHERE kalshi_ticker = ?
          AND minutes_to_start > 30 AND minutes_to_start < 240
          AND kalshi_yes_ask IS NOT NULL
        ORDER BY minutes_to_start ASC
        LIMIT 1
    """, (kalshi_ticker,))
    return cur.fetchone()


def main():
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA query_only = 1")
    stadiums = load_stadiums()

    games = get_settled_games(conn)
    print(f"Settled MLB games last {LOOKBACK_DAYS}d: {len(games)}")

    by_date = defaultdict(list)
    for g in games:
        by_date[g["date_iso"]].append(g)

    sched_cache = {}
    gamelog_cache = {}
    weather_by_team_date = {}

    rows = []
    processed = 0
    for di in sorted(by_date):
        if di not in sched_cache:
            sched_cache[di] = fetch_schedule(di) or {}
            time.sleep(0.1)
        sched = sched_cache[di]
        # Build (away_id, home_id) → game_pk + datetime
        sched_games = {}
        for dobj in sched.get("dates", []):
            for sg in dobj.get("games", []):
                aid = sg["teams"]["away"]["team"]["id"]
                hid = sg["teams"]["home"]["team"]["id"]
                ac = TEAM_MAP.get(aid); hc = TEAM_MAP.get(hid)
                if not (ac and hc): continue
                sched_games[(ac, hc)] = {
                    "pk": sg.get("gamePk"),
                    "gameDate": sg.get("gameDate"),
                }
        season = int(di[:4])
        for g in by_date[di]:
            processed += 1
            sg = sched_games.get((g["away"], g["home"]))
            if not sg or not sg["pk"]: continue
            box = fetch_boxscore(sg["pk"])
            if not box: continue
            a_pid, a_name = get_starter_id(box, "away")
            h_pid, h_name = get_starter_id(box, "home")
            time.sleep(0.05)
            if not a_pid or not h_pid: continue
            if a_pid not in gamelog_cache:
                gamelog_cache[a_pid] = fetch_gamelog(a_pid, season)
                time.sleep(0.05)
            if h_pid not in gamelog_cache:
                gamelog_cache[h_pid] = fetch_gamelog(h_pid, season)
                time.sleep(0.05)
            a_era, a_ip = era_before(gamelog_cache[a_pid], di)
            h_era, h_ip = era_before(gamelog_cache[h_pid], di)
            if a_era is None or h_era is None: continue
            # Cap ERAs to v2 range [1.0, 8.0] — outside = skip (matches strategy gate)
            if not (1.0 <= a_era <= 8.0) or not (1.0 <= h_era <= 8.0): continue

            era_diff = abs(a_era - h_era)
            better_is_home = (h_era < a_era)
            better_team = g["home"] if better_is_home else g["away"]
            ticker = g["ticker_home"] if better_is_home else g["ticker_away"]
            obs = get_pregame_obs(conn, ticker)
            if not obs: continue
            mts, ya, pin_yp, is_yh, nb_eu, nb_us, has_pin = obs
            if ya is None: continue
            # Pinnacle prob on the better-team ticker (flip if needed)
            pin_better = None
            if pin_yp is not None:
                # odds_observations.pinnacle_yes_prob is for THIS specific ticker
                # (which is the better-team yes ticker), so it's already aligned
                pin_better = pin_yp

            # Weather
            wcold = False
            stadium = stadiums.get(g["home"])
            if stadium and stadium.get("roof") != "dome":
                key = (g["home"], di)
                if key not in weather_by_team_date:
                    wdata = fetch_weather_archive(
                        stadium["lat"], stadium["lon"], di, di
                    )
                    weather_by_team_date[key] = wdata
                    time.sleep(0.1)
                wdata = weather_by_team_date[key]
                if wdata:
                    # Pick hour closest to gameDate (UTC)
                    try:
                        game_dt = datetime.fromisoformat(
                            sg["gameDate"].replace("Z", "+00:00"))
                        target = game_dt.replace(minute=0, second=0,
                                                  microsecond=0, tzinfo=None).isoformat()
                        if target in wdata:
                            temp_c, _ = wdata[target]
                            if temp_c is not None:
                                temp_f = temp_c * 9.0 / 5.0 + 32.0
                                wcold = temp_f < 60.0
                    except Exception:
                        pass

            won = g["winner"] == better_team
            pnl = (1.0 - ya - KALSHI_FEE) if won else (-ya - KALSHI_FEE)

            # Build signal features
            sig = {
                "ERA_DIFF_2": era_diff >= 2.0,
                "WEATHER_COLD": wcold,
                "PIN_AGREE": (pin_better is not None
                              and abs(pin_better - ya) < 0.05),
                "YA_BAND": 0.40 <= ya <= 0.75,
                "BETTER_IS_HOME": better_is_home,
                "MTS_LATE": mts is not None and mts < 60,
                "BOOKS_DEEP": (nb_eu or 0) + (nb_us or 0) >= 10,
            }
            rows.append({
                "date": di, "away": g["away"], "home": g["home"],
                "a_era": a_era, "h_era": h_era, "era_diff": era_diff,
                "better": better_team, "ya": ya, "won": won, "pnl": pnl,
                "sig": sig,
            })
            if processed % 20 == 0:
                print(f"  processed {processed}/{len(games)}  rows so far: {len(rows)}")

    if not rows:
        print("\nNo rows — check API access.")
        return

    print(f"\n=== Total backtest rows: {len(rows)} ===")
    print(f"Overall (no filter): n={len(rows)} "
          f"pnl=${sum(r['pnl'] for r in rows):.2f} "
          f"EV/$1=${sum(r['pnl'] for r in rows)/len(rows):.3f} "
          f"WR={100*sum(1 for r in rows if r['won'])/len(rows):.1f}%")

    SIGNALS = ["ERA_DIFF_2", "WEATHER_COLD", "PIN_AGREE", "YA_BAND",
               "BETTER_IS_HOME", "MTS_LATE", "BOOKS_DEEP"]
    print(f"\nPer-signal base rates (raw):")
    for s in SIGNALS:
        sub = [r for r in rows if r["sig"][s]]
        if not sub: continue
        n = len(sub); w = sum(1 for r in sub if r["won"])
        p = sum(r["pnl"] for r in sub)
        print(f"  {s:<16} n={n:>4}  WR={100*w/n:>5.1f}%  EV={p/n:+.3f}")

    print(f"\nEnumerating {2**len(SIGNALS) - 1} non-empty combos...")
    results = []
    for k in range(1, len(SIGNALS) + 1):
        for combo in itertools.combinations(SIGNALS, k):
            sub = [r for r in rows if all(r["sig"][s] for s in combo)]
            if len(sub) < 15: continue
            n = len(sub); w = sum(1 for r in sub if r["won"])
            p = sum(r["pnl"] for r in sub)
            results.append({
                "combo": combo, "k": k, "n": n, "wr": w / n,
                "ev": p / n, "pnl": p,
            })

    print(f"Combos with n≥15: {len(results)}")

    print(f"\n=== TOP 15 by EV/$1 ===")
    print(f"{'k':<2} {'combo':<60} {'n':>4} {'WR%':>6} {'EV/$1':>9} {'PnL':>9}")
    for r in sorted(results, key=lambda x: -x["ev"])[:15]:
        c = "+".join(r["combo"])
        print(f"{r['k']:<2} {c:<60} {r['n']:>4} {100*r['wr']:>5.1f}% "
              f"{r['ev']:>+8.3f} {r['pnl']:>+8.2f}")

    print(f"\n=== BOTTOM 10 by EV/$1 (avoid zones) ===")
    for r in sorted(results, key=lambda x: x["ev"])[:10]:
        c = "+".join(r["combo"])
        print(f"{r['k']:<2} {c:<60} {r['n']:>4} {100*r['wr']:>5.1f}% "
              f"{r['ev']:>+8.3f} {r['pnl']:>+8.2f}")

    # n >= 30 subset for higher confidence
    high_n = [r for r in results if r["n"] >= 30]
    print(f"\n=== TOP 10 by EV (n>=30 for higher confidence) ===")
    for r in sorted(high_n, key=lambda x: -x["ev"])[:10]:
        c = "+".join(r["combo"])
        print(f"{r['k']:<2} {c:<60} {r['n']:>4} {100*r['wr']:>5.1f}% "
              f"{r['ev']:>+8.3f} {r['pnl']:>+8.2f}")


if __name__ == "__main__":
    main()
