# +EV Companion App — Backend (Plan 1 of 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the EC2-hosted backend that powers the personal +EV betting companion iOS app — a FastAPI service over the existing softbook pipeline, plus a tiered sportsbook deep-link builder, an Expo push worker (replacing the Telegram sender), and a live in-game bet-grading poller.

**Architecture:** New Python modules live in `scripts/` alongside the existing `softbook_ev_*.py` and import `softbook_ev_core as core` (same-dir import; tests add `scripts/` to `sys.path`). The existing 20-min scan / recorder / settlement pipeline is unchanged — we read its `softbook_ev.db` snapshots for the live High-pick feed and add four app tables to the ubuntu-owned `softbook_paper.db`. The FastAPI app is exposed via Cloudflare Tunnel + Access (zero inbound ports). This plan is the foundation; Plan 2 (the Expo iOS app) consumes this API.

**Tech Stack:** Python 3 (stdlib + FastAPI/uvicorn/httpx, already in `requirements.txt`), SQLite, The Odds API single-event endpoint, MLB StatsAPI (public, no auth), Expo Push API, systemd, Cloudflare Tunnel + Access. Tests: pytest.

---

## Scope & Decomposition

Phase 1 of the spec (`docs/superpowers/specs/2026-06-12-ev-betting-companion-app-design.md`) is split into two plans because the backend (Python, this repo) and the iOS app (TypeScript/Expo, new project) are independent subsystems with a clean HTTP boundary:

- **Plan 1 (this document) — Backend:** DB tables, deep-link builder, feed composition, FastAPI endpoints, Expo push worker, live-grade poller, systemd + Cloudflare deploy. Fully testable in-repo with pytest; ships working software (a live, authenticated API + push) on its own.
- **Plan 2 (follow-up) — iOS Expo app:** screens, deep-link opening, one-tap capture UI, push registration. Depends on this plan's API contract. Written after the API is running.

## Manual Prerequisites (the human does these — NOT code tasks)

These cannot be automated by an agent and gate parts of the plan. Do them first; the plan notes where each is needed:

1. **Domain:** buy one at Cloudflare Registrar (~$10/yr), nameservers on Cloudflare Free DNS. Needed for Task 11.
2. **Cloudflare Zero Trust:** enable (free), create a Tunnel and an Access service token. Needed for Task 11 + Task 4 (the `CF_ACCESS_TEAM_DOMAIN` / `CF_ACCESS_AUD` for JWT validation).
3. **Apple Developer Program ($99/yr)** + **Expo/EAS account:** needed for Plan 2 (push delivery to the device) and to obtain a real Expo push token to test Task 9 end-to-end. The push *sender* (Task 9) is unit-testable without it.
4. **`.env` additions on EC2** (`/home/ubuntu/kalshi-arb/.env`): `APP_API_TOKEN=<long random string>` (the app's bearer), `CF_ACCESS_TEAM_DOMAIN=<team>.cloudflareaccess.com`, `CF_ACCESS_AUD=<access app AUD tag>`. Needed for Task 4.

## File Structure

New files (all created by this plan):

- `scripts/softbook_app_db.py` — app-table schema (`pick_state`, `app_bet`, `filter_prefs`, `push_token`), connection helper, `real_fill → app_bet` migration. One responsibility: app-side persistence.
- `scripts/softbook_deeplink.py` — pure tiered deep-link builder + Odds-API link/SID extraction. No I/O.
- `scripts/softbook_app_feed.py` — pure feed composition (active/cooled/expired classification + filter application) over snapshot rows. No I/O.
- `scripts/softbook_push.py` — Expo Push API sender + token pruning. The transport layer.
- `scripts/softbook_push_worker.py` — detects new High / reverse-middle / settlement results and sends pushes. Replaces the Telegram send path.
- `scripts/softbook_live_grade.py` — maps open `app_bet` rows to live MLB StatsAPI box scores and computes progress + projected status.
- `scripts/softbook_app_api.py` — FastAPI app wiring all endpoints + auth dependency.
- `tests/test_softbook_app_db.py`, `tests/test_softbook_deeplink.py`, `tests/test_softbook_app_feed.py`, `tests/test_softbook_push.py`, `tests/test_softbook_live_grade.py`, `tests/test_softbook_app_api.py` — one test file per module.
- `deploy/kalshi-softbook-api.service`, `deploy/kalshi-softbook-push.service`, `deploy/kalshi-softbook-live-grade.service`, `deploy/kalshi-softbook-live-grade.timer`, `deploy/cloudflared-config.yml`, `docs/setup-cloudflare-tunnel.md` — deployment.

Modified files: none in the existing pipeline. (`requirements.txt` already has fastapi/uvicorn/httpx; we add `PyJWT` in Task 4.)

## Conventions (match these exactly)

- Same-dir imports in `scripts/`: `import softbook_ev_core as core`.
- Tests import via `sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))` then `import <module>` (see `tests/test_softbook_fill_listener.py`).
- DB paths via env with the production defaults already used in the repo:
  `SOFTBOOK_EV_DB=/home/ubuntu/kalshi-arb/data/softbook_ev.db` (root-owned: `softbook_ev`, `high_alerted`, `wakeup_sent`),
  `SOFTBOOK_PAPER_DB=/home/ubuntu/kalshi-arb/data/softbook_paper.db` (ubuntu-owned: `paper_bet`, `paper_bet_high`, `real_fill`, `tg_state`).
- Pick identity natural key everywhere: `(event_id, market, side, point, player)` with `point` defaulting to `0` and `player` to `''` (matches `high_alerted`).
- Run tests: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb && python -m pytest tests/<file> -v` (use the repo venv on EC2; locally any py3 with `pip install fastapi uvicorn httpx pyjwt pytest`).
- Commit after every task with `git add <explicit paths>` (the user keeps parallel uncommitted edits — never `git add .`).

---

### Task 1: App DB module — schema, connection, migration

**Files:**
- Create: `scripts/softbook_app_db.py`
- Test: `tests/test_softbook_app_db.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_softbook_app_db.py
"""App-side persistence for the +EV companion app: pick_state (bet/dismissed
overrides on the live feed), app_bet (captured bets = calendar/PnL source of
truth), filter_prefs, push_token. Plus a one-time real_fill -> app_bet migration."""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import softbook_app_db as adb  # noqa: E402


def mem():
    db = sqlite3.connect(":memory:")
    adb.init_schema(db)
    return db


def test_init_schema_creates_all_tables():
    db = mem()
    names = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"pick_state", "app_bet", "filter_prefs", "push_token"} <= names


def test_init_schema_is_idempotent():
    db = mem()
    adb.init_schema(db)  # second call must not raise
    assert db.execute("SELECT count(*) FROM app_bet").fetchone() == (0,)


def test_set_pick_state_upsert():
    db = mem()
    pick = ("E1", "total", "Under", 169.5, "")
    adb.set_pick_state(db, pick, "dismissed", "2026-06-12T00:00:00+00:00")
    adb.set_pick_state(db, pick, "bet", "2026-06-12T00:05:00+00:00")  # overwrite
    rows = db.execute("SELECT state FROM pick_state").fetchall()
    assert rows == [("bet",)]


def test_pick_states_returns_keyed_dict():
    db = mem()
    adb.set_pick_state(db, ("E1", "h2h", "NYM", 0, ""), "bet", "t")
    adb.set_pick_state(db, ("E2", "total", "Over", 8.5, ""), "dismissed", "t")
    states = adb.pick_states(db)
    assert states[("E1", "h2h", "NYM", 0.0, "")] == "bet"
    assert states[("E2", "total", "Over", 8.5, "")] == "dismissed"


def test_insert_app_bet_returns_id_and_persists():
    db = mem()
    bet = {"event_id": "E1", "sport": "baseball_mlb", "home": "MIA", "away": "NYM",
           "commence_time": "2026-06-12T23:10:00Z", "market": "batter_total_bases",
           "side": "Over", "point": 1.5, "player": "Brandon Nimmo",
           "label": "Brandon Nimmo TB Over 1.5", "book": "theScore",
           "rec_odds": 2.2, "fill_odds": 2.2, "stake": 2.0,
           "placed_at": "2026-06-12T18:00:00+00:00", "source": "one_tap"}
    bid = adb.insert_app_bet(db, bet)
    assert isinstance(bid, int)
    row = db.execute("SELECT player, book, fill_odds, stake, source, live_status,"
                     " settled FROM app_bet WHERE id=?", (bid,)).fetchone()
    assert row == ("Brandon Nimmo", "theScore", 2.2, 2.0, "one_tap", "pending", 0)


