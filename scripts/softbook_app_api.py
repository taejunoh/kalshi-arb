# scripts/softbook_app_api.py
#!/usr/bin/env python3
"""FastAPI service for the +EV companion app.

Read/write API over the existing softbook pipeline (softbook_ev.db High-pick
snapshots) + the app tables (softbook_paper.db). Exposed via Cloudflare Tunnel +
Access (zero inbound ports). Auth: long-lived bearer (APP_API_TOKEN) on every
request; when CF_ACCESS_TEAM_DOMAIN is set we also validate the Cloudflare Access
JWT (Cf-Access-Jwt-Assertion) so the origin enforces auth even if the edge policy
is wrong.

Run: uvicorn softbook_app_api:app --host 127.0.0.1 --port 8000
"""
import json
import os
import sqlite3
import sys
import time

from fastapi import Depends, FastAPI, Header, HTTPException

import softbook_ev_core as core
import softbook_app_db as adb
import softbook_app_feed as feed
import softbook_deeplink as deeplink
import softbook_scan as scan
import datetime
from pydantic import BaseModel, ConfigDict
import softbook_ev_recorder as rec

EV_DB = os.getenv("SOFTBOOK_EV_DB", "/home/ubuntu/kalshi-arb/data/softbook_ev.db")

app = FastAPI(title="softbook +EV companion API")


def _env(key):
    val = os.getenv(key)
    if val is not None:
        return val
    try:
        for line in open(os.getenv("ENV_PATH", "/home/ubuntu/kalshi-arb/.env")):
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def _validate_cf_jwt(assertion):
    """Validate a Cloudflare Access JWT against the team certs. Only enforced
    when CF_ACCESS_TEAM_DOMAIN is set (i.e. behind the tunnel in production)."""
    team = _env("CF_ACCESS_TEAM_DOMAIN")
    if not team:
        return                              # local/dev: edge not in front
    if not assertion:
        raise HTTPException(403, "missing Cf-Access-Jwt-Assertion")
    import jwt
    from jwt import PyJWKClient
    certs = f"https://{team}/cdn-cgi/access/certs"
    try:
        signing_key = PyJWKClient(certs).get_signing_key_from_jwt(assertion).key
        jwt.decode(assertion, signing_key, algorithms=["RS256"],
                   audience=_env("CF_ACCESS_AUD"), issuer=f"https://{team}")
    except Exception:
        raise HTTPException(403, "invalid Access JWT")


def require_auth(authorization: str = Header(default=""),
                cf_access_jwt_assertion: str = Header(default="")):
    token = _env("APP_API_TOKEN")
    sent = authorization[7:] if authorization.startswith("Bearer ") else ""
    if not token or sent != token:
        raise HTTPException(401, "bad bearer")
    _validate_cf_jwt(cf_access_jwt_assertion)


def _ev_conn():
    # Resolve at call time (env may be set after import; also keeps tests isolated).
    ev_db = os.getenv("SOFTBOOK_EV_DB", EV_DB)
    return sqlite3.connect(f"file:{ev_db}?mode=ro", uri=True, timeout=15)


def _latest_scan_at():
    """Newest recorded_at across ALL tiers = when the scanner last ran.

    The active/cooled split keys off this rather than off the newest High row,
    so a scan that produced no High pick anywhere still retires the previous
    scan's picks. See softbook_app_feed.compose for the failure it prevents.
    """
    c = _ev_conn()
    try:
        row = c.execute(
            "SELECT MAX(recorded_at) FROM softbook_ev WHERE"
            " datetime(recorded_at) >= datetime('now','-2 days')").fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        c.close()
    return row[0] if row else None


_WATCH_CACHE = {"at": 0.0, "val": None}
_WATCH_TTL_S = 300.0


