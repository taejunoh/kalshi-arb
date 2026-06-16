#!/usr/bin/env python3
"""External final-results adapters for the sports the Odds API /scores feed does
NOT cover, so the paper + app settlers can grade them instead of letting them rot
to stale-void (verified 2026-06-16: Odds API returns completed=false / empty
scores for tennis, KBO, NCAA baseball, MMA, boxing).

Sources (free, unauthenticated; same ESPN pattern as the NBA/WNBA/NHL/PGA collectors):
  ESPN site API : tennis (ATP/WTA), NCAA baseball, MMA (UFC)
  Naver Sports  : KBO (Korean baseball — ESPN has no KBO)
  boxing        : intentionally UNSUPPORTED (no reliable free structured source;
                  low volume) -> still stale-voids, backfill manually if needed.

grade_bet() is the single entry point used by softbook_ev_paper_pnl.settle and
softbook_app_settle.settle_app_bets. It returns 'win'/'lose'/'push'/'void'/None
(None = result not available yet -> leave the bet pending). Grading rules:
  - h2h  : by WINNER (robust for tennis upsets where the match winner wins FEWER
           total games, e.g. 1-6 6-4 6-4). draw/no-contest -> push.
  - spread/total : core.grade on the per-side score (tennis = total games won;
           baseball = runs). A RETIRED tennis match voids spread/total (the match
           didn't finish naturally — standard book rule) but its h2h still grades.
  - walkover / postponed / cancelled -> void (no action, game never completed).
HTTP is injectable (fetch_json) so the settlers and tests can stub it.
"""
import json, re, unicodedata, urllib.request
from datetime import datetime, timedelta, timezone

import softbook_ev_core as core

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"
NAVER_KBO = "https://api-gw.sports.naver.com/schedule/games"

# Naver (Korean) team abbreviation -> Odds API team name.
KBO_TEAMS = {
    "한화": "Hanwha Eagles", "NC": "NC Dinos", "KT": "KT Wiz", "두산": "Doosan Bears",
    "LG": "LG Twins", "KIA": "Kia Tigers", "롯데": "Lotte Giants",
    "SSG": "SSG Landers", "키움": "Kiwoom Heroes", "삼성": "Samsung Lions",
}


def handles(sport):
    """True iff this module can source results for the sport (Odds API can't)."""
    return (sport in ("baseball_ncaa", "baseball_kbo")
            or sport.startswith("tennis_")
            or sport.startswith("mma_"))


def _http_json(url, timeout=20):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://sports.news.naver.com/"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def _dt(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return None