def test_migrate_real_fill_copies_filled_not_skipped():
    db = mem()
    db.execute("""CREATE TABLE real_fill(
        message_id INTEGER PRIMARY KEY, event_id TEXT, market TEXT, side TEXT,
        point REAL, player TEXT, alert_book TEXT, alert_odds REAL,
        fill_book TEXT, fill_odds REAL, fill_stake REAL, skipped INTEGER,
        raw_reply TEXT, replied_at TEXT, cashed_out INTEGER, cashout_amount REAL)""")
    db.execute("INSERT INTO real_fill VALUES (1,'E1','total','Under',169.5,'',"
               "'BetMGM',2.06,'FanDuel',1.98,5.0,0,'1.98 fd','t',0,NULL)")
    db.execute("INSERT INTO real_fill VALUES (2,'E2','h2h','NYM',0,'',"
               "'BetMGM',1.9,NULL,NULL,NULL,1,'skip','t',0,NULL)")  # skip -> excluded
    n = adb.migrate_real_fill(db)
    assert n == 1
    row = db.execute("SELECT event_id, book, fill_odds, stake, source, rec_odds"
                     " FROM app_bet").fetchone()
    assert row == ("E1", "FanDuel", 1.98, 5.0, "telegram", 2.06)


def test_migrate_real_fill_is_idempotent():
    db = mem()
    db.execute("""CREATE TABLE real_fill(
        message_id INTEGER PRIMARY KEY, event_id TEXT, market TEXT, side TEXT,
        point REAL, player TEXT, alert_book TEXT, alert_odds REAL,
        fill_book TEXT, fill_odds REAL, fill_stake REAL, skipped INTEGER,
        raw_reply TEXT, replied_at TEXT, cashed_out INTEGER, cashout_amount REAL)""")
    db.execute("INSERT INTO real_fill VALUES (1,'E1','total','Under',169.5,'',"
               "'BetMGM',2.06,'FanDuel',1.98,5.0,0,'1.98 fd','t',0,NULL)")
    adb.migrate_real_fill(db)
    adb.migrate_real_fill(db)  # second run must not duplicate
    assert db.execute("SELECT count(*) FROM app_bet").fetchone() == (1,)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_softbook_app_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'softbook_app_db'`.

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/softbook_app_db.py
#!/usr/bin/env python3
"""App-side persistence for the +EV companion app.

Four tables in the ubuntu-owned softbook_paper.db (the existing pipeline tables
are NOT touched):
  pick_state   user override on the live scan feed (bet/dismissed) so a
               dismissed/placed pick never re-appears on the next 20-min scan.
  app_bet      bets actually placed = source of truth for calendar PnL + live
               tracking. Supersedes the Telegram-keyed real_fill table.
  filter_prefs single-row feed filter (min EV, sports, muted books, sort).
  push_token   Expo push tokens (re-registered each app launch, pruned on
               DeviceNotRegistered).

LEDGER RULE (load-bearing): app_bet rows are bet records — never delete or
reprice them for measurement hygiene; segment instead.
"""
import os
import sqlite3

PAPER_DB = os.getenv("SOFTBOOK_PAPER_DB",
                     "/home/ubuntu/kalshi-arb/data/softbook_paper.db")

PICK_STATE_SCHEMA = """CREATE TABLE IF NOT EXISTS pick_state(
    event_id TEXT, market TEXT, side TEXT, point REAL, player TEXT,
    state TEXT NOT NULL,                 -- 'bet' | 'dismissed'
    updated_at TEXT NOT NULL,
    PRIMARY KEY (event_id, market, side, point, player))"""

APP_BET_SCHEMA = """CREATE TABLE IF NOT EXISTS app_bet(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT, sport TEXT, home TEXT, away TEXT, commence_time TEXT,
    market TEXT, side TEXT, point REAL, player TEXT, label TEXT,
    book TEXT, rec_odds REAL, fill_odds REAL, stake REAL,
    placed_at TEXT, source TEXT,
    live_status TEXT DEFAULT 'pending', live_progress TEXT,
    settled INTEGER DEFAULT 0, won INTEGER, pnl REAL, settled_at TEXT)"""

FILTER_PREFS_SCHEMA = """CREATE TABLE IF NOT EXISTS filter_prefs(
    id INTEGER PRIMARY KEY CHECK (id = 1),
    min_ev REAL DEFAULT 0.0, sports_json TEXT, muted_books_json TEXT,
    sort TEXT DEFAULT 'ev')"""

PUSH_TOKEN_SCHEMA = """CREATE TABLE IF NOT EXISTS push_token(
    token TEXT PRIMARY KEY, updated_at TEXT, active INTEGER DEFAULT 1)"""

APP_BET_COLS = ("event_id, sport, home, away, commence_time, market, side, point,"
                " player, label, book, rec_odds, fill_odds, stake, placed_at, source")


def init_schema(db):
    for s in (PICK_STATE_SCHEMA, APP_BET_SCHEMA, FILTER_PREFS_SCHEMA, PUSH_TOKEN_SCHEMA):
        db.execute(s)
    db.commit()


def connect():
    db = sqlite3.connect(PAPER_DB, timeout=60)
    init_schema(db)
    return db


def _norm(pick):
    """(event_id, market, side, point, player) with point->0.0, player->''."""
    eid, market, side, point, player = pick
    return (eid, market, side, float(point or 0), player or "")


def set_pick_state(db, pick, state, now):
    db.execute(
        "INSERT INTO pick_state(event_id,market,side,point,player,state,updated_at)"
        " VALUES (?,?,?,?,?,?,?)"
        " ON CONFLICT(event_id,market,side,point,player)"
        " DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at",
        (*_norm(pick), state, now))
    db.commit()


def pick_states(db):
    """All overrides keyed by the normalized pick tuple -> state."""
    return {(_norm((e, m, s, p, pl))): st for e, m, s, p, pl, st in db.execute(
        "SELECT event_id, market, side, point, player, state FROM pick_state")}


def insert_app_bet(db, bet):
    cur = db.execute(
        f"INSERT INTO app_bet({APP_BET_COLS}) VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (bet["event_id"], bet.get("sport"), bet.get("home"), bet.get("away"),
         bet.get("commence_time"), bet["market"], bet["side"],
         float(bet.get("point") or 0), bet.get("player") or "", bet.get("label"),
         bet.get("book"), bet.get("rec_odds"), bet.get("fill_odds"),
         bet.get("stake"), bet.get("placed_at"), bet.get("source")))
    db.commit()
    return cur.lastrowid


def migrate_real_fill(db):
    """Copy FILLED (skipped=0) real_fill rows into app_bet once. Idempotent via
    a source='telegram' + event/market/side/point/player de-dup check. Returns
    the number of new rows inserted. (real_fill lacks sport/home/away/label/
    commence_time; those stay NULL and the app reads them live by event_id.)"""
    try:
        rows = db.execute(
            "SELECT event_id, market, side, point, player, alert_odds, fill_book,"
            " fill_odds, fill_stake, replied_at FROM real_fill WHERE skipped=0"
        ).fetchall()
    except sqlite3.OperationalError:
        return 0
    n = 0
    for (eid, market, side, point, player, aodds, fbook, fodds, stake, rat) in rows:
        exists = db.execute(
            "SELECT 1 FROM app_bet WHERE source='telegram' AND event_id=? AND"
            " market=? AND side=? AND point=? AND player=?",
            (eid, market, side, float(point or 0), player or "")).fetchone()
        if exists:
            continue
        db.execute(
            f"INSERT INTO app_bet({APP_BET_COLS}) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, None, None, None, None, market, side, float(point or 0),
             player or "", None, fbook, aodds, fodds, stake, rat, "telegram"))
        n += 1
    db.commit()
    return n
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_softbook_app_db.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/softbook_app_db.py tests/test_softbook_app_db.py
git commit -m "feat(softbook-app): app DB tables + real_fill->app_bet migration"
```

---

### Task 2: Tiered deep-link builder

**Files:**
- Create: `scripts/softbook_deeplink.py`
- Test: `tests/test_softbook_deeplink.py`

