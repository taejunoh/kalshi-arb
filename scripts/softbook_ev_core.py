#!/usr/bin/env python3
"""Shared +EV core — soft-book vs sharp Pinnacle across H2H / SPREAD / TOTAL.

The "full version": for each soft-book offer (moneyline, OR a spread at line P,
OR a total at line T), we look up Pinnacle's price AT THE SAME LINE — using
Pinnacle's full alternate-line ladder — and de-vig the two complementary sides
to get the sharp fair probability there. EV = fair * soft_price - 1. No line
interpolation (only compute where Pinnacle actually quotes that exact line), so
key-numbers (3/7 in football, the 1.5 run line, etc.) are never guessed.

Pairing (verified on live data):
  H2H    : (team)            de-vig vs (other team)
  SPREAD : (team, +P)        de-vig vs (other team, -P)
  TOTAL  : (Over/Under, T)   de-vig vs (Under/Over, T)

One per-event call to /events/{id}/odds fetches every market+line for every book.
Used by softbook_ev_today.py (bet list), _recorder.py (CLV), _paper_pnl.py (settle).
"""
import os, json, urllib.request
from datetime import datetime, timezone

NY_LEGAL = {"fanduel": "FanDuel", "draftkings": "DraftKings", "betmgm": "BetMGM",
            "caesars": "Caesars", "betrivers": "BetRivers", "fanatics": "Fanatics"}
ODDS_MIN, ODDS_MAX = 1.50, 4.00      # bettable price band (cuts noise extremes)
EV_MIN, EV_MAX = 0.01, 0.08          # bettable EV band (>8% = stale/limited)
MAX_HOURS = 72                       # pre-game, near-term only
# alternates SUPERSET the main line, but request mains too in case a soft book
# offers no alternates -> we still capture its main spread/total.
MARKETS = "h2h,spreads,totals,alternate_spreads,alternate_totals"
B = "https://api.the-odds-api.com/v4/sports"


