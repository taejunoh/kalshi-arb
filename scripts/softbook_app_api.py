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

from fastapi import Depends, FastAPI, Header, HTTPException

import softbook_ev_core as core
import softbook_app_db as adb
import softbook_app_feed as feed
import softbook_deeplink as deeplink
import softbook_scan as scan
import datetime
from pydantic import BaseModel
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


def _latest_high_rows():
    """High snapshots from the most recent ~2 days of scans (enough to classify
    cooled vs active without scanning the whole table)."""
    cols = ("recorded_at, sport, event_id, commence_time, home, away, market,"
            " side, point, player, best_soft_book, best_soft_price, snapshot_ev,"
            " n_books, conf")
    c = _ev_conn()
    try:
        rows = c.execute(
            f"SELECT {cols} FROM softbook_ev WHERE tier='High' AND"
            " datetime(recorded_at) >= datetime('now','-2 days')").fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        c.close()
    keys = cols.replace(" ", "").split(",")
    out = []
    for r in rows:
        d = dict(zip(keys, r))
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
    """Set p['first_entry']: True when this (event,market,player)'s EARLIEST
    softbook_ev snapshot was already tier='High' (a standing soft-book mispricing
    grabbed on sight). Mirrors softbook_ev_recorder._first_entry_note exactly — the
    dashboard's First-entry High ledger settled +16.6% ROI on this subset, while
    late-crossers (drifted into High only after the line moved) settled ~0 (dilution)."""
    if not picks:
        return picks
    c = _ev_conn()
    try:
        cache = {}
        for p in picks:
            key = (p["event_id"], p["market"], p.get("player") or "")
            if key not in cache:
                row = c.execute(
                    "SELECT tier FROM softbook_ev WHERE event_id=? AND market=?"
                    " AND COALESCE(player,'')=? ORDER BY recorded_at LIMIT 1",
                    key).fetchone()
                cache[key] = bool(row and row[0] == "High")
            p["first_entry"] = cache[key]
    finally:
        c.close()
    return picks


@app.get("/picks")
def get_picks(_=Depends(require_auth)):
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    pdb = adb.connect()
    try:
        picks = feed.compose(_latest_high_rows(), adb.pick_states(pdb),
                             _prefs(pdb), now, adb.active_mute_rules(pdb))
    finally:
        pdb.close()
    _enrich_first_entry(picks)
    active = [p for p in picks if p["status"] == "active"]
    counts = {}
    for p in picks:
        counts[p["status"]] = counts.get(p["status"], 0) + 1
    return {"picks": active, "counts": counts,
            "cooled": [p for p in picks if p["status"] == "cooled"]}


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
    data, credits = core.event_odds(sport, eid, key, _markets_for_pick(market))
    fair, quotes = core.line_quotes(
        data, market, side, None if market == "h2h" else point, player or None)
    books = []
    for label_, px in sorted(quotes, key=lambda q: -q[1]):
        bkey = _LABEL_TO_KEY.get(label_, label_)
        links = deeplink.extract_links(data, bkey, market, side,
                                       None if market == "h2h" else point,
                                       player or None)
        pick = {"book": label_, "label": core.label(
            market, side, None if market == "h2h" else point, player or None),
            "odds": px}
        books.append({"book": label_, "odds": px,
                      "ev": (fair * px - 1) if fair else None,
                      "deeplink": deeplink.build(bkey, links, pick)})
    return {"fair": fair, "books": books, "credits": credits}


class ScanIn(BaseModel):
    image_base64: str
    media_type: str = "image/jpeg"


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
    return {"id": bid}


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


def _auto_dismiss_opposite(pdb, eid, market, side, point, player):
    """Reverse-middle: a same-game opposite-direction High that can never both
    win as this pick -> auto-dismiss it too (reuse recorder._no_both_win)."""
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
                               "dismissed", _now())


@app.post("/picks/{pick_id}/dismiss")
def dismiss_pick(pick_id: str, _=Depends(require_auth)):
    eid, market, side, point, player = feed.decode_id(pick_id)
    pdb = adb.connect()
    try:
        adb.set_pick_state(pdb, (eid, market, side, point, player),
                           "dismissed", _now())
        _auto_dismiss_opposite(pdb, eid, market, side, point, player)
    finally:
        pdb.close()
    return {"ok": True}


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


@app.get("/mybets")
def get_mybets(_=Depends(require_auth)):
    pdb = adb.connect()
    try:
        cal = {}
        for day, pnl in pdb.execute(
                "SELECT substr(settled_at,1,10) d, COALESCE(sum(pnl),0)"
                " FROM app_bet WHERE settled=1 AND settled_at IS NOT NULL"
                " GROUP BY d"):
            cal[day] = round(pnl, 2)
        cols = ("id, event_id, sport, home, away, commence_time, market, side,"
                " point, player, label, book, fill_odds, stake, live_status,"
                " live_progress")
        open_rows = [dict(zip(cols.replace(" ", "").split(","), r)) for r in
                     pdb.execute(f"SELECT {cols} FROM app_bet WHERE settled=0"
                                 " ORDER BY commence_time")]
        for o in open_rows:
            if o.get("live_progress"):
                try:
                    o["live_progress"] = json.loads(o["live_progress"])
                except Exception:
                    pass
    finally:
        pdb.close()
    return {"calendar": cal, "open": open_rows}


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
