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

def odds_bars(ev, odds):
    """The two prices a user shops books against, from a pick's stated EV and the
    decimal price it was quoted at. Recover the sharp fair win-probability
    (fair = (1+ev)/odds), then return DECIMAL odds:

      be  (본전) = 1/fair            — breakeven; the price where EV hits 0.
      min (최소) = (1+EV_MIN)/fair   — floor; the worst price still clearing the
                                       ROI>=1% policy bar. 어느 북이든 최소 위면 OK.

    Returns (min, be) or None when ev/odds are missing or the quoted price is not
    a valid decimal (>1).

    SINGLE SOURCE for every surface that shows these bars. EV_MIN is policy, and
    a surface that hard-codes its own copy will keep showing the old floor after
    the policy moves — the app used to mirror this formula and the constant in
    TypeScript, which is exactly the drift this function exists to prevent.
    Consumers: the Telegram alert (softbook_ev_recorder._min_odds_line) and the
    app card (softbook_app_api serves min_odds/be_odds on /picks).
    """
    if ev is None or odds is None or odds <= 1:
        return None
    fair = (1 + ev) / odds
    if fair <= 0:
        return None
    return (1 + EV_MIN) / fair, 1 / fair


GAME_MARKETS = ("h2h,spreads,totals,alternate_spreads,alternate_totals,"
                "team_totals,alternate_team_totals")
# Player-prop markets to scan, per sport — ONLY ones Pinnacle reliably quotes
# (so we have a sharp reference). Short labels for display + the stat to settle on.
PROP_MARKETS = {
    "baseball_mlb": ["pitcher_strikeouts", "batter_total_bases", "batter_home_runs",
                     "pitcher_outs", "pitcher_earned_runs", "pitcher_hits_allowed"],
    "basketball_nba": ["player_points", "player_rebounds", "player_assists"],
}
# Props with a REAL <key>_alternate variant on the Odds API. Request alternates
# only from this set: an unrecognized market key 422s the WHOLE event call, and
# e.g. pitcher_outs has no alternate (verified via /events/{id}/markets 6/12).
PROP_ALTS = {"pitcher_strikeouts", "batter_total_bases", "batter_home_runs",
             "player_points", "player_rebounds", "player_assists"}
PROP_SHORT = {"pitcher_strikeouts": "K", "batter_total_bases": "TB",
              "batter_home_runs": "HR", "pitcher_outs": "OUT",
              "pitcher_earned_runs": "ER", "pitcher_hits_allowed": "HA",
              "player_points": "PTS", "player_rebounds": "REB",
              "player_assists": "AST", "team_total": "TT"}
ALL_PROPS = {m for v in PROP_MARKETS.values() for m in v}


# Which confidence tier each market is bet at (user, 2026-08-05). Everything
# was High-only until now; ML moves to Med and leaves High entirely.
#
# ML by tier, first-entry paper ledger, Jun-Aug:
#     High  n=151  WR 41.1% vs BE 42.7%  -6.1%   Jun +15.9 / Jul -36.1 / Aug +51.1
#     Med   n=497  WR 50.9% vs BE 43.9%  +16.7%  Jun +11.9 / Jul +20.6 / Aug +23.3
#     Low   n=399  WR 44.6% vs BE 44.6%  +0.6%
# High sits BELOW its own breakeven and is the tier that produced the July
# collapse the 07-28 review called an "ML era break" — split by tier, the break
# is High's alone; Med is positive in all three months (2-prop z -1.06, +0.36).
# Med clears the gate at n=497 (Wilson LB 46.5% > BE 43.9%), survives dropping
# its best day and its best three days, and still clears with Wimbledon removed
# (n=423, LB 44.4% > BE 44.2%) — Wimbledon being the one chunk that genuinely
# cannot recur, at 43% of Med's PnL.
#
# Mechanism this fits, hypothesised 07-22: EV_MAX caps at 8%, so High (5-8%) is
# the top of the allowed range, where a soft price that far off Pinnacle more
# likely means OUR fair is stale than that we found an edge.
#
# Other markets are unchanged at High — this evidence is ML-specific and says
# nothing about spread/total.
BET_TIERS = {"h2h": frozenset({"Med"})}
BET_TIERS_DEFAULT = frozenset({"High"})


def bet_tiers(market):
    """Tiers at which `market` is bet. Single source for the tag, the alert
    gate, the app flag and the push worker."""
    return BET_TIERS.get(market, BET_TIERS_DEFAULT)