def read_key():
    for line in open(os.getenv("ENV_PATH", "/home/ubuntu/kalshi-arb/.env")):
        if line.startswith("ODDS_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.getenv("ODDS_API_KEY")


def hrs_to(ct):
    try:
        return (datetime.fromisoformat(ct.replace("Z", "+00:00"))
                - datetime.now(timezone.utc)).total_seconds() / 3600
    except Exception:
        return None


def soon(ct):
    h = hrs_to(ct)
    return h is not None and 0 < h <= MAX_HOURS


def active_sports(key):
    try:
        d = json.loads(urllib.request.urlopen(
            f"{B}/?apiKey={key}", timeout=20).read())
        return [s["key"] for s in d if s.get("active") and not s.get("has_outrights")]
    except Exception:
        return ["baseball_mlb", "basketball_wnba"]


def list_events(sport, key):
    """Near-term (pre-game, <=72h) events for a sport. Cheap bulk h2h call."""
    try:
        games = json.loads(urllib.request.urlopen(
            f"{B}/{sport}/odds/?apiKey={key}&regions=us&markets=h2h&oddsFormat=decimal",
            timeout=25).read())
    except Exception:
        return []
    return [g for g in games if soon(g.get("commence_time"))]


def event_odds(sport, eid, key):
    """Full per-event odds (all books, h2h+spreads+totals+alternates). Returns
    (data, credits_used) — credits from the x-requests-last response header."""
    url = (f"{B}/{sport}/events/{eid}/odds/?apiKey={key}&regions=us,eu"
           f"&markets={MARKETS}&oddsFormat=decimal")
    r = urllib.request.urlopen(url, timeout=30)
    data = json.loads(r.read())
    try:
        credits = int(r.headers.get("x-requests-last", "0"))
    except Exception:
        credits = 0
    return data, credits


def _devig(pa, pb):
    ia, ib = 1.0 / pa, 1.0 / pb
    return ia / (ia + ib)


def _pin_index(pin_book):
    """Flat price lookup from Pinnacle's markets: (kind, name, point) -> price.
    kind in {'h2h','spread','total'}; point is None for h2h, else round(pt,1)."""
    idx = {}
    for m in pin_book.get("markets", []):
        k = m["key"]
        kind = ("h2h" if k == "h2h" else
                "spread" if "spreads" in k else
                "total" if "totals" in k else None)
        if not kind:
            continue
        for o in m["outcomes"]:
            pt = None if kind == "h2h" else round(o.get("point", 0.0), 1)
            idx[(kind, o["name"], pt)] = o["price"]   # last wins (main==alt anyway)
    return idx


def _fair(idx, kind, name, point, home, away):
    """Pinnacle de-vigged fair prob for the bet (kind,name,point), or None if the
    complementary sharp price isn't quoted at that exact line."""
    pa = idx.get((kind, name, point))
    if pa is None:
        return None
    if kind == "total":
        opp = "Under" if name == "Over" else "Over"
        pb = idx.get((kind, opp, point))
    else:  # h2h or spread: complement is the OTHER team (spread: at -point)
        other = away if name == home else home
        pb = idx.get((kind, other, None if kind == "h2h" else round(-point, 1)))
    if pb is None:
        return None
    return _devig(pa, pb)


def label(market, side, point):
    if market == "h2h":
        return f"{side} ML"
    if market == "spread":
        return f"{side} {point:+g}"
    return f"{side} {point:g}"            # total: "Over 8.5"


def event_ev_bets(ev, sport):
    """All qualifying +EV soft-book bets for one event (across h2h/spread/total).
    Returns a list of dicts; caller dedups (e.g. best per market)."""
    home, away = ev.get("home_team"), ev.get("away_team")
    bk = {b["key"]: b for b in ev.get("bookmakers", [])}
    pin = bk.get("pinnacle")
    if not pin:
        return []
    idx = _pin_index(pin)
    out = []
    for bkey, blabel in NY_LEGAL.items():
        b = bk.get(bkey)
        if not b:
            continue
        for m in b.get("markets", []):
            k = m["key"]
            market = ("h2h" if k == "h2h" else
                      "spread" if "spreads" in k else
                      "total" if "totals" in k else None)
            if not market:
                continue
            for o in m["outcomes"]:
                price = o["price"]
                if not (ODDS_MIN <= price <= ODDS_MAX):
                    continue
                point = None if market == "h2h" else round(o.get("point", 0.0), 1)
                fair = _fair(idx, market, o["name"], point, home, away)
                if fair is None:
                    continue
                ev_val = fair * price - 1
                if EV_MIN <= ev_val <= EV_MAX:
                    out.append({
                        "event_id": ev.get("id"), "sport": sport,
                        "commence_time": ev.get("commence_time"),
                        "home": home, "away": away,
                        "market": market, "side": o["name"], "point": point,
                        "label": label(market, o["name"], point),
                        "book": blabel, "odds": price, "fair": fair, "ev": ev_val,
                        "pin_price": idx.get((market, o["name"], point)),
                    })
    return out


def best_per_market(bets):
    """Dedup to the single best-EV bet per (event, market) — so a game yields at
    most one moneyline + one spread + one total (never both sides of a market)."""
    best = {}
    for b in bets:
        k = (b["event_id"], b["market"])
        if k not in best or b["ev"] > best[k]["ev"]:
            best[k] = b
    return sorted(best.values(), key=lambda b: -b["ev"])


def grade(market, side, point, home, away, scores):
    """Settle a bet given final scores {team: int}. Returns 'win'/'lose'/'push'/None."""
    try:
        if market == "total":
            tot = scores[home] + scores[away]
            if tot == point:
                return "push"
            over = tot > point
            return "win" if (over == (side == "Over")) else "lose"
        other = away if side == home else home
        margin = scores[side] - scores[other]
        if market == "spread":
            margin += point
        if margin == 0:
            return "push"
        return "win" if margin > 0 else "lose"
    except Exception:
        return None


def scan_sport(sport, key, per_event_budget=None):
    """Live: list near-term events, fetch each, collect best +EV per market.
    Returns (bets, credits_used, n_events)."""
    evs = list_events(sport, key)
    bets, credits = [], 0
    for g in evs:
        if per_event_budget is not None and credits >= per_event_budget:
            break
        try:
            data, c = event_odds(sport, g["id"], key)
            credits += c
            bets += event_ev_bets(data, sport)
        except Exception:
            continue
    return best_per_market(bets), credits, len(evs)


if __name__ == "__main__":
    import sys
    k = read_key()
    sport = sys.argv[1] if len(sys.argv) > 1 else "baseball_mlb"
    bets, credits, nev = scan_sport(sport, k)
    print(f"{sport}: {nev} near-term events, {credits} API credits used, "
          f"{len(bets)} +EV bets (best per market):\n")
    for b in bets:
        print(f"  {b['away'][:14]:<14}@{b['home'][:14]:<14} | {b['market']:<6} "
              f"{b['label']:<18} {b['book']:<11} {b['odds']:.2f}  fair {b['fair']*100:4.1f}%  "
              f"EV {b['ev']*100:+.2f}%")
    if not bets:
        print("  (none in band right now)")
