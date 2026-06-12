#!/usr/bin/env python3
"""Player-stat settlement for soft-book +EV PROP bets.

The Odds API gives only TEAM scores, so props (pitcher Ks, batter total bases /
HR) must be settled from a stats source. MLB via the free statsapi.mlb.com:
match the Odds API event (teams + date) -> gamePk -> boxscore -> player stat.
Used by softbook_ev_paper_pnl.py (with core.grade_prop). In-process caching so a
settle run fetches each schedule/boxscore once.
"""
import json, urllib.request, unicodedata
from datetime import datetime, timedelta

# prop market -> (boxscore stat group, field)
MLB_STAT = {"pitcher_strikeouts": ("pitching", "strikeOuts"),
            "batter_total_bases": ("batting", "totalBases"),
            "batter_home_runs": ("batting", "homeRuns"),
            "pitcher_outs": ("pitching", "outs"),
            "pitcher_earned_runs": ("pitching", "earnedRuns"),
            "pitcher_hits_allowed": ("pitching", "hits")}

# NBA props settle from ESPN box scores (statsapi-style feed is MLB-only).
# market -> stat label in ESPN's summary boxscore 'labels' array.
NBA_STAT = {"player_points": "PTS", "player_rebounds": "REB",
            "player_assists": "AST"}

DNP = "DNP"   # game FINAL but player never appeared (scratch/bench) -> void per book rules

_sched = {}   # date_str -> {(away_norm, home_norm): (gamePk, final_bool)}
_box = {}     # gamePk -> {player_key: {(group,field): value}} | None


def _get(url):
    return json.loads(urllib.request.urlopen(url, timeout=20).read())


def _nt(s):
    return (s or "").strip().lower()


