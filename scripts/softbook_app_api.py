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


@app.get("/picks")
def get_picks(_=Depends(require_auth)):
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    pdb = adb.connect()
    try:
        picks = feed.compose(_latest_high_rows(), adb.pick_states(pdb),
                             _prefs(pdb), now)
    finally:
        pdb.close()
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
