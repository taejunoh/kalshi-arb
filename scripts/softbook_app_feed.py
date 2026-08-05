# scripts/softbook_app_feed.py
#!/usr/bin/env python3
"""Feed composition for the +EV companion app.

Active feed = the LATEST softbook_ev scan's High picks, minus pick_state
(bet/dismissed), classified active/cooled/expired, then filtered by filter_prefs.
Pure function: takes already-fetched High snapshot rows (list of dicts), the
pick_state map, the prefs row, and `now`. No DB or network.
"""
import json
from datetime import datetime, timezone


def _key(p):
    return (p["event_id"], p["market"], p["side"],
            float(p.get("point") or 0), p.get("player") or "")


def _parse_dt(s):
    """Parse an ISO-8601 string (with Z or +HH:MM offset) to an aware datetime."""
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _expired(ct, now):
    """Return True if commence_time <= now (game already started)."""
    ct_dt = _parse_dt(ct)
    now_dt = _parse_dt(now)
    if ct_dt is None or now_dt is None:
        return False
    return ct_dt <= now_dt


_MUTE_COL = {"player": "player", "market": "market", "sport": "sport", "event": "event_id"}


def _is_muted_row(row, rules):
    """True if the pick row matches any (field, value) mute rule (case-insensitive)."""
    for field, value in rules:
        col = _MUTE_COL.get(field)
        v = (value or "").strip().lower()
        if not col or not v:
            continue
        rv = row.get(col)
        if isinstance(rv, str) and rv.strip().lower() == v:
            return True
    return False


def compose(rows, states, prefs, now, mute_rules=(), latest_scan=None):
    """rows: High-tier softbook_ev snapshots (any scans). states: {key->state}.
    prefs: filter_prefs row dict or None. Returns picks with a 'status' field.

    `latest_scan` is the newest recorded_at across ALL tiers — i.e. when the
    scanner last ran. A pick is active when it was still High in that scan.

    Passing it matters. Deriving the reference from the High rows alone (the
    old behaviour, kept as the fallback) means a scan that produces no High
    pick anywhere does not advance the reference, so every pick from the last
    High-producing scan keeps reporting "active" even after later scans
    re-priced it downward. Observed 2026-08-05: Atmane@Khachanov h2h was High
    at 08:40 (EV 6.77%), re-scanned Med at 08:50 (EV 1.99%), and the 08:50 and
    09:00 scans happened to produce zero High picks — so the app showed it
    active, at its stale 6.77%, for 20+ minutes after the system knew better.
    """
    if mute_rules:
        rows = [r for r in rows if not _is_muted_row(r, mute_rules)]
    if not rows:
        return []
    latest = latest_scan or max(r["recorded_at"] for r in rows)
    # Keep one row per pick: prefer the latest-scan row, else its most recent.
    best = {}
    for r in rows:
        k = _key(r)
        if k not in best or r["recorded_at"] > best[k]["recorded_at"]:
            best[k] = r

    states = states or {}
    out = []
    for k, r in best.items():
        st = states.get(k)
        if st in ("bet", "dismissed"):
            status = st
        elif _expired(r["commence_time"], now):
            status = "expired"
        elif r["recorded_at"] == latest:
            status = "active"
        else:
            status = "cooled"               # was High earlier, gone from latest scan
        out.append({**r, "id": _encode_id(k), "status": status,
                    "ev": r.get("snapshot_ev"), "book": r.get("best_soft_book"),
                    "odds": r.get("best_soft_price")})

    out = _apply_filters(out, prefs)
    out.sort(key=_sort_key(prefs))
    return out


def _apply_filters(picks, prefs):
    """Filters apply to ACTIVE picks only (cooled/expired/bet/dismissed always
    pass through for their collapsed sections)."""
    if not prefs:
        return picks
    min_ev = prefs.get("min_ev") or 0
    sports = set(json.loads(prefs["sports_json"])) if prefs.get("sports_json") else None
    muted = set(json.loads(prefs["muted_books_json"])) if prefs.get("muted_books_json") else set()
    kept = []
    for p in picks:
        if p["status"] != "active":
            kept.append(p)
            continue
        if (p.get("ev") or 0) < min_ev:
            continue
        if sports is not None and p.get("sport") not in sports:
            continue
        if p.get("book") in muted:
            continue
        kept.append(p)
    return kept


def _sort_key(prefs):
    sort = (prefs or {}).get("sort") or "ev"
    if sort == "time":
        return lambda p: p.get("commence_time") or ""
    if sort == "n_books":
        return lambda p: -(p.get("n_books") or 0)
    return lambda p: -(p.get("ev") or 0)


def _encode_id(key):
    """Stable opaque pick id for the API path: pipe-join the natural key."""
    eid, market, side, point, player = key
    return "|".join((eid, market, side, str(point), player))


def decode_id(pick_id):
    """Inverse of _encode_id -> (event_id, market, side, point, player)."""
    eid, market, side, point, player = pick_id.split("|", 4)
    return (eid, market, side, float(point), player)
