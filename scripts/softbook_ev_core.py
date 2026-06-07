#!/usr/bin/env python3
"""Shared +EV core — soft-book vs sharp Pinnacle across H2H / SPREAD / TOTAL / PROPS.

The "full version": for each soft-book offer (moneyline, spread@P, total@T, or a
PLAYER PROP like pitcher strikeouts / batter total bases / home runs at line L),
we look up Pinnacle's price AT THE SAME LINE — via Pinnacle's alternate ladder
for game markets — and de-vig the two complementary sides for the sharp fair
probability. EV = fair * soft_price - 1. No line interpolation (only where
Pinnacle quotes the exact line) so key numbers are never guessed.

De-vig pairing (verified live):
  H2H    : (team)              vs (other team)
  SPREAD : (team, +P)          vs (other team, -P)
  TOTAL  : (Over/Under, T)     vs (Under/Over, T)
  PROP   : (Over/Under, T, player) vs (Under/Over, T, same player)

Props settle on PLAYER stats (Odds API gives only team scores) -> see
softbook_ev_stats.py. CLV needs no stats; realized PnL does.
"""
import os, json, urllib.request
from datetime import datetime, timezone

NY_LEGAL = {"fanduel": "FanDuel", "draftkings": "DraftKings", "betmgm": "BetMGM",
            "caesars": "Caesars", "betrivers": "BetRivers", "fanatics": "Fanatics"}
ODDS_MIN, ODDS_MAX = 1.50, 4.00
EV_MIN, EV_MAX = 0.01, 0.08
MAX_HOURS = 72
B = "https://api.the-odds-api.com/v4/sports"

GAME_MARKETS = "h2h,spreads,totals,alternate_spreads,alternate_totals"
# Player-prop markets to scan, per sport — ONLY ones Pinnacle reliably quotes
# (so we have a sharp reference). Short labels for display + the stat to settle on.
PROP_MARKETS = {
    "baseball_mlb": ["pitcher_strikeouts", "batter_total_bases", "batter_home_runs"],
}
PROP_SHORT = {"pitcher_strikeouts": "K", "batter_total_bases": "TB", "batter_home_runs": "HR"}
ALL_PROPS = {m for v in PROP_MARKETS.values() for m in v}


def markets_for(sport):
    props = PROP_MARKETS.get(sport, [])
    return GAME_MARKETS + (("," + ",".join(props)) if props else "")


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
        d = json.loads(urllib.request.urlopen(f"{B}/?apiKey={key}", timeout=20).read())
        return [s["key"] for s in d if s.get("active") and not s.get("has_outrights")]
    except Exception:
        return ["baseball_mlb", "basketball_wnba"]


def list_events(sport, key):
    try:
        games = json.loads(urllib.request.urlopen(
            f"{B}/{sport}/odds/?apiKey={key}&regions=us&markets=h2h&oddsFormat=decimal",
            timeout=25).read())
    except Exception:
        return []
    return [g for g in games if soon(g.get("commence_time"))]


def event_odds(sport, eid, key, markets=GAME_MARKETS):
    url = (f"{B}/{sport}/events/{eid}/odds/?apiKey={key}&regions=us,eu"
           f"&markets={markets}&oddsFormat=decimal")
    r = urllib.request.urlopen(url, timeout=30)
    data = json.loads(r.read())
    try:
        credits = int(r.headers.get("x-requests-last", "0"))
    except Exception:
        credits = 0
    return data, credits


def _canon(market_key):
    """Normalize an Odds API market key to our canonical market, or None to skip."""
    if market_key == "h2h":
        return "h2h"
    if "spreads" in market_key:
        return "spread"
    if "totals" in market_key:
        return "total"
    if market_key in ALL_PROPS:
        return market_key          # props keep their own key
    return None


def _is_ou(market):
    return market == "total" or market in ALL_PROPS


def _devig(pa, pb):
    ia, ib = 1.0 / pa, 1.0 / pb
    return ia / (ia + ib)


