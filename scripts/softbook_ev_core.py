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
            "williamhill_us": "Caesars",  # Odds API legacy key (William Hill US)
            "betrivers": "BetRivers", "fanatics": "Fanatics",
            "espnbet": "theScore",        # us2 region; ex-ESPN BET, rebranded 2025-12
            "ballybet": "BallyBet"}       # us2 region; no props quotes
# Explicit bookmakers= beats regions=us,eu: pulls the us2 NY books AND bills as
# 1 region (<=10 keys) instead of 2 — half the credits per event call.
BOOKMAKERS = ",".join(["pinnacle"] + list(NY_LEGAL))
ODDS_MIN, ODDS_MAX = 1.50, 4.00
EV_MIN, EV_MAX = 0.01, 0.08
MAX_HOURS = 72
QUIET_UTC = range(2, 9)     # 22:00-05:00 NYC sleep — no Telegram pushes in this window
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
            f"{B}/{sport}/odds/?apiKey={key}&bookmakers={BOOKMAKERS}&markets=h2h&oddsFormat=decimal",
            timeout=25).read())
    except Exception:
        return []
    return [g for g in games if soon(g.get("commence_time"))]


def event_odds(sport, eid, key, markets=GAME_MARKETS):
    url = (f"{B}/{sport}/events/{eid}/odds/?apiKey={key}&bookmakers={BOOKMAKERS}"
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
        if market == "h2h" and (market, None, "Draw", None) in idx:
            return None        # 3-way (soccer): a 2-way de-vig drops the Draw leg and
                               # over-states each side -> fake +EV. Skip until 3-way handled.
        other = away if side == home else home
        pb = idx.get((market, None, other, None if market == "h2h" else round(-point, 1)))
    if pb is None:
        return None
    return _devig(pa, pb)


def line_quotes(ev_data, market, side, point, player=None):
    """Fresh NY-book quotes + sharp fair for ONE exact line, from an event-odds
    payload (use point=None for h2h). Each book contributes its best price across
    main+alternate markets. Returns (fair_or_None, [(book_label, price), ...])."""
    home, away = ev_data.get("home_team"), ev_data.get("away_team")
    bk = {b["key"]: b for b in ev_data.get("bookmakers", [])}
    pin = bk.get("pinnacle")
    fair = _fair(_pin_index(pin), market, player, side, point, home, away) if pin else None
    target = None if market == "h2h" else round(point or 0.0, 1)
    quotes = []
    for bkey, blabel in NY_LEGAL.items():
        b = bk.get(bkey)
        if not b:
            continue
        best = None
        for m in b.get("markets", []):
            if _canon(m["key"]) != market:
                continue
            for o in m["outcomes"]:
                if o["name"] != side or (o.get("description") or None) != (player or None):
                    continue
                pt = None if market == "h2h" else round(o.get("point", 0.0), 1)
                if pt != target:
                    continue
                if best is None or o["price"] > best:
                    best = o["price"]
        if best is not None:
            quotes.append((blabel, best))
    return fair, quotes


def label(market, side, point, player=None):
    if market == "h2h":
        return f"{side} ML"
    if market == "spread":
        return f"{side} {point:+g}"
    if market == "total":
        return f"{side} {point:g}"
    return f"{player} {PROP_SHORT.get(market, market)} {side} {point:g}"   # prop


def _confidence(market, odds, ev_val, n_books, n_ev, ct):
    """0-100 reliability score + High/Med/Low tier. CONSENSUS dominates (how many
    soft books independently beat the sharp fair = real lag vs lone stale outlier);
    game markets > props; closer to start + sweet-spot odds/EV add. Thresholds are
    provisional — re-tune once CLV-by-tier data lands (~2wk)."""
    # CONSENSUS by ABSOLUTE count of books beating fair, minus a dissent penalty.
    # (n_ev/n_books fraction is misleading: 1/1 is "unconfirmable", not "unanimous".)
    consensus = max(0, 12 * min(n_ev, 4) - 4 * min(n_books - n_ev, 4))  # 0-48
    score = consensus
    score += 12 if market in ("h2h", "spread", "total") else 0        # game > prop
    h = hrs_to(ct)
    h = 99 if h is None else h
    score += 15 if h <= 3 else 10 if h <= 12 else 5 if h <= 24 else 0  # imminence
    if 2.0 <= odds <= 3.0:
        score += 8                                                    # sweet odds
    if 0.02 <= ev_val <= 0.04:
        score += 7                                                    # sweet EV
    score = round(score, 1)
    tier = "High" if score >= 55 else "Med" if score >= 33 else "Low"
    return score, tier


def event_ev_bets(ev, sport):
    home, away = ev.get("home_team"), ev.get("away_team")
    bk = {b["key"]: b for b in ev.get("bookmakers", [])}
    pin = bk.get("pinnacle")
    if not pin:
        return []
    idx = _pin_index(pin)
    # gather EVERY NY soft-book quote, grouped by line (for multi-book consensus)
    lines = {}
    for bkey, blabel in NY_LEGAL.items():
        b = bk.get(bkey)
        if not b:
            continue
        for m in b.get("markets", []):
            market = _canon(m["key"])
            if not market:
                continue
            for o in m["outcomes"]:
                player = o.get("description")
                point = None if market == "h2h" else round(o.get("point", 0.0), 1)
                lines.setdefault((market, o["name"], point, player), []).append((blabel, o["price"]))
    out = []
    for (market, side, point, player), quotes in lines.items():
        fair = _fair(idx, market, player, side, point, home, away)
        if fair is None:
            continue
        evs = [(bl, px, fair * px - 1) for bl, px in quotes]
        n_books = len(evs)                                   # NY books quoting this line
        n_ev = sum(1 for _, _, e in evs if e > 0)            # ...that beat the sharp fair
        cand = [(bl, px, e) for bl, px, e in evs
                if ODDS_MIN <= px <= ODDS_MAX and EV_MIN <= e <= EV_MAX]
        if not cand:
            continue
        bl, px, ev_val = max(cand, key=lambda x: x[2])       # best bettable book
        score, tier = _confidence(market, px, ev_val, n_books, n_ev, ev.get("commence_time"))
        out.append({
            "event_id": ev.get("id"), "sport": sport,
            "commence_time": ev.get("commence_time"),
            "home": home, "away": away,
            "market": market, "side": side, "point": point, "player": player,
            "label": label(market, side, point, player),
            "book": bl, "odds": px, "fair": fair, "ev": ev_val,
            "pin_price": idx.get((market, player, side, point)),
            "n_books": n_books, "n_ev": n_ev, "conf": score, "tier": tier,
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
    for b in sorted(bets, key=lambda x: -x["conf"]):
        print(f"  {b['tier']:<4} conf{b['conf']:>5}  {b['n_ev']}/{b['n_books']}bk  "
              f"{b['label']:<26} {b['book']:<11} {b['odds']:.2f}  EV {b['ev']*100:+.2f}%")
    if not bets:
        print("  (none in band right now)")