def _ascii(name):
    s = unicodedata.normalize("NFKD", name or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def _norm(name):
    """Accent/punct/space-insensitive key (Iva Jović==Iva Jovic,
    Felix Auger-Aliassime==Felix Auger Aliassime)."""
    return re.sub(r"[^a-z0-9]", "", _ascii(name))


def _tokens(name):
    return set(re.findall(r"[a-z0-9]+", _ascii(name)))


def _name_match(odds_name, rec_name):
    """Match two raw names tolerant of: accents, punctuation, a Jr./suffix
    (substring), AND surname-first vs given-first ordering (Xinyu Wang ==
    Wang Xinyu, Shuai Zhang == Zhang Shuai) via order-insensitive token sets."""
    on, rn = _norm(odds_name), _norm(rec_name)
    if not on or not rn:
        return False
    if on == rn or on in rn or rn in on:
        return True
    ot, rt = _tokens(odds_name), _tokens(rec_name)        # order-insensitive
    return bool(ot) and bool(rt) and (ot == rt or ot <= rt or rt <= ot)


def _status(name):
    if name == "STATUS_FINAL":
        return "final"
    if name == "STATUS_RETIRED":
        return "retired"
    if name in ("STATUS_WALKOVER", "STATUS_FORFEIT", "STATUS_CANCELED",
                "STATUS_CANCELLED", "STATUS_POSTPONED", "STATUS_ABANDONED"):
        return "void"
    return "pending"


def _rec(a_name, b_name, a_score, b_score, winner, status, detail=""):
    return {"a_name": a_name, "b_name": b_name,
            "a_norm": _norm(a_name), "b_norm": _norm(b_name),
            "a_score": a_score, "b_score": b_score,
            "winner": winner, "status": status, "detail": detail}


# ---- per-provider fetchers (each returns a list of _rec; never raises) ----

def _fetch_tennis(tour, dt, fetch_json, cache):
    key = ("tennis", tour, dt.strftime("%Y%m%d"))
    if key in cache:
        return cache[key]
    out = []
    try:
        data = fetch_json(f"{ESPN_BASE}/tennis/{tour}/scoreboard?dates={dt.strftime('%Y%m%d')}")
        for e in data.get("events", []):
            for g in e.get("groupings", []):
                if "Singles" not in (g.get("grouping", {}).get("displayName") or ""):
                    continue                              # skip doubles (no athlete names)
                for c in g.get("competitions", []):
                    cs = c.get("competitors", [])
                    if len(cs) != 2:
                        continue

                    def side(s):
                        nm = (s.get("athlete", {}) or {}).get("displayName") or ""
                        games = sum((ls.get("value") or 0) for ls in s.get("linescores", []))
                        return nm, games, bool(s.get("winner"))
                    (an, ag, aw), (bn, bg, bw) = side(cs[0]), side(cs[1])
                    if not an or not bn:
                        continue
                    st = _status((c.get("status", {}).get("type", {}) or {}).get("name", ""))
                    winner = "a" if aw else ("b" if bw else None)
                    out.append(_rec(an, bn, ag, bg, winner, st))
    except Exception:
        out = []
    cache[key] = out
    return out


def _fetch_espn_team(sportpath, league, dt, fetch_json, cache):
    key = (sportpath, league, dt.strftime("%Y%m%d"))
    if key in cache:
        return cache[key]
    out = []
    try:
        data = fetch_json(f"{ESPN_BASE}/{sportpath}/{league}/scoreboard?dates={dt.strftime('%Y%m%d')}")
        for e in data.get("events", []):
            comp = (e.get("competitions") or [{}])[0]
            cs = comp.get("competitors", [])
            if len(cs) != 2:
                continue

            def side(s):
                nm = (s.get("team", {}) or {}).get("displayName") or ""
                try:
                    sc = float(s.get("score"))
                except (TypeError, ValueError):
                    sc = None
                return nm, sc, bool(s.get("winner"))
            (an, asc, aw), (bn, bsc, bw) = side(cs[0]), side(cs[1])
            if not an or not bn:
                continue
            st = _status((comp.get("status", {}).get("type", {}) or {}).get("name", ""))
            winner = "a" if aw else ("b" if bw else None)
            if winner is None and st == "final" and asc is not None and bsc is not None and asc != bsc:
                winner = "a" if asc > bsc else "b"      # derive from score if flag missing
            out.append(_rec(an, bn, asc, bsc, winner, st))
    except Exception:
        out = []
    cache[key] = out
    return out


def _fetch_mma(dt, fetch_json, cache):
    key = ("mma", "ufc", dt.strftime("%Y%m%d"))
    if key in cache:
        return cache[key]
    out = []
    try:
        data = fetch_json(f"{ESPN_BASE}/mma/ufc/scoreboard?dates={dt.strftime('%Y%m%d')}")
        for e in data.get("events", []):
            for c in e.get("competitions", []):
                cs = c.get("competitors", [])
                if len(cs) != 2:
                    continue

                def side(s):
                    return (s.get("athlete", {}) or {}).get("displayName") or "", bool(s.get("winner"))
                (an, aw), (bn, bw) = side(cs[0]), side(cs[1])
                if not an or not bn:
                    continue
                st = _status((c.get("status", {}).get("type", {}) or {}).get("name", ""))
                # final with no winner flag = draw / no-contest -> push on a 2-way ML
                winner = "a" if aw else ("b" if bw else ("draw" if st == "final" else None))
                out.append(_rec(an, bn, None, None, winner, st))
    except Exception:
        out = []
    cache[key] = out
    return out


def _fetch_kbo(dt, fetch_json, cache):
    ds = dt.strftime("%Y-%m-%d")
    key = ("kbo", ds)
    if key in cache:
        return cache[key]
    out = []
    try:
        data = fetch_json(f"{NAVER_KBO}?fromDate={ds}&toDate={ds}"
                          "&categoryId=kbo&upperCategoryId=kbaseball")
        for g in (data.get("result", {}) or {}).get("games", []):
            code = g.get("statusCode")
            st = ("final" if code == "RESULT"
                  else "void" if code in ("CANCEL", "CANCELED", "CANCELLED", "PPD", "SUSPENDED")
                  else "pending")
            away = KBO_TEAMS.get(g.get("awayTeamName"), g.get("awayTeamName"))
            home = KBO_TEAMS.get(g.get("homeTeamName"), g.get("homeTeamName"))
            asc, bsc = g.get("awayTeamScore"), g.get("homeTeamScore")
            winner = None
            if st == "final" and asc is not None and bsc is not None and asc != bsc:
                winner = "a" if asc > bsc else "b"
            out.append(_rec(away, home, asc, bsc, winner, st))   # a=away, b=home
    except Exception:
        out = []
    cache[key] = out
    return out


def _results_for(sport, commence_iso, fetch_json, cache):
    d = _dt(commence_iso)
    if d is None:
        return []
    if sport.startswith("tennis_"):
        tour = "wta" if "wta" in sport else "atp"
        recs = []
        for dd in (d, d - timedelta(days=1)):            # tournament-wide; ±day for tz
            recs += _fetch_tennis(tour, dd, fetch_json, cache)
        return recs
    if sport == "baseball_ncaa":
        recs = []
        for dd in (d, d - timedelta(days=1)):
            recs += _fetch_espn_team("baseball", "college-baseball", dd, fetch_json, cache)
        return recs
    if sport.startswith("mma_"):
        recs = []
        for dd in (d, d - timedelta(days=1)):            # late-ET cards bucket to prev day
            recs += _fetch_mma(dd, fetch_json, cache)
        return recs
    if sport == "baseball_kbo":
        kst = d + timedelta(hours=9)                     # Naver buckets by KST date
        return _fetch_kbo(kst, fetch_json, cache)
    return []


def _match(records, home, away):
    """Find the record for this matchup and orient a/b -> home/away by name."""
    for r in records:
        a, b = r["a_name"], r["b_name"]
        if _name_match(home, a) and _name_match(away, b):
            return {"home_score": r["a_score"], "away_score": r["b_score"],
                    "winner_side": ("home" if r["winner"] == "a" else
                                    "away" if r["winner"] == "b" else
                                    "draw" if r["winner"] == "draw" else None),
                    "status": r["status"]}
        if _name_match(home, b) and _name_match(away, a):
            return {"home_score": r["b_score"], "away_score": r["a_score"],
                    "winner_side": ("home" if r["winner"] == "b" else
                                    "away" if r["winner"] == "a" else
                                    "draw" if r["winner"] == "draw" else None),
                    "status": r["status"]}
    return None


def grade_bet(sport, home, away, commence_iso, market, side, point, player="",
              fetch_json=_http_json, cache=None):
    """-> 'win'|'lose'|'push'|'void'|None (None = no result yet, leave pending)."""
    if cache is None:
        cache = {}
    m = _match(_results_for(sport, commence_iso, fetch_json, cache), home, away)
    if m is None:
        return None
    st = m["status"]
    if st == "void":
        return "void"                                    # walkover / postponed / cancelled
    if st == "pending":
        return None
    if market == "h2h":                                  # moneyline by WINNER (RET still has one)
        ws = m["winner_side"]
        if ws == "draw":
            return "push"
        if ws not in ("home", "away") or side not in (home, away):
            return None
        return "win" if side == (home if ws == "home" else away) else "lose"
    if st == "retired":
        return "void"                                    # spread/total on a RET -> no action
    hs, as_ = m["home_score"], m["away_score"]
    if hs is None or as_ is None:
        return None
    scores = {home: hs, away: as_}
    if market == "team_total":
        return core.grade_prop(side, point, scores.get(player))
    return core.grade(market, side, point, home, away, scores)