def _alert_and_now(picks):
    """Annotate each pick with the snapshot it was ALERTED on and the CURRENT
    one, so the card can show "alerted +1.1%, now +0.4%" instead of a single
    number whose age is invisible.

    alert_* = this pick's EARLIEST High snapshot — the scan that triggered the
    Telegram/push. now_* = its LATEST snapshot at ANY tier, which is the part
    the High-only feed cannot see: once a pick drops out of High it stops
    producing High rows, so its last High row is not its current price.
    """
    if not picks:
        return picks
    c = _ev_conn()
    try:
        for p in picks:
            args = (p["event_id"], p["market"], p["side"],
                    p.get("player") or "", p.get("point"))
            where = ("event_id=? AND market=? AND side=? AND IFNULL(player,'')=?"
                     " AND IFNULL(point,-999)=IFNULL(?,-999)")
            try:
                tiers = sorted(core.bet_tiers(p["market"]))
                a = c.execute(
                    "SELECT recorded_at, snapshot_ev, best_soft_price, best_soft_book"
                    f" FROM softbook_ev WHERE {where}"
                    f" AND tier IN ({','.join('?' * len(tiers))})"
                    " ORDER BY recorded_at ASC LIMIT 1", (*args, *tiers)).fetchone()
                n = c.execute(
                    "SELECT recorded_at, snapshot_ev, best_soft_price,"
                    f" best_soft_book, tier FROM softbook_ev WHERE {where}"
                    " ORDER BY recorded_at DESC LIMIT 1", args).fetchone()
            except sqlite3.OperationalError:
                a = n = None
            if a:
                p["alerted_at"], p["alert_ev"] = a[0], a[1]
                p["alert_odds"], p["alert_book"] = a[2], a[3]
            if n:
                p["now_at"], p["now_ev"] = n[0], n[1]
                p["now_odds"], p["now_book"], p["now_tier"] = n[2], n[3], n[4]
            # Why a cooled pick is cooled. Measured 2026-08-05: of 17 cooled
            # picks, 9 had never actually cooled — their last snapshot was
            # still High, they simply stopped appearing (book pulled the
            # market, or the line moved and became a different pick). Lumping
            # those in with genuinely re-priced ones told the user "this went
            # cold" about a bet whose last known EV was unchanged.
            if p.get("status") == "cooled":
                # "faded" = re-priced out of the tier this market is bet at;
                # "gone" = no later snapshot at all. bet_tiers, not a literal
                # 'High': an ML pick now sitting at High has left Med, which is
                # a fade, and one sitting at Med has not.
                nt = p.get("now_tier")
                p["cooled_reason"] = ("gone" if nt is None
                                      else "faded" if nt not in core.bet_tiers(p["market"])
                                      else "gone")
    finally:
        c.close()
    return picks


def _latest_high_rows():
    """High snapshots from the most recent ~2 days of scans (enough to classify
    cooled vs active without scanning the whole table)."""
    cols = ("recorded_at, sport, event_id, commence_time, home, away, market,"
            " side, point, player, best_soft_book, best_soft_price, snapshot_ev,"
            " n_books, conf, tier")
    tier_sql, tier_params = core.alertable_tiers_sql()
    c = _ev_conn()
    try:
        rows = c.execute(
            f"SELECT {cols} FROM softbook_ev WHERE {tier_sql} AND"
            " datetime(recorded_at) >= datetime('now','-2 days')",
            tier_params).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        c.close()
    keys = cols.replace(" ", "").split(",")
    out = []
    for r in rows:
        d = dict(zip(keys, r))
        if d["tier"] not in core.bet_tiers(d["market"]):
            continue                 # not the tier this market is bet at
        if not core.is_confirmable(d["n_books"]):
            continue                 # single-book market — see core.is_confirmable
        d["label"] = core.label(d["market"], d["side"],
                                 None if d["market"] == "h2h" else d["point"],
                                 d["player"] or None)
        out.append(d)
    return out


def _prefs(pdb):
    row = pdb.execute(
        "SELECT min_ev, sports_json, muted_books_json, sort FROM filter_prefs"
        " WHERE id=1").fetchone()
    if not row:
        return None
    return dict(zip(("min_ev", "sports_json", "muted_books_json", "sort"), row))