Research conclusion (spec, Deep-Link section): bet-slip **prefill is primary-source-confirmed only
for FanDuel (provider-returned `addToBetslip` universal link) and DraftKings (`dksb://sb/addbet/<sid>`)**,
and is best-effort. Everything else opens the event/app + copyable text. Links come from The Odds API
(`includeLinks=true&includeSids=true`) — never hand-built (state-host drift). The builder is a pure
function; payload extraction is a separate pure function.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_softbook_deeplink.py
"""Tiered sportsbook deep-link builder. FD/DK -> best-effort bet-slip prefill
from provider-returned links/SIDs; all other books -> open event/app + copyable
bet text (always present so a failed prefill degrades to manual entry)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import softbook_deeplink as dl  # noqa: E402

PICK = {"book": "FanDuel", "label": "Brandon Nimmo TB Over 1.5", "odds": 2.2,
        "market": "batter_total_bases", "side": "Over", "point": 1.5,
        "player": "Brandon Nimmo"}


def test_copy_text_always_present():
    out = dl.build("fanduel", {}, PICK)
    assert out["copy_text"] == "FanDuel · Brandon Nimmo TB Over 1.5 @ 2.20"


def test_fanduel_uses_provider_betslip_universal_link():
    links = {"betslip": "https://sportsbook.fanduel.com/addToBetslip?marketId=42&selectionId=99"}
    out = dl.build("fanduel", links, PICK)
    assert out["tier"] == "prefill"
    assert out["url"] == links["betslip"]


def test_fanduel_without_betslip_link_falls_back_to_event():
    out = dl.build("fanduel", {"event": "https://sportsbook.fanduel.com/baseball"}, PICK)
    assert out["tier"] == "event"
    assert out["url"] == "https://sportsbook.fanduel.com/baseball"


def test_draftkings_builds_dksb_scheme_from_sid():
    out = dl.build("draftkings", {"sid": "0OU76623766U1250_3"}, PICK)
    assert out["tier"] == "prefill"
    assert out["url"] == "dksb://sb/addbet/0OU76623766U1250_3"


def test_draftkings_without_sid_uses_event_link():
    out = dl.build("draftkings", {"event": "https://sportsbook.draftkings.com/event/123"}, PICK)
    assert out["tier"] == "event"
    assert out["url"] == "https://sportsbook.draftkings.com/event/123"


def test_non_prefill_book_never_prefills_even_with_betslip_link():
    # BetMGM/Caesars/etc: prefill is NOT primary-source confirmed -> never claim it
    out = dl.build("betmgm", {"betslip": "https://example/bet"}, PICK)
    assert out["tier"] in ("event", "home")
    assert out["url"] != "https://example/bet"


def test_unknown_book_with_no_links_falls_back_to_none_url():
    out = dl.build("ballybet", {}, PICK)
    assert out["tier"] == "home"
    assert out["url"] is None          # app has no scheme/link -> copy-only
    assert out["copy_text"].startswith("BallyBet ·")


def test_extract_links_pulls_outcome_betslip_and_sid():
    ev = {"bookmakers": [{"key": "fanduel", "markets": [
        {"key": "batter_total_bases", "link": "https://fd/market", "outcomes": [
            {"name": "Over", "description": "Brandon Nimmo", "point": 1.5,
             "price": 2.2, "link": "https://fd/betslip?x=1", "sid": "S1"}]}]}]}
    got = dl.extract_links(ev, "fanduel", "batter_total_bases", "Over", 1.5, "Brandon Nimmo")
    assert got["betslip"] == "https://fd/betslip?x=1"
    assert got["sid"] == "S1"


def test_extract_links_matches_h2h_with_point_none():
    ev = {"bookmakers": [{"key": "draftkings", "markets": [
        {"key": "h2h", "outcomes": [
            {"name": "New York Mets", "price": 1.9, "sid": "DK1"}]}]}]}
    got = dl.extract_links(ev, "draftkings", "h2h", "New York Mets", None, None)
    assert got["sid"] == "DK1"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_softbook_deeplink.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'softbook_deeplink'`.

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/softbook_deeplink.py
#!/usr/bin/env python3
"""Tiered sportsbook deep-link builder for the +EV companion app.

Reality (researched + adversarially verified, see the design spec): bet-slip
PREFILL is primary-source-confirmed only for FanDuel (provider-returned
addToBetslip universal link) and DraftKings (dksb://sb/addbet/<sid>), and is
best-effort even there. Every other book: open the event page / app and rely on
the copyable bet text. Links/SIDs come from The Odds API includeLinks/includeSids;
we never hand-build hosts (state drift). Always emit copy_text so a failed
prefill degrades to manual entry, never a dead end.

Returned dict: {"tier": "prefill"|"event"|"home", "url": str|None, "copy_text": str}
"""
import softbook_ev_core as core

# Only these two get the "prefill" tier; for them we trust a provider betslip
# link / SID. (BetMGM/Caesars/BetRivers/Fanatics/theScore/BallyBet: no public
# prefill confirmed -> event/home only.)
PREFILL_BOOKS = {"fanduel", "draftkings"}

# Universal-link / app home fallbacks (open the app to its home when no
# event/betslip link is available). None => copy-only.
HOME_URL = {
    "fanduel": "https://sportsbook.fanduel.com",
    "draftkings": "https://sportsbook.draftkings.com",
    "betmgm": "https://sports.nj.betmgm.com",
    "williamhill_us": "https://www.williamhill.us",
    "betrivers": "https://www.betrivers.com",
    "fanatics": "https://sportsbook.fanatics.com",
    "espnbet": "https://espnbet.com",
    "ballybet": None,
}


def _odds(px):
    try:
        return f"{float(px):.2f}"
    except Exception:
        return "?"


def build(book_key, links, pick):
    """book_key: Odds API key (e.g. 'fanduel'). links: {betslip?, sid?, event?}
    extracted for THIS outcome. pick: dict with label/odds/book(label)."""
    label = pick.get("book") or core.NY_LEGAL.get(book_key, book_key)
    copy_text = f"{label} · {pick['label']} @ {_odds(pick.get('odds'))}"
    url, tier = None, "home"

    if book_key in PREFILL_BOOKS:
        if book_key == "fanduel" and links.get("betslip"):
            return {"tier": "prefill", "url": links["betslip"], "copy_text": copy_text}
        if book_key == "draftkings" and links.get("sid"):
            return {"tier": "prefill", "url": f"dksb://sb/addbet/{links['sid']}",
                    "copy_text": copy_text}

    if links.get("event"):
        return {"tier": "event", "url": links["event"], "copy_text": copy_text}

    return {"tier": "home", "url": HOME_URL.get(book_key), "copy_text": copy_text}


def extract_links(ev_data, book_key, market, side, point, player):
    """Walk an Odds API single-event payload (includeLinks/includeSids) for the
    one outcome matching this pick; return {betslip, sid, event}. Mirrors
    core.line_quotes' matching (canon market, side name, player desc, point)."""
    target = None if market == "h2h" else round(point or 0.0, 1)
    out = {"betslip": None, "sid": None, "event": None}
    for b in ev_data.get("bookmakers", []):
        if b.get("key") != book_key:
            continue
        out["event"] = b.get("link") or out["event"]
        for m in b.get("markets", []):
            if core._canon(m["key"]) != market:
                continue
            out["event"] = m.get("link") or out["event"]
            for o in m.get("outcomes", []):
                if o["name"] != side or (o.get("description") or None) != (player or None):
                    continue
                pt = None if market == "h2h" else round(o.get("point", 0.0), 1)
                if pt != target:
                    continue
                if o.get("link"):
                    out["betslip"] = o["link"]
                if o.get("sid"):
                    out["sid"] = o["sid"]
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_softbook_deeplink.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/softbook_deeplink.py tests/test_softbook_deeplink.py
git commit -m "feat(softbook-app): tiered deep-link builder (FD/DK prefill, others event/copy)"
```

---

### Task 3: Feed composition (active / cooled / dismissed / expired + filters)

**Files:**
- Create: `scripts/softbook_app_feed.py`
- Test: `tests/test_softbook_app_feed.py`

The feed = the latest scan's High picks (from `softbook_ev`), minus `pick_state` (bet/dismissed),
with each remaining pick classified `active` / `cooled` (was High earlier but not in the latest scan)
/ `expired` (commence_time passed), then filtered. Pure function over rows + states + now.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_softbook_app_feed.py
"""Feed composition: turn the latest softbook_ev scan's High picks into the app
feed, applying pick_state overrides (bet/dismissed leave the feed), cooled/
expired classification, and filter_prefs."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import softbook_app_feed as feed  # noqa: E402

NOW = "2026-06-12T18:00:00+00:00"


def _snap(event_id, market, side, point, player, ev, book="theScore",
          recorded_at=NOW, ct="2026-06-12T23:10:00Z", conf=60, n_books=9,
          sport="baseball_mlb", home="MIA", away="NYM", label=None):
    return {"event_id": event_id, "market": market, "side": side, "point": point,
            "player": player, "snapshot_ev": ev, "best_soft_book": book,
            "best_soft_price": 2.2, "recorded_at": recorded_at, "commence_time": ct,
            "conf": conf, "n_books": n_books, "sport": sport, "home": home,
            "away": away, "label": label or f"{player} {side} {point}"}


def test_only_latest_scan_high_picks_are_active():
    rows = [
        _snap("E1", "total", "Over", 8.5, "", 0.05, recorded_at="2026-06-12T17:40:00+00:00"),
        _snap("E1", "total", "Over", 8.5, "", 0.05, recorded_at=NOW),  # latest -> active
        _snap("E2", "h2h", "NYM", 0, "", 0.03, recorded_at=NOW),
    ]
    out = feed.compose(rows, states={}, prefs=None, now=NOW)
    ids = {(p["event_id"], p["status"]) for p in out}
    assert ("E1", "active") in ids and ("E2", "active") in ids
    assert len([p for p in out if p["event_id"] == "E1"]) == 1  # deduped to latest


def test_bet_and_dismissed_leave_the_active_feed():
    rows = [_snap("E1", "total", "Over", 8.5, "", 0.05),
            _snap("E2", "h2h", "NYM", 0, "", 0.03)]
    states = {("E1", "total", "Over", 8.5, ""): "bet",
              ("E2", "h2h", "NYM", 0.0, ""): "dismissed"}
    out = feed.compose(rows, states=states, prefs=None, now=NOW)
    assert [p for p in out if p["status"] == "active"] == []
    assert {p["status"] for p in out} == {"bet", "dismissed"}


def test_cooled_when_high_earlier_but_absent_from_latest_scan():
    rows = [
        _snap("E1", "total", "Over", 8.5, "", 0.05, recorded_at="2026-06-12T17:40:00+00:00"),
        _snap("E9", "h2h", "NYM", 0, "", 0.03, recorded_at=NOW),  # defines latest scan
    ]
    out = feed.compose(rows, states={}, prefs=None, now=NOW)
    e1 = next(p for p in out if p["event_id"] == "E1")
    assert e1["status"] == "cooled"


def test_expired_when_commence_time_passed():
    rows = [_snap("E1", "total", "Over", 8.5, "", 0.05, ct="2026-06-12T17:00:00Z")]
    out = feed.compose(rows, states={}, prefs=None, now=NOW)
    assert out[0]["status"] == "expired"


def test_min_ev_filter_drops_low_ev_actives():
    rows = [_snap("E1", "total", "Over", 8.5, "", 0.05),
            _snap("E2", "h2h", "NYM", 0, "", 0.015)]
    prefs = {"min_ev": 0.03, "sports_json": None, "muted_books_json": None, "sort": "ev"}
    out = feed.compose(rows, states={}, prefs=prefs, now=NOW)
    active = [p for p in out if p["status"] == "active"]
    assert [p["event_id"] for p in active] == ["E1"]


def test_muted_book_filter_drops_active():
    rows = [_snap("E1", "total", "Over", 8.5, "", 0.05, book="theScore")]
    prefs = {"min_ev": 0, "sports_json": None,
             "muted_books_json": '["theScore"]', "sort": "ev"}
    out = feed.compose(rows, states={}, prefs=prefs, now=NOW)
    assert [p for p in out if p["status"] == "active"] == []


def test_sort_by_ev_desc():
    rows = [_snap("E1", "total", "Over", 8.5, "", 0.03),
            _snap("E2", "h2h", "NYM", 0, "", 0.07)]
    out = feed.compose(rows, states={}, prefs=None, now=NOW)
    active = [p for p in out if p["status"] == "active"]
    assert [p["event_id"] for p in active] == ["E2", "E1"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_softbook_app_feed.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'softbook_app_feed'`.

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/softbook_app_feed.py
#!/usr/bin/env python3
"""Feed composition for the +EV companion app.

Active feed = the LATEST softbook_ev scan's High picks, minus pick_state
(bet/dismissed), classified active/cooled/expired, then filtered by filter_prefs.
Pure function: takes already-fetched High snapshot rows (list of dicts), the
pick_state map, the prefs row, and `now`. No DB or network.
"""
import json
import softbook_ev_core as core


def _key(p):
    return (p["event_id"], p["market"], p["side"],
            float(p.get("point") or 0), p.get("player") or "")


def _expired(ct, now):
    h = core.hrs_to(ct)
    return h is not None and h <= 0


def compose(rows, states, prefs, now):
    """rows: High-tier softbook_ev snapshots (any scans). states: {key->state}.
    prefs: filter_prefs row dict or None. Returns picks with a 'status' field."""
    if not rows:
        return []
    latest = max(r["recorded_at"] for r in rows)
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_softbook_app_feed.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/softbook_app_feed.py tests/test_softbook_app_feed.py
git commit -m "feat(softbook-app): feed composition (active/cooled/expired + filters)"
```

---

### Task 4: FastAPI app — auth + `/picks` + `/picks/{id}`

**Files:**
- Create: `scripts/softbook_app_api.py`
- Modify: `requirements.txt` (add `PyJWT>=2.8.0`)
- Test: `tests/test_softbook_app_api.py`

Auth model (spec, Security): the iOS app sends Cloudflare Access service-token headers
(validated at the edge) + a long-lived bearer in `Authorization`. FastAPI re-validates the bearer
(belt-and-suspenders) and, when present, the `Cf-Access-Jwt-Assertion`. For local tests we inject
the env token and skip JWT (no Cloudflare in front).

- [ ] **Step 1: Add the dependency and write the failing test**

Add to `requirements.txt` (after the existing `httpx>=0.27.0` line):

```
PyJWT>=2.8.0
```

```python
# tests/test_softbook_app_api.py
"""FastAPI app endpoints over the softbook pipeline + app tables. Uses an
in-memory-ish seeded SQLite copy via env DB paths and FastAPI's TestClient."""
import json
import os
import sqlite3
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


@pytest.fixture()
def client(monkeypatch, tmp_path):
    ev_db = tmp_path / "ev.db"
    paper_db = tmp_path / "paper.db"
    # seed softbook_ev with one High pick on the latest scan
    e = sqlite3.connect(ev_db)
    e.execute("""CREATE TABLE softbook_ev(
        recorded_at TEXT, sport TEXT, event_id TEXT, commence_time TEXT,
        home TEXT, away TEXT, market TEXT, side TEXT, point REAL, player TEXT,
        pinnacle_fair_prob REAL, pinnacle_price REAL, best_soft_book TEXT,
        best_soft_price REAL, snapshot_ev REAL, n_books INTEGER, n_ev INTEGER,
        conf REAL, tier TEXT)""")
    # recorded_at must be recent: the API filters datetime(recorded_at) >=
    # datetime('now','-2 days'), so a fixed past date would break this test over
    # time. Use SQLite's datetime('now') so it is always inside the window.
    e.execute("INSERT INTO softbook_ev VALUES (datetime('now'),"
              "'baseball_mlb','E1','2099-01-01T00:00:00Z','MIA','NYM',"
              "'batter_total_bases','Over',1.5,'Brandon Nimmo',0.45,2.0,"
              "'theScore',2.2,0.074,9,5,60,'High')")
    e.commit(); e.close()
    monkeypatch.setenv("SOFTBOOK_EV_DB", str(ev_db))
    monkeypatch.setenv("SOFTBOOK_PAPER_DB", str(paper_db))
    monkeypatch.setenv("APP_API_TOKEN", "secret")
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "")  # disables JWT check in tests
    import importlib
    import softbook_app_api
    importlib.reload(softbook_app_api)
    return TestClient(softbook_app_api.app)


