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
    return sqlite3.connect(f"file:{EV_DB}?mode=ro", uri=True, timeout=15)


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