def _enrich_first_entry(picks):
    """Set p['first_entry'] (THIS SIDE was High at the market's earliest snapshot —
    side-aware, so the opposite side after a steam is a late-crosser, not a first-entry) and
    p['bet'] = the BET verdict via core.is_bet = FIRST-ENTRY GAME ONLY (h2h/spread/
    total & first-entry; props and late-crossers skip). Single source of truth shared
    with the Telegram tag and the push worker — see core.is_bet. No confirmed pick
    edge (6/15 ≈BE); ≈BE bonus-clearing bets, not a money-maker."""
    if not picks:
        return picks
    c = _ev_conn()
    try:
        cache = {}                                       # market -> (seen, High sides at open)
        for p in picks:
            key = (p["event_id"], p["market"], p.get("player") or "")
            if key not in cache:
                cache[key] = core.market_first_entry_sides(c, *key)
            # side-aware: the opposite side that crossed High after a steam is a
            # late-crosser, NOT this market's first-entry (single source: core).
            p["first_entry"] = p["side"] in cache[key][1]
            # should_bet = the BET verdict (first-entry GAME only); named distinctly
            # from the pick lifecycle status 'bet' (user-marked-as-placed).
            # .get: a row missing its tier degrades to SKIP rather than raising —
            # same policy as a missing snapshot, never mislabel on a transient miss.
            p["should_bet"] = core.is_bet(p["first_entry"], p["market"], p.get("tier"))
            # 최소/본전 bars, served rather than recomputed client-side: EV_MIN is
            # policy and the app had been mirroring both it and the formula in TS,
            # so a server-side change would have left the card showing a stale
            # floor with nothing to catch it. Decimal odds; the app formats them
            # with its own americanOdds. None when ev/price is unknown -> the app
            # hides the line. Same core.odds_bars the Telegram alert uses.
            bars = core.odds_bars(p.get("ev"), p.get("odds"))
            p["min_odds"], p["be_odds"] = bars if bars else (None, None)
    finally:
        c.close()
    return picks


EXPIRED_WINDOW_H = 24   # recently-commenced expired picks stay findable in-app (a
                        # pushed pick you reacted to after game start); older expired
                        # picks are stale noise and are dropped from the feed.


def _within_hours(ct, now_dt, hours):
    """True if commence_time ct is within `hours` before now_dt. On any parse
    ambiguity, return True — never hide a pick the user may be looking for."""
    dt = feed._parse_dt(ct)
    if dt is None or now_dt is None:
        return True
    try:
        return 0 <= (now_dt - dt).total_seconds() <= hours * 3600
    except TypeError:
        return True


@app.get("/picks")
def get_picks(_=Depends(require_auth)):
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    pdb = adb.connect()
    try:
        picks = feed.compose(_latest_high_rows(), adb.pick_states(pdb),
                             _prefs(pdb), now, adb.active_mute_rules(pdb),
                             latest_scan=_latest_scan_at())
    finally:
        pdb.close()
    _enrich_first_entry(picks)
    # SKIP picks (props, late-crossers) leave the app feed entirely (user,
    # 2026-08-05). Telegram has suppressed them since 7/22; the app was the last
    # surface still showing a card that says "do not bet this". They stay
    # recorded — the ledger, the CLV trail and /picks/{id} are untouched, so an
    # existing deep link still resolves; only the feed hides them.
    # ...but only from the FEED. A pick the user marked bet or dismissed is a
    # record of what they did, and our verdict does not get to erase it — it
    # keeps its place in counts and in MyBets.
    picks = [p for p in picks if p.get("should_bet") is not False
             or p.get("status") in ("bet", "dismissed")]
    _alert_and_now(picks)
    active = [p for p in picks if p["status"] == "active"]
    counts = {}
    for p in picks:
        counts[p["status"]] = counts.get(p["status"], 0) + 1
    # Expired = game already started. Kept (windowed to recent commence, newest
    # first) so an alert you reacted to late stays findable — the feed list used to
    # drop these entirely, leaving only a count. counts['expired'] is the full
    # classification count; this list is the bounded display set.
    now_dt = feed._parse_dt(now)
    expired = sorted(
        (p for p in picks if p["status"] == "expired"
         and _within_hours(p["commence_time"], now_dt, EXPIRED_WINDOW_H)),
        key=lambda p: p.get("commence_time") or "", reverse=True)
    return {"picks": active, "counts": counts,
            "cooled": [p for p in picks if p["status"] == "cooled"],
            "expired": expired}


@app.get("/picks/{pick_id}")
def get_pick(pick_id: str, _=Depends(require_auth)):
    now = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc).isoformat()
    pdb = adb.connect()
    try:
        all_picks = feed.compose(_latest_high_rows(), adb.pick_states(pdb),
                                 _prefs(pdb), now)
    finally:
        pdb.close()
    _enrich_first_entry(all_picks)
    for p in all_picks:
        if p["id"] == pick_id:
            return p
    raise HTTPException(404, "pick not found")


# Odds API book key (Odds API uses keys; NY_LEGAL maps key->label).
_LABEL_TO_KEY = {v: k for k, v in core.NY_LEGAL.items()}