def test_unauthorized_without_bearer(client):
    assert client.get("/picks").status_code == 401


def test_picks_returns_active_high(client):
    r = client.get("/picks", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    picks = r.json()["picks"]
    assert len(picks) == 1
    p = picks[0]
    assert p["status"] == "active" and p["player"] == "Brandon Nimmo"
    assert p["id"] == "E1|batter_total_bases|Over|1.5|Brandon Nimmo"


def test_pick_detail_by_id(client):
    pid = "E1|batter_total_bases|Over|1.5|Brandon Nimmo"
    r = client.get(f"/picks/{pid}", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    assert r.json()["label"] == "Brandon Nimmo TB Over 1.5" or r.json()["side"] == "Over"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_softbook_app_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'softbook_app_api'`.

- [ ] **Step 3: Write minimal implementation**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_softbook_app_api.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/softbook_app_api.py tests/test_softbook_app_api.py requirements.txt
git commit -m "feat(softbook-app): FastAPI service auth + /picks feed + /picks/{id}"
```

---

### Task 5: On-demand odds refresh — `/picks/{id}/refresh`

**Files:**
- Modify: `scripts/softbook_app_api.py`
- Test: `tests/test_softbook_app_api.py` (add)

Reuses `core.event_odds()` (single-event, scoped to the pick's markets → ~3-5 credits) +
`core.line_quotes()` for fresh per-book odds and `softbook_deeplink` for the tiered links.

- [ ] **Step 1: Write the failing test (add to the file)**

```python
# append to tests/test_softbook_app_api.py

def test_refresh_returns_fresh_quotes_and_deeplinks(client, monkeypatch):
    import softbook_app_api
    # stub the Odds API single-event call
    ev_payload = {"home_team": "MIA", "away_team": "NYM", "bookmakers": [
        {"key": "pinnacle", "markets": [{"key": "batter_total_bases", "outcomes": [
            {"name": "Over", "description": "Brandon Nimmo", "point": 1.5, "price": 1.9},
            {"name": "Under", "description": "Brandon Nimmo", "point": 1.5, "price": 1.9}]}]},
        {"key": "fanduel", "markets": [{"key": "batter_total_bases", "outcomes": [
            {"name": "Over", "description": "Brandon Nimmo", "point": 1.5, "price": 2.2,
             "link": "https://sportsbook.fanduel.com/addToBetslip?marketId=1&selectionId=2"}]}]},
    ]}
    monkeypatch.setattr(softbook_app_api.core, "event_odds",
                        lambda sport, eid, key, markets=None: (ev_payload, 4))
    monkeypatch.setattr(softbook_app_api.core, "read_key", lambda: "k")
    pid = "E1|batter_total_bases|Over|1.5|Brandon Nimmo"
    r = client.get(f"/picks/{pid}/refresh", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    body = r.json()
    books = {b["book"]: b for b in body["books"]}
    assert "FanDuel" in books
    fd = books["FanDuel"]
    assert fd["odds"] == 2.2
    assert fd["deeplink"]["tier"] == "prefill"
    assert fd["deeplink"]["url"].startswith("https://sportsbook.fanduel.com/addToBetslip")
    assert body["credits"] == 4
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_softbook_app_api.py::test_refresh_returns_fresh_quotes_and_deeplinks -v`
Expected: FAIL — 404 (route not defined yet).

- [ ] **Step 3: Add the implementation to `scripts/softbook_app_api.py`**

Add this import near the top (with the other `import softbook_*` lines):

```python
import softbook_deeplink as deeplink
```

Add these helpers + route at the end of the file:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_softbook_app_api.py -v`
Expected: PASS (all, including the new refresh test).

- [ ] **Step 5: Commit**

```bash
git add scripts/softbook_app_api.py tests/test_softbook_app_api.py
git commit -m "feat(softbook-app): /picks/{id}/refresh — single-event odds + deep-links"
```

---

### Task 6: Capture + dismiss — `POST /bets`, `POST /picks/{id}/dismiss`

**Files:**
- Modify: `scripts/softbook_app_api.py`
- Test: `tests/test_softbook_app_api.py` (add)

`POST /bets` writes an `app_bet` and marks the pick `bet`. `POST /picks/{id}/dismiss` marks it
`dismissed` and, for reverse-middle pairs (same game, opposite direction, can't both win — reuse
`softbook_ev_recorder._no_both_win`), auto-dismisses the opposite side too.

- [ ] **Step 1: Write the failing test (add to the file)**

```python
# append to tests/test_softbook_app_api.py

def test_post_bet_records_and_marks_pick_bet(client):
    body = {"event_id": "E1", "sport": "baseball_mlb", "home": "MIA", "away": "NYM",
            "commence_time": "2099-01-01T00:00:00Z", "market": "batter_total_bases",
            "side": "Over", "point": 1.5, "player": "Brandon Nimmo",
            "label": "Brandon Nimmo TB Over 1.5", "book": "theScore",
            "rec_odds": 2.2, "fill_odds": 2.25, "stake": 2.0, "source": "one_tap"}
    r = client.post("/bets", json=body, headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200 and r.json()["id"] >= 1
    # pick now excluded from the active feed
    feed_r = client.get("/picks", headers={"Authorization": "Bearer secret"})
    assert feed_r.json()["counts"].get("bet") == 1
    assert feed_r.json()["picks"] == []


def test_dismiss_marks_pick_dismissed(client):
    pid = "E1|batter_total_bases|Over|1.5|Brandon Nimmo"
    r = client.post(f"/picks/{pid}/dismiss", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    feed_r = client.get("/picks", headers={"Authorization": "Bearer secret"})
    assert feed_r.json()["counts"].get("dismissed") == 1
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_softbook_app_api.py::test_post_bet_records_and_marks_pick_bet -v`
Expected: FAIL — 405/404 (routes not defined).

- [ ] **Step 3: Add the implementation to `scripts/softbook_app_api.py`**

Add near the other imports:

```python
import datetime
from pydantic import BaseModel
import softbook_ev_recorder as rec
```

Add the model + routes at the end:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_softbook_app_api.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add scripts/softbook_app_api.py tests/test_softbook_app_api.py
git commit -m "feat(softbook-app): POST /bets capture + /dismiss with reverse-middle auto-dismiss"
```

---

### Task 7: My bets — `GET /mybets`, settings, push-token register

**Files:**
- Modify: `scripts/softbook_app_api.py`
- Test: `tests/test_softbook_app_api.py` (add)

`GET /mybets` returns calendar-ready PnL (per-day sums of settled `app_bet`) + open bets with live
status. Settings read/write `filter_prefs`. `POST /push/register` upserts a push token.

- [ ] **Step 1: Write the failing test (add to the file)**

```python
# append to tests/test_softbook_app_api.py

def test_mybets_groups_open_and_calendar(client):
    # place a bet, then settle one manually in the DB
    body = {"event_id": "E1", "sport": "baseball_mlb", "market": "batter_total_bases",
            "side": "Over", "point": 1.5, "player": "Brandon Nimmo",
            "label": "x", "book": "theScore", "fill_odds": 2.2, "stake": 2.0}
    client.post("/bets", json=body, headers={"Authorization": "Bearer secret"})
    import softbook_app_db as adb2
    pdb = adb2.connect()
    pdb.execute("UPDATE app_bet SET settled=1, won=1, pnl=2.4,"
                " settled_at='2026-06-12T22:00:00+00:00'")
    pdb.commit(); pdb.close()
    r = client.get("/mybets", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    body = r.json()
    assert body["calendar"]["2026-06-12"] == 2.4
    assert isinstance(body["open"], list)


def test_settings_roundtrip(client):
    put = client.put("/settings", json={"min_ev": 0.03, "sports": ["baseball_mlb"],
                                        "muted_books": ["BallyBet"], "sort": "time"},
                     headers={"Authorization": "Bearer secret"})
    assert put.status_code == 200
    got = client.get("/settings", headers={"Authorization": "Bearer secret"}).json()
    assert got["min_ev"] == 0.03 and got["sort"] == "time"
    assert got["muted_books"] == ["BallyBet"]


def test_push_register_upserts_token(client):
    for _ in range(2):
        r = client.post("/push/register", json={"token": "ExponentPushToken[abc]"},
                        headers={"Authorization": "Bearer secret"})
        assert r.status_code == 200
    import softbook_app_db as adb2
    pdb = adb2.connect()
    n = pdb.execute("SELECT count(*) FROM push_token").fetchone()[0]
    pdb.close()
    assert n == 1
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_softbook_app_api.py -k "mybets or settings or push_register" -v`
Expected: FAIL — routes 404/405.

- [ ] **Step 3: Add the implementation to `scripts/softbook_app_api.py`**

Add models + routes at the end:

```python
class SettingsIn(BaseModel):
    min_ev: float = 0.0
    sports: list[str] | None = None
    muted_books: list[str] | None = None
    sort: str = "ev"


class TokenIn(BaseModel):
    token: str


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_softbook_app_api.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add scripts/softbook_app_api.py tests/test_softbook_app_api.py
git commit -m "feat(softbook-app): /mybets calendar+open, /settings, /push/register"
```

---

### Task 8: Expo Push sender

**Files:**
- Create: `scripts/softbook_push.py`
- Test: `tests/test_softbook_push.py`

The transport: POST messages to `https://exp.host/--/api/v2/push/send`, then pull receipts and prune
tokens that report `DeviceNotRegistered`. Uses `httpx` (already a dep). Network is injected for tests.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_softbook_push.py
"""Expo Push API sender. Builds the message payload, posts to exp.host, prunes
tokens that come back DeviceNotRegistered."""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import softbook_push as push  # noqa: E402


def test_build_message_has_required_fields():
    msg = push.build_message("ExponentPushToken[x]", "New High",
                             "Nimmo TB Over 1.5 · EV 7.4%",
                             data={"pick_id": "E1|..."}, category="high_pick")
    assert msg["to"] == "ExponentPushToken[x]"
    assert msg["title"] == "New High"
    assert msg["categoryId"] == "high_pick"
    assert msg["data"]["pick_id"] == "E1|..."


def test_send_posts_chunked_and_returns_tickets():
    posted = {}

    def fake_post(url, json):
        posted["url"] = url
        posted["json"] = json
        return {"data": [{"status": "ok", "id": "T1"}]}

    tickets = push.send([push.build_message("ExponentPushToken[x]", "t", "b")],
                        post=fake_post)
    assert posted["url"] == "https://exp.host/--/api/v2/push/send"
    assert tickets == [{"status": "ok", "id": "T1"}]


def test_prune_marks_device_not_registered_inactive():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE push_token(token TEXT PRIMARY KEY, updated_at TEXT,"
               " active INTEGER DEFAULT 1)")
    db.execute("INSERT INTO push_token VALUES ('ExponentPushToken[dead]','t',1)")
    receipts = {"R1": {"status": "error",
                       "details": {"error": "DeviceNotRegistered"}}}
    push.prune_tokens(db, {"R1": "ExponentPushToken[dead]"}, receipts)
    assert db.execute("SELECT active FROM push_token").fetchone() == (0,)


def test_active_tokens_reads_only_active():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE push_token(token TEXT PRIMARY KEY, updated_at TEXT,"
               " active INTEGER DEFAULT 1)")
    db.execute("INSERT INTO push_token VALUES ('A','t',1)")
    db.execute("INSERT INTO push_token VALUES ('B','t',0)")
    assert push.active_tokens(db) == ["A"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_softbook_push.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'softbook_push'`.

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/softbook_push.py
#!/usr/bin/env python3
"""Expo Push API sender for the +EV companion app (replaces the Telegram send
path). POST messages to exp.host, pull receipts, prune dead tokens.

Docs shape: POST https://exp.host/--/api/v2/push/send with one message or an
array (<=100). Each returns a ticket {status, id}. Later POST the ticket ids to
/getReceipts; a DeviceNotRegistered receipt means stop sending to that token.
"""
SEND_URL = "https://exp.host/--/api/v2/push/send"
RECEIPT_URL = "https://exp.host/--/api/v2/push/getReceipts"


def build_message(token, title, body, data=None, category=None):
    msg = {"to": token, "title": title, "body": body, "sound": "default"}
    if data:
        msg["data"] = data
    if category:
        msg["categoryId"] = category
    return msg


def _http_post(url, json):
    import httpx
    return httpx.post(url, json=json, timeout=20,
                      headers={"Content-Type": "application/json"}).json()


def send(messages, post=_http_post):
    """Send in chunks of 100; return the flat list of tickets."""
    tickets = []
    for i in range(0, len(messages), 100):
        resp = post(SEND_URL, messages[i:i + 100])
        tickets.extend(resp.get("data", []))
    return tickets


def get_receipts(ticket_ids, post=_http_post):
    if not ticket_ids:
        return {}
    out = {}
    for i in range(0, len(ticket_ids), 1000):
        resp = post(RECEIPT_URL, {"ids": ticket_ids[i:i + 1000]})
        out.update(resp.get("data", {}))
    return out


def prune_tokens(db, ticket_to_token, receipts):
    """Mark a token inactive when its receipt is DeviceNotRegistered."""
    for ticket_id, rec in receipts.items():
        if rec.get("status") == "error" and \
                rec.get("details", {}).get("error") == "DeviceNotRegistered":
            token = ticket_to_token.get(ticket_id)
            if token:
                db.execute("UPDATE push_token SET active=0 WHERE token=?", (token,))
    db.commit()


def active_tokens(db):
    return [r[0] for r in db.execute(
        "SELECT token FROM push_token WHERE active=1")]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_softbook_push.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/softbook_push.py tests/test_softbook_push.py
git commit -m "feat(softbook-app): Expo Push API sender + token pruning"
```

---

### Task 9: Push worker — new High / reverse-middle / settlement triggers

**Files:**
- Create: `scripts/softbook_push_worker.py`
- Test: `tests/test_softbook_push.py` (add)

Runs every scan cycle. Sends a push for each NEW High pick (deduped via a `pushed_high` table so a
pick pushes once), and for settled `app_bet` rows not yet notified (`notified_settle`). Reverse-middle
warnings ride on the High push when an opposite-direction High exists (reuse `rec._no_both_win`).

- [ ] **Step 1: Write the failing test (add to the file)**

```python
# append to tests/test_softbook_push.py
import softbook_push_worker as worker  # noqa: E402


def _worker_dbs():
    ev = sqlite3.connect(":memory:")
    ev.execute("""CREATE TABLE softbook_ev(
        recorded_at TEXT, sport TEXT, event_id TEXT, commence_time TEXT, home TEXT,
        away TEXT, market TEXT, side TEXT, point REAL, player TEXT,
        pinnacle_fair_prob REAL, pinnacle_price REAL, best_soft_book TEXT,
        best_soft_price REAL, snapshot_ev REAL, n_books INTEGER, n_ev INTEGER,
        conf REAL, tier TEXT)""")
    # datetime('now') so the worker's '-1 day' recency filter always includes it.
    ev.execute("INSERT INTO softbook_ev VALUES (datetime('now'),"
               "'baseball_mlb','E1','2099-01-01T00:00:00Z','MIA','NYM',"
               "'batter_total_bases','Over',1.5,'Brandon Nimmo',0.45,2.0,"
               "'theScore',2.2,0.074,9,5,60,'High')")
    ev.commit()
    pdb = sqlite3.connect(":memory:")
    import softbook_app_db as adb
    adb.init_schema(pdb)
    pdb.execute("INSERT INTO push_token VALUES ('ExponentPushToken[x]','t',1)")
    pdb.commit()
    return ev, pdb


def test_worker_pushes_new_high_once():
    ev, pdb = _worker_dbs()
    sent = []
    worker.run_once(ev, pdb, send=lambda msgs: sent.extend(msgs) or [])
    assert len(sent) == 1
    assert "Nimmo" in sent[0]["title"] + sent[0]["body"]
    worker.run_once(ev, pdb, send=lambda msgs: sent.extend(msgs) or [])  # dedup
    assert len(sent) == 1


def test_worker_pushes_settlement_result_once():
    ev, pdb = _worker_dbs()
    pdb.execute("INSERT INTO app_bet(event_id,market,side,point,player,label,book,"
                "fill_odds,stake,settled,won,pnl,settled_at) VALUES "
                "('E1','batter_total_bases','Over',1.5,'Brandon Nimmo','x','FanDuel',"
                "2.2,2.0,1,1,2.4,'2026-06-12T22:00:00+00:00')")
    pdb.commit()
    sent = []
    worker.run_once(ev, pdb, send=lambda msgs: sent.extend(msgs) or [])
    settle = [m for m in sent if m.get("categoryId") == "settle"]
    assert len(settle) == 1 and "win" in settle[0]["body"].lower() or "적중" in settle[0]["body"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_softbook_push.py -k worker -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'softbook_push_worker'`.

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/softbook_push_worker.py
#!/usr/bin/env python3
"""Push worker for the +EV companion app — replaces the Telegram send path.

Each run: push every NEW High pick (deduped in pushed_high so a pick pushes
once; a moved line is a new pick = new push, like the recorder's high_alerted),
attach a reverse-middle warning when a same-game opposite-direction High exists,
and push settlement results for app_bet rows not yet notified.

Runs as a oneshot timer aligned to the scan cycle (kalshi-softbook-push.timer),
or inline after a scan. Fires 24/7 (no quiet window — the user bets at night).
"""
import datetime
import os
import sqlite3

import softbook_ev_core as core
import softbook_ev_recorder as rec
import softbook_app_db as adb
import softbook_app_feed as feed
import softbook_push as push

EV_DB = os.getenv("SOFTBOOK_EV_DB", "/home/ubuntu/kalshi-arb/data/softbook_ev.db")

PUSHED_HIGH = """CREATE TABLE IF NOT EXISTS pushed_high(
    event_id TEXT, market TEXT, side TEXT, point REAL, player TEXT,
    pushed_at TEXT, PRIMARY KEY (event_id, market, side, point, player))"""
NOTIFIED_SETTLE = """CREATE TABLE IF NOT EXISTS notified_settle(
    app_bet_id INTEGER PRIMARY KEY, notified_at TEXT)"""


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _latest_high(ev):
    cols = ("recorded_at, sport, event_id, commence_time, home, away, market,"
            " side, point, player, best_soft_book, best_soft_price, snapshot_ev,"
            " n_books, conf")
    rows = ev.execute(
        f"SELECT {cols} FROM softbook_ev WHERE tier='High' AND"
        " datetime(recorded_at) >= datetime('now','-1 day')").fetchall()
    keys = cols.replace(" ", "").split(",")
    return [dict(zip(keys, r)) for r in rows]


def _opp_note(rows, pick):
    this = {"market": pick["market"], "side": pick["side"],
            "point": pick["point"] or 0, "player": pick["player"] or "",
            "home": pick["home"], "away": pick["away"]}
    for r in rows:
        if r["event_id"] != pick["event_id"] or r["market"] == pick["market"]:
            continue
        other = {"market": r["market"], "side": r["side"], "point": r["point"] or 0,
                 "player": r["player"] or "", "home": r["home"], "away": r["away"]}
        if rec._no_both_win(this, other):
            return " ⚖️ 반대방향 High 있음 (동시적중 불가)"
    return ""


def _high_messages(ev, pdb, tokens, now):
    pdb.execute(PUSHED_HIGH)
    rows = _latest_high(ev)
    latest = max((r["recorded_at"] for r in rows), default=None)
    msgs = []
    for r in rows:
        if r["recorded_at"] != latest:
            continue
        pk = (r["event_id"], r["market"], r["side"], r["point"] or 0, r["player"] or "")
        cur = pdb.execute(
            "INSERT OR IGNORE INTO pushed_high VALUES (?,?,?,?,?,?)", (*pk, now))
        if not cur.rowcount:
            continue
        label = core.label(r["market"], r["side"],
                           None if r["market"] == "h2h" else r["point"],
                           r["player"] or None)
        pid = feed._encode_id(pk)
        body = (f"{label} · {r['best_soft_book']} {r['best_soft_price']:.2f}"
                f" · EV {(r['snapshot_ev'] or 0)*100:+.1f}%" + _opp_note(rows, r))
        for tok in tokens:
            msgs.append(push.build_message(tok, "🟣 New High +EV", body,
                                           data={"pick_id": pid}, category="high_pick"))
    pdb.commit()
    return msgs


def _settle_messages(pdb, tokens, now):
    pdb.execute(NOTIFIED_SETTLE)
    rows = pdb.execute(
        "SELECT a.id, a.label, a.won, a.pnl FROM app_bet a"
        " LEFT JOIN notified_settle n ON n.app_bet_id=a.id"
        " WHERE a.settled=1 AND n.app_bet_id IS NULL").fetchall()
    msgs = []
    for bid, label, won, pnl in rows:
        verdict = "✅ 적중" if won else "❌ 미적중"
        body = f"{label or ''} — {verdict} ({(pnl or 0):+.2f})"
        for tok in tokens:
            msgs.append(push.build_message(tok, "정산 결과", body,
                                           data={"app_bet_id": bid}, category="settle"))
        pdb.execute("INSERT OR IGNORE INTO notified_settle VALUES (?,?)", (bid, now))
    pdb.commit()
    return msgs


def run_once(ev, pdb, send=None):
    send = send or (lambda msgs: push.send(msgs))
    now = _now()
    tokens = push.active_tokens(pdb)
    if not tokens:
        return 0
    msgs = _high_messages(ev, pdb, tokens, now) + _settle_messages(pdb, tokens, now)
    if msgs:
        send(msgs)
    return len(msgs)


def main():
    ev = sqlite3.connect(f"file:{EV_DB}?mode=ro", uri=True, timeout=15)
    pdb = adb.connect()
    try:
        n = run_once(ev, pdb)
        print(f"[softbook-push] sent {n} messages")
    finally:
        ev.close()
        pdb.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_softbook_push.py -v`
Expected: PASS (all push + worker tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/softbook_push_worker.py tests/test_softbook_push.py
git commit -m "feat(softbook-app): push worker — new High + reverse-middle + settlement"
```

---

### Task 10: Live in-game grading poller

**Files:**
- Create: `scripts/softbook_live_grade.py`
- Test: `tests/test_softbook_live_grade.py`

For each open MLB `app_bet`, fetch the player's current stat from the public MLB StatsAPI live feed,
compute progress vs the line, and project status. App-side + advisory (final settlement stays with
the existing backfill). MLB-first; NBA is a follow-up (clear seam left). The grading math is pure;
the StatsAPI fetch is injected for tests.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_softbook_live_grade.py
"""Live in-game grading: project an open prop bet's status from the player's
current stat vs the line. Pure math + an injected stat fetcher."""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import softbook_live_grade as lg  # noqa: E402


def test_project_over_winning_when_stat_reaches_line():
    out = lg.project("Over", 1.5, current=2, game_state="Mid 2nd")
    assert out["live_status"] == "winning"
    assert out["progress"]["current"] == 2 and out["progress"]["needed"] == 1.5


def test_project_over_pending_when_below_line():
    out = lg.project("Over", 1.5, current=0, game_state="Mid 2nd")
    assert out["live_status"] == "losing"          # below line, game ongoing
    assert out["progress"]["remaining"] == 2       # ceil(1.5)-0 to clear


def test_project_under_winning_while_below_line():
    out = lg.project("Under", 1.5, current=1, game_state="Top 5th")
    assert out["live_status"] == "winning"


def test_project_returns_pending_when_stat_unknown():
    out = lg.project("Over", 1.5, current=None, game_state=None)
    assert out["live_status"] == "pending"


def test_grade_open_bets_updates_db():
    db = sqlite3.connect(":memory:")
    import softbook_app_db as adb
    adb.init_schema(db)
    db.execute("INSERT INTO app_bet(event_id,sport,market,side,point,player,book,"
               "fill_odds,stake,settled) VALUES ('E1','baseball_mlb',"
               "'batter_total_bases','Over',1.5,'Brandon Nimmo','FanDuel',2.2,2.0,0)")
    db.commit()

    def fake_stat(sport, event_id, player, market):
        return 2, "Mid 2nd"        # current total bases, game state

    n = lg.grade_open_bets(db, fetch_stat=fake_stat)
    assert n == 1
    row = db.execute("SELECT live_status, live_progress FROM app_bet").fetchone()
    assert row[0] == "winning"
    assert json.loads(row[1])["current"] == 2


def test_grade_skips_settled_bets():
    db = sqlite3.connect(":memory:")
    import softbook_app_db as adb
    adb.init_schema(db)
    db.execute("INSERT INTO app_bet(event_id,sport,market,side,point,player,settled)"
               " VALUES ('E1','baseball_mlb','batter_total_bases','Over',1.5,'X',1)")
    db.commit()
    assert lg.grade_open_bets(db, fetch_stat=lambda *a: (5, "Final")) == 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_softbook_live_grade.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'softbook_live_grade'`.

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/softbook_live_grade.py
#!/usr/bin/env python3
"""Live in-game grading for open app_bet rows (app-side, advisory).

Joins each open bet to a live sports feed (MLB StatsAPI, public/no-auth) to read
the player's CURRENT stat, then projects status vs the line for the progress ring
("0 of 2 total bases, Mid 2nd"). Final win/loss still comes from the existing
settlement backfill — projected status is advisory and reconciled there (handles
push/void/scratch/rain). MLB-first; NBA fetch is a clear follow-up seam.

Runs as kalshi-softbook-live-grade.timer (~30-60s during games).
"""
import json
import math
import os
import sqlite3
import urllib.request

import softbook_app_db as adb

# market -> MLB StatsAPI boxscore stat path (batting/pitching stat name)
MLB_STAT = {
    "batter_total_bases": ("batting", "totalBases"),
    "batter_home_runs": ("batting", "homeRuns"),
    "pitcher_strikeouts": ("pitching", "strikeOuts"),
    "pitcher_outs": ("pitching", "outs"),
    "pitcher_earned_runs": ("pitching", "earnedRuns"),
    "pitcher_hits_allowed": ("pitching", "hits"),
}


def project(side, point, current, game_state):
    """Pure: project live_status + progress dict from current stat vs line."""
    if current is None:
        return {"live_status": "pending",
                "progress": {"current": None, "needed": point,
                             "game_state": game_state}}
    over = side == "Over"
    if over:
        status = "winning" if current > point else "losing"
        remaining = max(0, math.ceil(point) - current) if current <= point else 0
    else:
        status = "winning" if current < point else "losing"
        remaining = 0
    return {"live_status": status,
            "progress": {"current": current, "needed": point,
                         "remaining": remaining, "game_state": game_state}}


def _mlb_live_stat(sport, event_id, player, market):
    """Fetch a player's current stat from the MLB StatsAPI live feed. event_id is
    the Odds API event id, NOT the MLB gamePk — map by team+date upstream; here we
    accept a gamePk passed through event_id when numeric, else return unknown.
    (The team/date->gamePk mapping is built in grade_open_bets' fetcher wiring.)"""
    sp = MLB_STAT.get(market)
    if not sp or sport != "baseball_mlb" or not str(event_id).isdigit():
        return None, None
    group, stat = sp
    url = f"https://statsapi.mlb.com/api/v1.1/game/{event_id}/feed/live"
    try:
        data = json.loads(urllib.request.urlopen(url, timeout=15).read())
    except Exception:
        return None, None
    ls = data.get("liveData", {})
    state = (data.get("gameData", {}).get("status", {}).get("detailedState"))
    box = ls.get("boxscore", {}).get("teams", {})
    for side in ("home", "away"):
        for pid, pdata in box.get(side, {}).get("players", {}).items():
            full = pdata.get("person", {}).get("fullName", "")
            if full.lower() == (player or "").lower():
                val = pdata.get("stats", {}).get(group, {}).get(stat)
                return (int(val) if val is not None else None), state
    return None, state


def grade_open_bets(db, fetch_stat=_mlb_live_stat):
    """Project status for every open app_bet; write live_status + live_progress.
    Returns the number of bets updated."""
    rows = db.execute(
        "SELECT id, sport, event_id, market, side, point, player FROM app_bet"
        " WHERE settled=0").fetchall()
    n = 0
    for bid, sport, eid, market, side, point, player in rows:
        if market not in MLB_STAT:           # NBA props: follow-up seam
            continue
        current, state = fetch_stat(sport, eid, player, market)
        proj = project(side, point, current, state)
        db.execute("UPDATE app_bet SET live_status=?, live_progress=? WHERE id=?",
                   (proj["live_status"], json.dumps(proj["progress"]), bid))
        n += 1
    db.commit()
    return n


def main():
    db = adb.connect()
    try:
        n = grade_open_bets(db)
        print(f"[softbook-live-grade] updated {n} open bets")
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_softbook_live_grade.py -v`
Expected: PASS (6 tests).

Note for the implementer: the Odds-API-event-id → MLB `gamePk` mapping is the one piece not covered
by unit tests (it needs the live MLB schedule). It is wired in `main()` later via a small
team+date→gamePk lookup against `https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=YYYY-MM-DD`;
until then `_mlb_live_stat` returns `pending` for non-numeric event ids, which is the safe default.
Capture this in the Open Questions follow-up — do not block the plan on it.

- [ ] **Step 5: Commit**

```bash
git add scripts/softbook_live_grade.py tests/test_softbook_live_grade.py
git commit -m "feat(softbook-app): live in-game grading poller (MLB StatsAPI)"
```

---

### Task 11: Deploy — systemd units + Cloudflare Tunnel + Access

**Files:**
- Create: `deploy/kalshi-softbook-api.service`
- Create: `deploy/kalshi-softbook-push.service`, `deploy/kalshi-softbook-push.timer`
- Create: `deploy/kalshi-softbook-live-grade.service`, `deploy/kalshi-softbook-live-grade.timer`
- Create: `deploy/cloudflared-config.yml`
- Create: `docs/setup-cloudflare-tunnel.md`

These are config + a runbook. No unit tests; verification is the live smoke test at the end.

- [ ] **Step 1: Create the API systemd unit**

```ini
# deploy/kalshi-softbook-api.service
[Unit]
Description=Soft-book +EV companion API (FastAPI/uvicorn, behind Cloudflare Tunnel)
After=network-online.target
Wants=network-online.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/kalshi-arb
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=/home/ubuntu/kalshi-arb/scripts
ExecStart=/home/ubuntu/kalshi-arb/venv/bin/uvicorn softbook_app_api:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5
MemoryMax=200M

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Create the push worker unit + timer**

```ini
# deploy/kalshi-softbook-push.service
[Unit]
Description=Soft-book +EV companion push worker (new High / reverse-middle / settlement)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=ubuntu
WorkingDirectory=/home/ubuntu/kalshi-arb
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/ubuntu/kalshi-arb/venv/bin/python scripts/softbook_push_worker.py
TimeoutStartSec=120
MemoryMax=150M
```

```ini
# deploy/kalshi-softbook-push.timer
[Unit]
Description=Run the soft-book push worker every 5 minutes (aligned to scans)

[Timer]
OnCalendar=*:0/5
Persistent=false

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Create the live-grade unit + timer**

```ini
# deploy/kalshi-softbook-live-grade.service
[Unit]
Description=Soft-book +EV companion live in-game bet grading (MLB StatsAPI)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=ubuntu
WorkingDirectory=/home/ubuntu/kalshi-arb
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/ubuntu/kalshi-arb/venv/bin/python scripts/softbook_live_grade.py
TimeoutStartSec=120
MemoryMax=150M
```

```ini
# deploy/kalshi-softbook-live-grade.timer
[Unit]
Description=Run live bet grading every minute

[Timer]
OnCalendar=*:*:0/60
Persistent=false

[Install]
WantedBy=timers.target
```

- [ ] **Step 4: Create the cloudflared config template**

```yaml
# deploy/cloudflared-config.yml
# Install to /etc/cloudflared/config.yml on EC2 (root). Replace <TUNNEL_UUID> and
# the hostname. cloudflared runs as root (HOME=/root), so the config + credentials
# file must live under /etc/cloudflared, not ~/.cloudflared.
tunnel: <TUNNEL_UUID>
credentials-file: /etc/cloudflared/<TUNNEL_UUID>.json
ingress:
  - hostname: api.<your-domain>
    service: http://localhost:8000
  - service: http_status:404
```

- [ ] **Step 5: Write the runbook**

````markdown
# docs/setup-cloudflare-tunnel.md
# Cloudflare Tunnel + Access for the +EV companion API

Goal: expose the FastAPI service (localhost:8000 on EC2) to the iOS app with ZERO
inbound ports open, authenticated by a Cloudflare Access service token.

## 1. Domain
Buy a domain at Cloudflare Registrar (~$10/yr) or move an existing one to
Cloudflare Free DNS.

## 2. Install cloudflared on EC2
```bash
ssh -i /Users/taejunoh/Desktop/LFG/pem/kalshi-bot.pem ubuntu@3.146.180.145
sudo mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | \
  sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" | \
  sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt-get update && sudo apt-get install -y cloudflared
```

## 3. Create + route the tunnel
```bash
cloudflared tunnel login                       # browser auth, pick your domain
cloudflared tunnel create softbook-api         # writes ~/.cloudflared/<UUID>.json
cloudflared tunnel route dns softbook-api api.<your-domain>
sudo mkdir -p /etc/cloudflared
sudo cp ~/.cloudflared/<UUID>.json /etc/cloudflared/
sudo cp /home/ubuntu/kalshi-arb/deploy/cloudflared-config.yml /etc/cloudflared/config.yml
sudo sed -i 's/<TUNNEL_UUID>/<UUID>/g; s/<your-domain>/example.com/g' /etc/cloudflared/config.yml
sudo cloudflared --config /etc/cloudflared/config.yml service install
sudo systemctl enable --now cloudflared
```

## 4. Cloudflare Access (service token)
In Zero Trust dashboard:
1. Access > Service Auth > Service Tokens > Create. Save Client Id + Secret
   (secret shown once).
2. Access > Applications > Add a self-hosted app for `api.<your-domain>`.
3. Add ONE policy: Action = **Service Auth**, Include = the service token.
   (Optional 2nd policy: Action=Allow, Include=One-Time PIN + your email, for
   browser access.)
4. Copy the application **AUD** tag and your team domain
   (`<team>.cloudflareaccess.com`).

## 5. EC2 .env additions
```
APP_API_TOKEN=<long random string>
CF_ACCESS_TEAM_DOMAIN=<team>.cloudflareaccess.com
CF_ACCESS_AUD=<application AUD tag>
```

## 6. Close inbound ports
The tunnel is outbound-only. Remove inbound API rules from the EC2 security group
and ufw (keep your SSH rule). Break-glass: re-add a temporary SG rule, or install
Tailscale, if Cloudflare has an incident.

## 7. Install + start the app services
```bash
cd /home/ubuntu/kalshi-arb && git pull
sudo cp deploy/kalshi-softbook-api.service /etc/systemd/system/
sudo cp deploy/kalshi-softbook-push.{service,timer} /etc/systemd/system/
sudo cp deploy/kalshi-softbook-live-grade.{service,timer} /etc/systemd/system/
venv/bin/pip install -r requirements.txt        # picks up PyJWT
sudo systemctl daemon-reload
sudo systemctl enable --now kalshi-softbook-api.service
sudo systemctl enable --now kalshi-softbook-push.timer
sudo systemctl enable --now kalshi-softbook-live-grade.timer
```

## 8. Smoke test (from your laptop)
```bash
curl -s https://api.<your-domain>/picks \
  -H "Authorization: Bearer <APP_API_TOKEN>" \
  -H "CF-Access-Client-Id: <client-id>" \
  -H "CF-Access-Client-Secret: <client-secret>" | head
```
Expect a JSON `{"picks":[...],"counts":{...}}`. A 401 = bad bearer; a Cloudflare
login page = the Access policy isn't Service Auth.
````

- [ ] **Step 6: Run the full backend test suite**

Run: `python -m pytest tests/test_softbook_app_db.py tests/test_softbook_deeplink.py tests/test_softbook_app_feed.py tests/test_softbook_app_api.py tests/test_softbook_push.py tests/test_softbook_live_grade.py -v`
Expected: PASS (all).

- [ ] **Step 7: Commit**

```bash
git add deploy/kalshi-softbook-api.service deploy/kalshi-softbook-push.service \
  deploy/kalshi-softbook-push.timer deploy/kalshi-softbook-live-grade.service \
  deploy/kalshi-softbook-live-grade.timer deploy/cloudflared-config.yml \
  docs/setup-cloudflare-tunnel.md
git commit -m "feat(softbook-app): systemd units + Cloudflare Tunnel/Access deploy runbook"
```

---

## Deferred to Plan 2 (iOS Expo app) — not in this plan

- Expo/React Native app: 픽 feed (filters), pick detail (on-demand refresh + deep-link open via
  `Linking.openURL`), one-tap confirm capture sheet, 내 베팅 (calendar + live), settings.
- `getExpoPushTokenAsync` registration → `POST /push/register`; actionable notification categories
  ("✓ 베팅함" / "✕ 관심없음").
- On-device deep-link verification checklist (FanDuel/DraftKings prefill actually populates the slip
  on the NY iPhone before showing the "prefill" label).
- TestFlight build + install.

## Deferred to Phase 2 (per spec) — not in this plan

- Screenshot AI capture (`POST /bets/scan` + vision model).
- Price-move / cashout push triggers.
- Filter-rule persistence beyond `filter_prefs` (saved mute rules).

## Self-Review

Spec coverage (Phase 1 backend slice):
- Picks feed + filters → Tasks 3, 4, 7. Pick lifecycle (bet/dismiss/cooled/expired) → Task 3 + 6.
- On-demand odds refetch → Task 5. Tiered deep-link → Task 2 (+ wired in 5).
- Capture (one-tap) → Task 6 (`POST /bets`). PnL source = app_bet → Tasks 1, 7.
- Live tracking/auto-grade → Task 10. Push (new High/reverse-middle/settlement) → Tasks 8, 9.
- Security (Tunnel + Access, zero inbound, JWT validation) → Tasks 4, 11. `real_fill` migration → Task 1.
- iOS app, screenshot AI, price-move/cashout push → explicitly deferred (Plan 2 / Phase 2).

Type/name consistency: `_encode_id`/`decode_id` (feed) used by API Tasks 4-6 and worker Task 9;
`adb.set_pick_state`/`pick_states`/`insert_app_bet` signatures match across Tasks 1, 6, 9;
`push.build_message`/`send`/`active_tokens` match across Tasks 8, 9; `rec._no_both_win` reused in
Tasks 6, 9 with the `{market,side,point,player,home,away}` dict shape it expects.

Known non-unit-tested seam (documented, not a blocker): Odds-API-event-id → MLB `gamePk` mapping in
`softbook_live_grade.main()` (Task 10 note).