def _pin_index(pin_book):
    """(market, player, side, point) -> Pinnacle price. player=None for game markets."""
    idx = {}
    for m in pin_book.get("markets", []):
        cm = _canon(m["key"])
        if not cm:
            continue
        for o in m["outcomes"]:
            player = o.get("description")              # set for props, None otherwise
            pt = None if cm == "h2h" else round(o.get("point", 0.0), 1)
            idx[(cm, player, o["name"], pt)] = o["price"]
    return idx


def _fair(idx, market, player, side, point, home, away):
    pa = idx.get((market, player, side, point))
    if pa is None:
        return None
    if _is_ou(market):                                 # Over/Under complement, same player+line
        opp = "Under" if side == "Over" else "Over"
        pb = idx.get((market, player, opp, point))
    else:                                              # h2h / spread: other team
        other = away if side == home else home
        pb = idx.get((market, None, other, None if market == "h2h" else round(-point, 1)))
    if pb is None:
        return None
    return _devig(pa, pb)


def label(market, side, point, player=None):
    if market == "h2h":
        return f"{side} ML"
    if market == "spread":
        return f"{side} {point:+g}"
    if market == "total":
        return f"{side} {point:g}"
    return f"{player} {PROP_SHORT.get(market, market)} {side} {point:g}"   # prop


def event_ev_bets(ev, sport):
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
            market = _canon(m["key"])
            if not market:
                continue
            for o in m["outcomes"]:
                price = o["price"]
                if not (ODDS_MIN <= price <= ODDS_MAX):
                    continue
                player = o.get("description")
                point = None if market == "h2h" else round(o.get("point", 0.0), 1)
                fair = _fair(idx, market, player, o["name"], point, home, away)
                if fair is None:
                    continue
                ev_val = fair * price - 1
                if EV_MIN <= ev_val <= EV_MAX:
                    out.append({
                        "event_id": ev.get("id"), "sport": sport,
                        "commence_time": ev.get("commence_time"),
                        "home": home, "away": away,
                        "market": market, "side": o["name"], "point": point,
                        "player": player,
                        "label": label(market, o["name"], point, player),
                        "book": blabel, "odds": price, "fair": fair, "ev": ev_val,
                        "pin_price": idx.get((market, player, o["name"], point)),
                    })
    return out


def best_per_market(bets):
    """Best-EV bet per (event, market, player) — one ML + one spread + one total +
    one bet per (player, prop). Never both sides of the same market."""
    best = {}
    for b in bets:
        k = (b["event_id"], b["market"], b.get("player"))
        if k not in best or b["ev"] > best[k]["ev"]:
            best[k] = b
    return sorted(best.values(), key=lambda b: -b["ev"])


def grade(market, side, point, home, away, scores):
    """Settle a GAME-market bet given final team scores {team: int}."""
    try:
        if market == "total":
            tot = scores[home] + scores[away]
            if tot == point:
                return "push"
            return "win" if ((tot > point) == (side == "Over")) else "lose"
        other = away if side == home else home
        margin = scores[side] - scores[other]
        if market == "spread":
            margin += point
        if margin == 0:
            return "push"
        return "win" if margin > 0 else "lose"
    except Exception:
        return None


def grade_prop(side, point, stat):
    """Settle a PROP bet given the player's final stat value (from softbook_ev_stats)."""
    if stat is None:
        return None
    if stat == point:
        return "push"
    return "win" if ((stat > point) == (side == "Over")) else "lose"


def scan_sport(sport, key, per_event_budget=None):
    evs = list_events(sport, key)
    mk = markets_for(sport)
    bets, credits = [], 0
    for g in evs:
        if per_event_budget is not None and credits >= per_event_budget:
            break
        try:
            data, c = event_odds(sport, g["id"], key, mk)
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
    print(f"{sport}: {nev} events, {credits} credits, {len(bets)} +EV bets:\n")
    for b in bets:
        print(f"  {b['market']:<18} {b['label']:<28} {b['book']:<11} {b['odds']:.2f}  "
              f"fair {b['fair']*100:4.1f}%  EV {b['ev']*100:+.2f}%")
    if not bets:
        print("  (none in band right now)")