def _markets_for_pick(market):
    """Scope the single-event refetch to just this pick's markets (cheaper)."""
    if market in core.ALL_PROPS:
        keys = [market] + ([market + "_alternate"] if market in core.PROP_ALTS else [])
        return ",".join(keys)
    return core.GAME_MARKETS


def _find_sport(pdb_unused, event_id):
    c = _ev_conn()
    try:
        row = c.execute("SELECT sport FROM softbook_ev WHERE event_id=? LIMIT 1",
                        (event_id,)).fetchone()
    finally:
        c.close()
    return row[0] if row else None


@app.get("/picks/{pick_id}/refresh")
def refresh_pick(pick_id: str, _=Depends(require_auth)):
    eid, market, side, point, player = feed.decode_id(pick_id)
    sport = _find_sport(None, eid)
    key = core.read_key()
    if not (sport and key):
        raise HTTPException(503, "no sport/key")
    data, credits = core.event_odds(sport, eid, key, _markets_for_pick(market), links=True)
    fair, quotes = core.line_quotes(
        data, market, side, None if market == "h2h" else point, player or None)
    home, away = data.get("home_team"), data.get("away_team")
    books = []
    for label_, px in sorted(quotes, key=lambda q: -q[1]):
        bkey = _LABEL_TO_KEY.get(label_, label_)
        links = deeplink.extract_links(data, bkey, market, side,
                                       None if market == "h2h" else point,
                                       player or None)
        # deeplink.build reads these structured fields to make copy_text a SEARCH-ready entity
        # (name to paste into the book's search box), not a human-readable bet line.
        pick = {"book": label_, "odds": px, "market": market, "side": side,
                "point": None if market == "h2h" else point, "player": player or None,
                "home": home, "away": away}
        books.append({"book": label_, "odds": px,
                      "ev": (fair * px - 1) if fair else None,
                      "deeplink": deeplink.build(bkey, links, pick)})
    return {"fair": fair, "books": books, "credits": credits}


class ScanIn(BaseModel):
    image_base64: str
    media_type: str = "image/jpeg"


def _telegram_bet_done(bet):
    """Look up the alert's message_id in the recorder db (read-only) and stamp
    it. Delegates to rec.mark_bet_done — the recorder owns Telegram and the
    high_alerted schema; a second implementation here would drift."""
    import softbook_ev_recorder as rec
    c = sqlite3.connect(f"file:{rec.OUT}?mode=ro", uri=True, timeout=5)
    try:
        odds = bet.fill_odds if getattr(bet, "fill_odds", None) else bet.rec_odds
        note = " ".join(x for x in (
            bet.book, rec._american(odds) if odds else None) if x)
        rec.mark_bet_done(c, bet.event_id, bet.market, bet.side,
                          bet.point or 0, bet.player or "", note or "앱에서 표시")
    finally:
        c.close()


class BetIn(BaseModel):
    event_id: str
    sport: str | None = None
    home: str | None = None
    away: str | None = None
    commence_time: str | None = None
    market: str
    side: str
    point: float | None = 0
    player: str | None = ""
    label: str | None = None
    book: str | None = None
    rec_odds: float | None = None
    fill_odds: float | None = None
    stake: float | None = None
    source: str = "one_tap"


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@app.post("/bets")
def post_bet(bet: BetIn, _=Depends(require_auth)):
    pdb = adb.connect()
    try:
        d = bet.dict()
        d["placed_at"] = _now()
        bid = adb.insert_app_bet(pdb, d)
        adb.set_pick_state(pdb,
            (bet.event_id, bet.market, bet.side, bet.point or 0, bet.player or ""),
            "bet", _now())
    finally:
        pdb.close()
    # Stamp the original Telegram alert "✅ 베팅 완료" so the message in chat
    # history stops reading as an open recommendation (2026-08-06: a stale
    # message led to the same pick being bet twice, 50 minutes apart, at a
    # worse price). Best-effort: a Telegram or ev-db hiccup must never fail
    # the bet recording the user just did.
    try:
        _telegram_bet_done(bet)
    except Exception:
        pass
    return {"id": bid}


class BetPatch(BaseModel):
    """The only three fields the user typed, so the only three they may retype.

    extra="forbid" makes anything else a 422 rather than a silent no-op:
    event/market/side/point/player describe a DIFFERENT bet (void this one and
    record a new one instead), rec_odds is the system's observation at
    recommendation time and feeds CLV, and settled/won/pnl belong to the
    settle worker."""
    model_config = ConfigDict(extra="forbid")
    book: str | None = None
    stake: float | None = None
    fill_odds: float | None = None