def alertable_tiers():
    """Union of every tier any market is bet at — the prefetch set for the SQL
    that pulls candidates. Per-market narrowing is is_bet's job."""
    out = set(BET_TIERS_DEFAULT)
    for t in BET_TIERS.values():
        out |= set(t)
    return frozenset(out)


def alertable_tiers_sql(col="tier"):
    """(sql, params) for an `IN` over every tier any market is bet at — the
    prefetch filter for queries that span markets. Rows STILL need the
    per-market check (`tier in bet_tiers(market)`); this only avoids pulling
    tiers nothing is bet at."""
    t = sorted(alertable_tiers())
    return f"{col} IN ({','.join('?' * len(t))})", t


def is_bet(first_entry, market, tier):
    """THE bet rule (user, 2026-06-16; tier added 2026-08-05): bet iff the pick
    was High from its earliest snapshot (first_entry), it is a game market —
    h2h / spread / total (props, incl. team_total, and late-crossers skip) —
    AND its confidence tier is one this market is bet at (see BET_TIERS).

    SINGLE SOURCE OF TRUTH for the Telegram tag, the app /picks 'bet' flag, and
    the push worker — change the policy here, once (the Telegram↔app divergence
    on 6/16 is why). No confirmed pick edge (6/15 re-measure ≈BE, Pikkit −4.6%);
    ≈BE bonus-clearing vehicle, bet small, no size increase until forward CLV
    confirms an edge.

    `tier` is required rather than defaulted: a default would let a caller that
    has not been updated keep the old policy silently, which is exactly the
    divergence this function exists to prevent.
    """
    return (bool(first_entry) and market not in PROP_SHORT
            and tier in bet_tiers(market))


# --- forward-observation watchlist (2026-07-22) --------------------------------
# NOT a bet-rule change. is_bet still returns True for these; they are annotated
# so the user can hold off manually while the cell re-measures forward.
EV_WATCH_MIN = 0.05
EV_WATCH_SINCE = "2026-07-22"        # rows placed on/after this date = the OOS test


def is_ev_watch(market, ev):
    """ML at stated EV >= EV_WATCH_MIN. ANALYSIS CELL ONLY since 2026-08-05 — no
    user-facing surface reads this any more.

    History: from 7/22 this cell carried a "hold" advisory on the Telegram tag,
    the app card and the push title, because High ML at EV>=5% had run 4-21
    (-60.8%). That review is closed. High ML is no longer bet at all (the whole
    tier went, not just this band), and re-measured on the cohort we DO bet the
    pathology does not reproduce: Med ML EV 5-8% is n=32, WR 50.0% vs BE 42.0%,
    +$22.23 on $89.95.

    n=32 proves nothing either way — Wilson LB 33.6% sits below breakeven, one
    day (6/23) is 54% of the PnL, and the 22-bet June block is +$1.50. The user's
    call, given that: the band is 6.4% of Med ML volume and is not negative, so
    it rides with the rest rather than carrying a warning nobody can act on.
    Reaching a decidable n here would take ~8 months at ~10 bets/month, so
    "keep waiting" was not actually on the table.

    softbook_ev_watch_eval still uses this to re-measure the band on demand.
    """
    return market == "h2h" and ev is not None and ev >= EV_WATCH_MIN


def market_first_entry_sides(db, event_id, market, player):
    """(seen, sides): `sides` = frozenset of the sides that were at this market's
    BET TIER (core.bet_tiers — High for most, Med for ML since 2026-08-05) at this
    (event, market, player)'s EARLIEST softbook_ev snapshot. A pick is first-entry iff
    ITS side is in `sides`. Side-aware: the OPPOSITE side that crosses into High only
    after the line moved is a LATE-CROSSER even though the market was High early — so it
    SKIPs. (Before 2026-06-17 this was keyed on (event,market,player) only, ignoring
    side, so an opposite-side post-steam crosser inherited the open-High side's
    first_entry=True and was mislabeled BET in Telegram, the app /picks flag, and push.)
    Deterministic if a scan stored multiple sides at one recorded_at (set membership at
    the min ts, not a nondeterministic LIMIT 1). `seen`=False only when NO snapshot
    exists — the recorder then emits no tag; app/push treat it as not-first-entry.
    Raises sqlite3.Error to the caller (each applies its own on-error policy). SINGLE
    SOURCE for the Telegram tag / app /picks flag / push; softbook_ev_measure
    .first_entry_map mirrors this exactly on in-memory rows."""
    opn = db.execute(
        "SELECT MIN(recorded_at) FROM softbook_ev"
        " WHERE event_id=? AND market=? AND COALESCE(player,'')=?",
        (event_id, market, player or "")).fetchone()
    if not opn or opn[0] is None:
        return (False, frozenset())
    tiers = sorted(bet_tiers(market))    # the tier THIS market is bet at, not literal High
    return (True, frozenset(s for (s,) in db.execute(
        "SELECT side FROM softbook_ev WHERE event_id=? AND market=?"
        " AND COALESCE(player,'')=? AND recorded_at=?"
        f" AND tier IN ({','.join('?' * len(tiers))})",
        (event_id, market, player or "", opn[0], *tiers))))