def _np(s):
    """Normalize a player name to 'first last' (accent-strip, drop middle/suffix)."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    toks = [t for t in "".join(c if c.isalnum() or c == " " else " " for c in s).split()
            if t and t not in ("jr", "sr", "ii", "iii", "iv")]
    if not toks:
        return ""
    return f"{toks[0]} {toks[-1]}" if len(toks) >= 2 else toks[0]


def _schedule(date_str):
    if date_str in _sched:
        return _sched[date_str]
    out = {}
    try:
        d = _get(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date_str}")
        for dd in d.get("dates", []):
            for g in dd.get("games", []):
                t = g.get("teams", {})
                home = (t.get("home", {}).get("team", {}) or {}).get("name", "")
                away = (t.get("away", {}).get("team", {}) or {}).get("name", "")
                st = g.get("status", {})
                final = (st.get("abstractGameState") == "Final"
                         or st.get("detailedState") == "Final")
                out[(_nt(away), _nt(home))] = (g.get("gamePk"), final)
    except Exception:
        pass
    _sched[date_str] = out
    return out


def _boxscore(pk):
    if pk in _box:
        return _box[pk]
    res = {}
    try:
        d = _get(f"https://statsapi.mlb.com/api/v1/game/{pk}/boxscore")
        for side in ("home", "away"):
            for _, p in d.get("teams", {}).get(side, {}).get("players", {}).items():
                nm = _np(p.get("person", {}).get("fullName"))
                if not nm:
                    continue
                flat = {}
                for grp in ("batting", "pitching"):
                    for f, v in (p.get("stats", {}).get(grp, {}) or {}).items():
                        flat[(grp, f)] = v
                res[nm] = flat
    except Exception:
        res = None
    _box[pk] = res
    return res


_nba_sched = {}   # date_str -> {(away_norm, home_norm): (event_id, final_bool)}
_nba_box = {}     # event_id -> {player_key: {label: value}} | None


def _nba_schedule(date_str):
    if date_str in _nba_sched:
        return _nba_sched[date_str]
    out = {}
    try:
        d = _get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/"
                 f"scoreboard?dates={date_str.replace('-', '')}")
        for e in d.get("events", []):
            comp = (e.get("competitions") or [{}])[0]
            home = away = ""
            for c in comp.get("competitors", []):
                nm = (c.get("team") or {}).get("displayName", "")
                if c.get("homeAway") == "home":
                    home = nm
                else:
                    away = nm
            final = ((e.get("status") or {}).get("type") or {}).get("completed", False)
            out[(_nt(away), _nt(home))] = (e.get("id"), final)
    except Exception:
        pass
    _nba_sched[date_str] = out
    return out


def _nba_boxscore(eid):
    if eid in _nba_box:
        return _nba_box[eid]
    res = {}
    try:
        d = _get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/"
                 f"summary?event={eid}")
        for team in (d.get("boxscore") or {}).get("players", []):
            for grp in team.get("statistics", []):
                labels = grp.get("labels") or grp.get("names") or []
                for a in grp.get("athletes", []):
                    nm = _np((a.get("athlete") or {}).get("displayName"))
                    if not nm:
                        continue
                    if a.get("didNotPlay") or not a.get("stats"):
                        res.setdefault(nm, {})   # appeared in box but DNP
                        continue
                    res[nm] = dict(zip(labels, a["stats"]))
    except Exception:
        res = None
    _nba_box[eid] = res
    return res


def _nba_stat(away, home, commence_iso, market, player):
    label = NBA_STAT.get(market)
    if not label:
        return None
    for date_str in _et_dates(commence_iso):
        hit = _nba_schedule(date_str).get((_nt(away), _nt(home)))
        if not hit:
            continue
        eid, final = hit
        if not final:
            return None
        box = _nba_boxscore(eid)
        if not box:
            return None
        pdata = box.get(_np(player))
        if pdata is None or label not in pdata:
            return DNP                   # final + absent/empty line -> void
        try:
            return float(pdata[label])
        except Exception:
            return None
    return None


def _et_dates(commence_iso):
    try:
        dt = datetime.fromisoformat(commence_iso.replace("Z", "+00:00"))
    except Exception:
        return []
    et = dt - timedelta(hours=4)            # EDT; MLB schedule is keyed by US date
    return [et.strftime("%Y-%m-%d"),
            (et - timedelta(days=1)).strftime("%Y-%m-%d"),
            (et + timedelta(days=1)).strftime("%Y-%m-%d")]


def player_stat(sport, away, home, commence_iso, market, player):
    """Final stat value for the prop's player; DNP if the game is final but the
    player never appeared (-> void); None if not findable / not final yet."""
    if sport == "basketball_nba":
        return _nba_stat(away, home, commence_iso, market, player)
    if sport != "baseball_mlb":
        return None
    gp = MLB_STAT.get(market)
    if not gp:
        return None
    for date_str in _et_dates(commence_iso):
        hit = _schedule(date_str).get((_nt(away), _nt(home)))
        if not hit:
            continue
        pk, final = hit
        if not final:
            return None                      # game not final -> can't settle yet
        box = _boxscore(pk)
        if not box:
            return None
        pdata = box.get(_np(player))
        if pdata is None:
            return DNP                       # final + not in box -> scratched / not rostered
        v = pdata.get(gp)
        if v is None:
            return DNP                       # rostered but no appearance in this stat group
        try:
            return float(v)
        except Exception:
            return None
    return None


if __name__ == "__main__":
    # self-test on completed 2026-06-06 games
    cases = [
        ("baseball_mlb", "Seattle Mariners", "Detroit Tigers", "2026-06-06T17:11:00Z",
         "pitcher_strikeouts", "Ty Madden", 3.0),
        ("baseball_mlb", "Seattle Mariners", "Detroit Tigers", "2026-06-06T17:11:00Z",
         "batter_total_bases", "Josh Naylor", 1.0),
    ]
    for sport, a, h, ct, mk, pl, exp in cases:
        v = player_stat(sport, a, h, ct, mk, pl)
        print(f"  {pl:<16} {mk:<20} -> {v}  (expect {exp})  {'OK' if v == exp else 'CHECK'}")