@app.patch("/bets/{bet_id}")
def patch_bet(bet_id: int, body: BetPatch, _=Depends(require_auth)):
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(422, "no editable fields supplied")
    # exclude_unset only tells "field omitted" from "field explicitly set" — it
    # does NOT stop an explicit null from getting through. stake/fill_odds feed
    # settle's pnl = stake * (fill_odds - 1) with no None guard, so a nulled
    # one poisons that row and (since settle commits once after its loop) takes
    # the whole batch down with it. There's also no sense in which a placed bet
    # with no stake is a record of anything, so this isn't just a worker
    # crash-guard — it's an invalid edit. book has neither problem: it's
    # descriptive metadata and POST /bets already allows a bookless bet.
    if "stake" in fields and fields["stake"] is None:
        raise HTTPException(422, "stake cannot be cleared")
    if "fill_odds" in fields and fields["fill_odds"] is None:
        raise HTTPException(422, "fill_odds cannot be cleared")
    pdb = adb.connect()
    try:
        row = adb.get_app_bet(pdb, bet_id)
        if row is None:
            raise HTTPException(404, "no such bet")
        if row["settled"]:
            # Editing the stake now would contradict a pnl already computed from
            # it. The honest move in that situation is /unmark.
            raise HTTPException(409, "settled bet — 수정 불가, 표시 해제를 쓰세요")
        if row["not_placed"]:
            raise HTTPException(409, "not-placed bet — 먼저 표시를 복원하세요")
        adb.update_app_bet(pdb, bet_id, fields)
    finally:
        pdb.close()
    return {"ok": True}


def _pick_of(row):
    """The pick_state key for an app_bet row."""
    return (row["event_id"], row["market"], row["side"],
            row["point"] or 0, row["player"] or "")


@app.post("/bets/{bet_id}/unmark")
def unmark_bet(bet_id: int, _=Depends(require_auth)):
    """"이 베팅은 성립하지 않았다" — a mis-tap, or the book rejected the slip.

    The row is NOT deleted: app_bet rows are bet records (LEDGER RULE in
    softbook_app_db). It gets not_placed=1, which the settle worker, the push
    worker, the live grader and /mybets all filter out, and its pick_state 'bet'
    override is dropped so the pick returns to the live feed.

    Allowed on a settled bet too: if a mis-marked bet already auto-settled, that
    day's PnL is ALREADY wrong and this is the only way to fix it. The response
    reports the change so the app can say which day moves by how much rather
    than letting a past number shift in silence.

    Reversible: DELETE the same path."""
    pdb = adb.connect()
    try:
        row = adb.get_app_bet(pdb, bet_id)
        if row is None:
            raise HTTPException(404, "no such bet")
        if not row["not_placed"]:
            adb.set_not_placed(pdb, bet_id, True, _now())
            # The pick_state 'bet' override speaks for the PICK, not for this
            # row, so it may only be dropped once no owned bet on that pick
            # survives. Two rows on one pick is reachable (one-tap the pick from
            # the feed, then also scan the paper slip); returning the pick to the
            # feed on the first unmark would offer a one-tap re-bet while the
            # other bet is still open — the 2026-08-06 double-bet.
            if adb.live_bets_for_pick(pdb, _pick_of(row), exclude_id=bet_id) == 0:
                adb.clear_bet_state(pdb, _pick_of(row))
    finally:
        pdb.close()
    # `row` is the state BEFORE this call, so a repeat unmark correctly reports
    # no change. Only a settled bet moves the calendar.
    if row["not_placed"] or not row["settled"]:
        return {"ok": True, "pnl_delta": None, "settled_day": None}
    return {"ok": True,
            "pnl_delta": -(row["pnl"] or 0),
            "settled_day": (row["settled_at"] or "")[:10] or None}


@app.delete("/bets/{bet_id}/unmark")
def remark_bet(bet_id: int, _=Depends(require_auth)):
    """Undo an unmark: the bet counts again and the pick re-hides from the feed."""
    pdb = adb.connect()
    try:
        row = adb.get_app_bet(pdb, bet_id)
        if row is None:
            raise HTTPException(404, "no such bet")
        if row["not_placed"]:
            adb.set_not_placed(pdb, bet_id, False, None)
            adb.set_pick_state(pdb, _pick_of(row), "bet", _now())
    finally:
        pdb.close()
    return {"ok": True}