def markets_for(sport):
    # FanDuel/DraftKings/BetMGM list props ONLY under the _alternate key (verified
    # live 2026-06-12: batter_home_runs had no FD/DK/MGM rows, _alternate did) —
    # fetch both and _canon folds them back onto the base market.
    props = [k for m in PROP_MARKETS.get(sport, [])
             for k in ((m, m + "_alternate") if m in PROP_ALTS else (m,))]
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


# Non-regular-season NBA events the Odds API serves under DEDICATED sport keys
# (separate from basketball_nba). active_sports() auto-pulls EVERY "active" sport,
# so when the Odds API flips these on (Summer League every July, etc.) they flow
# straight into +EV alerts. Blocked because BOTH hold: (a) they are not on NY's
# approved wagering catalog -> not placeable on NY-geo DK/FD apps even though the
# national book catalog quotes them (verified 2026-07-07: OKC@UTA Summer League
# alerted but un-bettable in NY), and (b) the is_bet first-entry edge was validated
# on REGULAR-SEASON data only, so the EV number does not transfer to these thin,
# high-variance, roster-unknown slates. (2026-07-07)
SPORT_BLOCKLIST = {"basketball_nba_summer_league", "basketball_nba_preseason",
                   "basketball_nba_all_stars"}


def active_sports(key):
    try:
        d = json.loads(urllib.request.urlopen(f"{B}/?apiKey={key}", timeout=20).read())
        return [s["key"] for s in d if s.get("active")
                and not s.get("has_outrights") and s["key"] not in SPORT_BLOCKLIST]
    except Exception:
        return ["baseball_mlb", "basketball_wnba"]


def list_events(sport, key):
    try:
        games = json.loads(urllib.request.urlopen(
            f"{B}/{sport}/odds/?apiKey={key}&bookmakers={BOOKMAKERS}&markets=h2h&oddsFormat=decimal",
            timeout=25).read())
    except Exception:
        return []
    # Pinnacle is the fair source — without it event_ev_bets returns [] no matter
    # what, so don't spend a per-event odds call (MMA/boxing/tennis list many
    # events Pinnacle never quotes; the h2h list response already shows the books).
    return [g for g in games if soon(g.get("commence_time"))
            and any(b.get("key") == "pinnacle" for b in g.get("bookmakers", []))]


def event_odds(sport, eid, key, markets=GAME_MARKETS, links=False):
    url = (f"{B}/{sport}/events/{eid}/odds/?apiKey={key}&bookmakers={BOOKMAKERS}"
           f"&markets={markets}&oddsFormat=decimal")
    if links:
        # Provider bet-slip links + outcome SIDs for the app's tiered deep-link prefill.
        # Verified same credit cost as without (1 credit). Only request on the on-demand
        # /refresh path — never the bulk scan or price-move polling.
        url += "&includeLinks=true&includeSids=true"
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
    if "team_totals" in market_key:    # MUST precede the 'totals' substring check
        return "team_total"
    if "spreads" in market_key:
        return "spread"
    if "totals" in market_key:
        return "total"
    if market_key in ALL_PROPS:
        return market_key          # props keep their own key
    if market_key.endswith("_alternate") and market_key[:-10] in ALL_PROPS:
        return market_key[:-10]    # FD/DK/MGM quote props only on the alt ladder
    return None


def _is_ou(market):
    return market in ("total", "team_total") or market in ALL_PROPS


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
                # best price per BOOK per line: books repeat an outcome across
                # main+alt markets (and Caesars even within one) — counting the
                # dupes inflated n_books/n_ev (e.g. "5/5bk" that was really 4/4)
                by_book = lines.setdefault((market, o["name"], point, player), {})
                if o["price"] > by_book.get(blabel, 0.0):
                    by_book[blabel] = o["price"]
    out = []
    for (market, side, point, player), by_book in lines.items():
        quotes = list(by_book.items())
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
            # every NY book quoting THIS line, best-EV first (odds move fast —
            # the dashboard shows these so a stale best-book quote isn't a dead end)
            "books_all": [{"book": b2, "odds": p2, "ev": round(e2, 4)}
                          for b2, p2, e2 in sorted(evs, key=lambda x: -x[2])],
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