@app.post("/bets/scan")
def scan_bet(body: ScanIn, _=Depends(require_auth)):
    """Parse a bet-slip image into a BetIn-shaped draft for the confirm sheet.
    Does NOT write app_bet — the app reviews, then POSTs /bets with source='scan'."""
    try:
        draft = scan.parse_bet_slip(body.image_base64, body.media_type)
    except Exception as e:
        raise HTTPException(422, f"scan parse failed: {e}")
    edb = _ev_conn()
    try:
        eid = scan.match_event(edb, draft)
    finally:
        edb.close()
    bet = {
        "event_id": eid,
        "sport": draft.get("sport"),
        "home": draft.get("home"),
        "away": draft.get("away"),
        "commence_time": None,
        "market": draft.get("market"),
        "side": draft.get("side"),
        "point": draft.get("point"),
        "player": draft.get("player"),
        "label": draft.get("label"),
        "book": draft.get("book"),
        "rec_odds": None,
        "fill_odds": draft.get("fill_odds"),
        "stake": draft.get("stake"),
        "source": "scan",
    }
    return {"bet": bet, "matched": eid is not None, "notes": draft.get("notes", "")}


def _auto_dismiss_opposite(pdb, eid, market, side, point, player, ts):
    """Reverse-middle: a same-game opposite-direction High that can never both
    win as this pick -> auto-dismiss it too (reuse recorder._no_both_win).

    `ts` is the SAME timestamp as the user's own dismiss row, on purpose: it is
    what lets undismiss find and revert this whole group. Nothing else marks a
    row as auto-dismissed, so the shared timestamp is the group id."""
    this = {"market": market, "side": side, "point": point or 0,
            "player": player or "", "home": None, "away": None}
    for r in _latest_high_rows():
        if r["event_id"] != eid:
            continue
        other = {"market": r["market"], "side": r["side"], "point": r["point"] or 0,
                 "player": r["player"] or "", "home": r["home"], "away": r["away"]}
        this2 = {**this, "home": r["home"], "away": r["away"]}
        if rec._no_both_win(this2, other):
            adb.set_pick_state(pdb, (eid, r["market"], r["side"],
                                     r["point"] or 0, r["player"] or ""),
                               "dismissed", ts)


@app.post("/picks/{pick_id}/dismiss")
def dismiss_pick(pick_id: str, _=Depends(require_auth)):
    eid, market, side, point, player = feed.decode_id(pick_id)
    ts = _now()                          # one ts for the whole dismiss group
    pdb = adb.connect()
    try:
        adb.set_pick_state(pdb, (eid, market, side, point, player),
                           "dismissed", ts)
        _auto_dismiss_opposite(pdb, eid, market, side, point, player, ts)
    finally:
        pdb.close()
    return {"ok": True}


@app.post("/picks/{pick_id}/undismiss")
def undismiss_pick(pick_id: str, _=Depends(require_auth)):
    """Revert a dismiss — the 5-second 실행취소 in the app. The 관심없음 button
    sits next to 베팅, so mis-taps happen; undo beats a confirm dialog because
    the honest case (a real dismiss) costs nothing extra.

    Reverts the whole GROUP: the user's row plus any reverse-middle opposites
    _auto_dismiss_opposite added in the same request, found by the shared
    timestamp. Only state='dismissed' rows are touched — a 'bet' row is a record
    of what the user did and no undo path may delete it."""
    eid, market, side, point, player = feed.decode_id(pick_id)
    pdb = adb.connect()
    try:
        n = adb.undismiss_group(pdb, (eid, market, side, point, player))
    finally:
        pdb.close()
    if not n:
        raise HTTPException(404, "no dismissed state to undo")
    return {"ok": True, "reverted": n}


class SettingsIn(BaseModel):
    min_ev: float = 0.0
    sports: list[str] | None = None
    muted_books: list[str] | None = None
    sort: str = "ev"


class TokenIn(BaseModel):
    token: str


class RuleIn(BaseModel):
    field: str
    value: str


_RULE_FIELDS = ("player", "market", "sport", "event")


@app.get("/rules")
def get_rules(_=Depends(require_auth)):
    pdb = adb.connect()
    try:
        return {"rules": adb.list_mute_rules(pdb)}
    finally:
        pdb.close()


@app.post("/rules")
def add_rule(body: RuleIn, _=Depends(require_auth)):
    if body.field not in _RULE_FIELDS:
        raise HTTPException(400, f"field must be one of {_RULE_FIELDS}")
    pdb = adb.connect()
    try:
        rid = adb.add_mute_rule(pdb, body.field, body.value, _now())
    finally:
        pdb.close()
    return {"id": rid}


@app.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, _=Depends(require_auth)):
    pdb = adb.connect()
    try:
        adb.delete_mute_rule(pdb, rule_id)
    finally:
        pdb.close()
    return {"ok": True}


_MYBET_COLS = ("id, event_id, sport, home, away, commence_time, market, side,"
               " point, player, label, book, fill_odds, stake, live_status,"
               " live_progress, settled, won, pnl, settled_at, not_placed,"
               " not_placed_at")

# 90 days: the app screen is a working record, not an archive — the dashboard
# History owns the long tail. Keeps the payload bounded as app_bet grows.
_SETTLED_WINDOW_D = 90


def _mybet_rows(pdb, tail, params=()):
    keys = [c.strip() for c in _MYBET_COLS.split(",")]
    rows = [dict(zip(keys, r)) for r in
            pdb.execute(f"SELECT {_MYBET_COLS} FROM app_bet WHERE {tail}", params)]
    for o in rows:
        if o.get("live_progress"):
            try:
                o["live_progress"] = json.loads(o["live_progress"])
            except Exception:
                pass
    return rows


@app.get("/mybets")
def get_mybets(_=Depends(require_auth)):
    """The bet ledger, split by what the user can do with each row: open (waiting),
    settled (done), not_placed (disowned — kept visible because the row is never
    deleted). not_placed is excluded from the calendar: its PnL is not real."""
    pdb = adb.connect()
    try:
        cal = {}
        for day, pnl in pdb.execute(
                "SELECT substr(settled_at,1,10) d, COALESCE(sum(pnl),0)"
                " FROM app_bet WHERE settled=1 AND settled_at IS NOT NULL"
                " AND not_placed=0 GROUP BY d"):
            cal[day] = round(pnl, 2)
        open_rows = _mybet_rows(
            pdb, "settled=0 AND not_placed=0 ORDER BY commence_time")
        settled = _mybet_rows(
            pdb, "settled=1 AND not_placed=0 AND settled_at IS NOT NULL"
                 " AND datetime(settled_at) >= datetime('now', ?)"
                 " ORDER BY settled_at DESC", (f"-{_SETTLED_WINDOW_D} days",))
        not_placed = _mybet_rows(pdb, "not_placed=1 ORDER BY not_placed_at DESC")
    finally:
        pdb.close()
    return {"calendar": cal, "open": open_rows,
            "settled": settled, "not_placed": not_placed}


@app.get("/settings")
def get_settings(_=Depends(require_auth)):
    pdb = adb.connect()
    try:
        p = _prefs(pdb)
    finally:
        pdb.close()
    if not p:
        return {"min_ev": 0.0, "sports": None, "muted_books": None, "sort": "ev"}
    return {"min_ev": p["min_ev"],
            "sports": json.loads(p["sports_json"]) if p["sports_json"] else None,
            "muted_books": json.loads(p["muted_books_json"]) if p["muted_books_json"] else None,
            "sort": p["sort"]}


@app.put("/settings")
def put_settings(s: SettingsIn, _=Depends(require_auth)):
    pdb = adb.connect()
    try:
        pdb.execute(
            "INSERT INTO filter_prefs(id,min_ev,sports_json,muted_books_json,sort)"
            " VALUES (1,?,?,?,?) ON CONFLICT(id) DO UPDATE SET"
            " min_ev=excluded.min_ev, sports_json=excluded.sports_json,"
            " muted_books_json=excluded.muted_books_json, sort=excluded.sort",
            (s.min_ev, json.dumps(s.sports) if s.sports else None,
             json.dumps(s.muted_books) if s.muted_books else None, s.sort))
        pdb.commit()
    finally:
        pdb.close()
    return {"ok": True}


@app.post("/push/register")
def push_register(t: TokenIn, _=Depends(require_auth)):
    pdb = adb.connect()
    try:
        pdb.execute(
            "INSERT INTO push_token(token,updated_at,active) VALUES (?,?,1)"
            " ON CONFLICT(token) DO UPDATE SET updated_at=excluded.updated_at,"
            " active=1", (t.token, _now()))
        pdb.commit()
    finally:
        pdb.close()
    return {"ok": True}
