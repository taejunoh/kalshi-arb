# +EV Companion App — Phase 2 (Plan 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close out Phase 1 of the +EV companion app (on-device badge/filter verification, deep-link prefill verification, Telegram decommission runbook) and ship Phase 2 — screenshot-AI capture for off-feed bets, price-move/cashout push, saved mute-rules, and swipe-to-dismiss polish.

**Architecture:** Extends the existing two-subsystem split with **no new boundary**. Backend = the FastAPI service (`scripts/softbook_app_api.py`) + the 5-min push worker (`scripts/softbook_push_worker.py`) over `data/softbook_paper.db`; app = the Expo SDK 56 client in `app/`. Phase 2 adds (a) a new `POST /bets/scan` endpoint that calls Claude vision to parse a bet-slip image into a `BetIn`-shaped draft the user confirms (never auto-inserts — the human-in-the-loop one-tap path is unchanged), (b) a price-move detector folded into the existing push worker, (c) a `mute_rule` table + `/rules` CRUD that the server-side feed composer honours, and (d) a swipe gesture on the pick card. All four are additive: every new column is added with an idempotent `ALTER`, every new endpoint is auth-gated like the rest, and the app stays a thin typed client.

**Tech Stack:** Python 3 + FastAPI + `urllib.request`/`httpx` (existing) **+ `anthropic` SDK (new)** on the backend; Expo SDK 56 / React Native / React 19 / TypeScript 6 / jest-expo on the app, **+ `expo-image-picker` and `react-native-gesture-handler` (new, via `npx expo install`)**. Vision model: **`claude-opus-4-8`** (the skill-mandated default; vision-capable, 1M context, ~cents/scan at personal volume).

---

## Scope & Decomposition

**In scope (this plan):**

- **Part A — Phase-1 closeout & soak** (mostly manual / ops; minimal code):
  - On-device verification of the 🟣/⚪ first-entry badge + "first-entry only" filter on TestFlight v1.0.1.
  - Deep-link prefill on-device verification → populate `VERIFIED_PREFILL_BOOKS` in `app/src/config.ts`.
  - Telegram decommission runbook (no-action-in-plan; executed in a later ops session **after** the soak).
- **Part B — Phase 2 (convenience & richness):**
  - Screenshot-AI capture for off-feed bets (`POST /bets/scan` + Claude vision; app camera/library capture → confirm sheet).
  - Price-move / cashout push for open bets.
  - Saved mute-rules / advanced triage (`mute_rule` table + `/rules` CRUD + a Rules editor screen; the server feed honours rules).
  - Polish: swipe-to-dismiss on the pick card.

**Out of scope (permanently or later):**

- Android (`app.json` stays `platforms: ["ios"]`).
- Credential-based auto bet-sync (BookSync / SharpSports) — permanent Non-Goal per the spec.
- A computed dollar "cashout value" — we do **not** have the book's cashout oracle, so price-move push reports the **odds move only** and frames a favourable move as a cashout *opportunity*; it never fabricates a buyout number.
- Re-architecting auth, the Cloudflare tunnel, or the live-grade/settle pipeline — Phase 1 owns those and they are untouched.

This plan produces working, testable software on its own: every backend task lands a green pytest against a seeded SQLite copy, every app task lands a green jest-expo suite, and `tsc` stays clean. Tasks 3–15 are fully codeable and testable offline with mocks. Tasks 1–2 and the runbook need the human (device, real bets, ops) and are marked accordingly.

---

## The contracts this plan extends (ground truth — verified against master `8269042`)

### Existing backend (do not change the shape — extend only)

`scripts/softbook_app_api.py` — FastAPI, every route behind `require_auth` (bearer `APP_API_TOKEN` + optional Cloudflare Access JWT):

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/picks` | — | `{picks:[Pick], counts:{str:int}, cooled:[Pick]}` |
| GET | `/picks/{id}` | — | `Pick` or 404 |
| GET | `/picks/{id}/refresh` | — | `{fair:float\|null, books:[BookQuote], credits:int}` |
| POST | `/bets` | `BetIn` | `{id:int}` |
| POST | `/picks/{id}/dismiss` | — | `{ok:true}` |
| GET | `/mybets` | — | `{calendar:{date:pnl}, open:[OpenBet]}` |
| GET | `/settings` | — | `SettingsOut` |
| PUT | `/settings` | `SettingsIn` | `{ok:true}` |
| POST | `/push/register` | `{token:str}` | `{ok:true}` |

`BetIn` (Pydantic, `scripts/softbook_app_api.py`): `event_id:str` (req), `market:str` (req), `side:str` (req), and optional `sport, home, away, commence_time, point(=0), player(=""), label, book, rec_odds, fill_odds, stake`, plus `source:str = "one_tap"`.

Module aliases inside `softbook_app_api.py`: `adb` = `softbook_app_db`, `core` = `softbook_ev_core`, `feed` = `softbook_app_feed`, `deeplink` = `softbook_deeplink`. Helpers: `_now()` → UTC ISO string; `core.event_odds(sport, event_id, key, markets)` → `(data, credits)`; `core.line_quotes(data, market, side, point, player)` → `(fair, [(book_label, price), …])`; `_markets_for_pick(market)`; `feed.decode_id(id)` → `(event_id, market, side, point, player)`; `feed._encode_id(...)`; `adb.connect()`, `adb.init_schema(db)`, `adb.insert_app_bet(db, bet)`, `adb.set_pick_state(db, key, state, ts)`, `adb.pick_states(db)`.

`scripts/softbook_app_db.py` — `data/softbook_paper.db` (env `SOFTBOOK_PAPER_DB`). Tables (`CREATE TABLE IF NOT EXISTS`, all created in `init_schema(db)`, called on every `connect()`):

```sql
app_bet(id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT, sport TEXT, home TEXT, away TEXT,
        commence_time TEXT, market TEXT, side TEXT, point REAL, player TEXT, label TEXT,
        book TEXT, rec_odds REAL, fill_odds REAL, stake REAL, placed_at TEXT, source TEXT,
        live_status TEXT DEFAULT 'pending', live_progress TEXT,
        settled INTEGER DEFAULT 0, won INTEGER, pnl REAL, settled_at TEXT)
pick_state(event_id TEXT, market TEXT, side TEXT, point REAL, player TEXT,
           state TEXT NOT NULL, updated_at TEXT,
           PRIMARY KEY (event_id, market, side, point, player))
filter_prefs(id INTEGER PRIMARY KEY CHECK (id=1), min_ev REAL DEFAULT 0.0,
             sports_json TEXT, muted_books_json TEXT, sort TEXT DEFAULT 'ev')
push_token(token TEXT PRIMARY KEY, updated_at TEXT, active INTEGER DEFAULT 1)
```

`insert_app_bet(db, bet)` inserts the columns `APP_BET_COLS` = `event_id,sport,home,away,commence_time,market,side,point,player,label,book,rec_odds,fill_odds,stake,placed_at,source` and returns `lastrowid`. **Ledger rule (load-bearing): `app_bet` rows are bet records — never DELETE or UPDATE-for-hygiene; segment instead.**

`scripts/softbook_push_worker.py` — `run_once(ev, pdb, send=None)` builds `_high_messages(...)` + `_settle_messages(...)`, then `push.send(msgs)`. Dedup tables `pushed_high(event_id,market,side,point,player PK)` and `notified_settle(app_bet_id PK, notified_at)`. Categories `"high_pick"` (data `{pick_id}`) and `"settle"` (data `{app_bet_id}`). `push.build_message(token,title,body,data=None,category=None)`, `push.active_tokens(db)`, `push.send(messages, post=...)`. Timer `deploy/kalshi-softbook-push.timer` = `OnCalendar=*:0/5` (every 5 min), 24/7.

`scripts/softbook_ev.db` (env `SOFTBOOK_EV_DB`), table `softbook_ev(recorded_at, sport, event_id, commence_time, home, away, market, side, point, player, pinnacle_fair_prob, pinnacle_price, best_soft_book, best_soft_price, snapshot_ev, n_books, n_ev, conf, tier)`. Used read-only for event lookup.

### Existing app (`app/src`)

- `config.ts` — `CATEGORY_HIGH="high_pick"`, `CATEGORY_SETTLE="settle"`, `ACTION_BET="BET_PLACED"`, `ACTION_DISMISS="NOT_INTERESTED"`, `ALL_BOOKS=[…8 NY books…]`, `SPORT_LABELS`, `VERIFIED_PREFILL_BOOKS:string[]=[]`, `DEFAULT_SETTINGS`, `MIN_EV_STEPS`.
- `api/types.ts` — `Pick, PicksResponse, Deeplink, BookQuote, RefreshResponse, BetIn, LiveProgress, OpenBet, MyBets, Settings, SortKey, PickStatus`.
- `api/client.ts` — `api.{getPicks, getPick, refreshPick, postBet, dismissPick, getMyBets, getSettings, putSettings, registerPush}`; `request<T>(path, init)` attaches bearer + CF headers from Keychain (`storage.ts`).
- `capture.ts` — `buildBetIn(pick, inputs): BetIn` (`source:"one_tap"`).
- `deeplink.ts` — `isVerifiedPrefill(book, dl)` = `dl.tier==="prefill" && VERIFIED_PREFILL_BOOKS.includes(book)`; `openOrCopy(dl)`.
- `push.ts` — `notifTarget(response): NotifTarget` (maps `ACTION_BET`→capture, `ACTION_DISMISS`→dismiss, `data.pick_id`→pickDetail, `data.app_bet_id`→mybets), `registerCategories()`, `registerForPush()`.
- `PushBridge.tsx` — `runTarget(target, deps)`; `navigation/index.tsx` — `captureRoute(id)`, `pickDetailRoute(id)`, `myBetsRoute()`.
- Screens: `PicksScreen`, `PickDetailScreen`, `CaptureSheet`, `MyBetsScreen`, `SettingsScreen`; `components/PickCard.tsx`.
- Tests: jest-expo, colocated `*.test.ts(x)`; mock expo/navigation modules **before** import; `await render`/`await fireEvent` (RNTL v14 async); fixtures as module-scope `const`.

### New contracts this plan introduces (locked — every task below uses exactly these names)

- **Endpoint** `POST /bets/scan` → request `{image_base64:str, media_type:str="image/jpeg"}`; response `ScanDraft` = `{bet: BetInDraft, matched:bool, notes:str}` where `BetInDraft` is the `BetIn` shape with all fields optional except none are required at this stage (the app fills/edits, then POSTs to existing `/bets`). New `BetIn.source` value when the user confirms a scanned bet: `"scan"`.
- **Module** `scripts/softbook_scan.py` — `parse_bet_slip(image_b64, media_type, client=None) -> dict` (Claude vision; injectable client for tests), `SCAN_SCHEMA` (raw JSON schema), `match_event(ev_db, draft) -> str|None`.
- **Env / dep** — `ANTHROPIC_API_KEY` in `.env`/`.env.example`; `anthropic` in `requirements.txt`. Model id `claude-opus-4-8`.
- **app_bet columns (new, idempotent ALTER)** — `current_best_odds REAL`, `last_odds_check_at TEXT`.
- **Dedup table** `pushed_price_move(app_bet_id INTEGER, kind TEXT, notified_at TEXT, PRIMARY KEY(app_bet_id, kind))`, `kind ∈ {'move','cashout'}`.
- **Push category** `CATEGORY_PRICE_MOVE="price_move"` (backend constant + app constant), data `{app_bet_id:int}` (routes to MyBets via the existing `data.app_bet_id` path — no new action buttons).
- **Table** `mute_rule(id INTEGER PRIMARY KEY AUTOINCREMENT, field TEXT, value TEXT, created_at TEXT)`, `field ∈ {'player','market','sport','event'}`.
- **Endpoints** `GET /rules` → `{rules:[MuteRule]}`; `POST /rules` (`{field,value}`) → `{id:int}`; `DELETE /rules/{id}` → `{ok:true}`.
- **App types** `ScanDraft`, `BetInDraft`, `MuteRule`; **app api** `api.scanBet(image_base64, media_type)`, `api.getRules()`, `api.addRule(field,value)`, `api.deleteRule(id)`.

---

## Manual Prerequisites (the human does these — NOT code tasks)

1. **Anthropic API key.** Add `ANTHROPIC_API_KEY=sk-ant-…` to EC2 `/home/ubuntu/kalshi-arb/.env` (and a placeholder line to `.env.example`). The key is the user's own Anthropic key; the scan endpoint 500s without it. Confirm `python -c "import anthropic"` succeeds in the EC2 venv after Task 3's `pip install`.
2. **TestFlight v1.0.1 on the NY iPhone** with FanDuel + DraftKings installed and logged in — required for Tasks 1 and 2 (on-device verification).
3. **Backend live** (already true on master `8269042`): `api.keepmypick.com` up, `kalshi-softbook-api.service` + `kalshi-softbook-push.timer` running. Deploy = `git fetch && git reset --hard origin/master`; **restart `kalshi-softbook-api.service` after any `scripts/softbook_app_api.py` / `softbook_scan.py` change** (the push worker + live-grade are oneshot timers, no restart needed).

---

## File Structure

```
scripts/
  softbook_scan.py            (NEW)  parse_bet_slip + SCAN_SCHEMA + match_event  — Claude vision
  softbook_app_api.py         (MOD)  + POST /bets/scan, + /rules CRUD, + ScanIn/RuleIn models
  softbook_app_db.py          (MOD)  + ALTER app_bet cols, + pushed_price_move, + mute_rule, + helpers
  softbook_push_worker.py     (MOD)  + _price_move_messages() wired into run_once
  softbook_app_feed.py         (MOD)  compose() filters picks matching active mute_rule
  softbook_ev_core.py          (unchanged — reused: event_odds, line_quotes)
requirements.txt              (MOD)  + anthropic
.env.example                  (MOD)  + ANTHROPIC_API_KEY
tests/
  test_softbook_scan.py            (NEW)
  test_softbook_scan_endpoint.py   (NEW)
  test_softbook_price_move.py      (NEW)
  test_softbook_mute_rules.py      (NEW)
app/src/
  config.ts                   (MOD)  + CATEGORY_PRICE_MOVE, + VERIFIED_PREFILL_BOOKS edit (Task 2)
  api/types.ts                (MOD)  + ScanDraft, BetInDraft, MuteRule
  api/client.ts               (MOD)  + scanBet, getRules, addRule, deleteRule
  scan.ts                     (NEW)  pickImage() wrapper + draftToCaptureParams()
  scan.test.ts                (NEW)
  push.ts                     (MOD)  notifTarget already routes app_bet_id; + CATEGORY_PRICE_MOVE registration
  screens/CaptureSheet.tsx    (MOD)  accept scan-draft route params (prefill from a scan)
  screens/RulesScreen.tsx     (NEW)  mute-rule editor
  screens/RulesScreen.test.tsx(NEW)
  screens/SettingsScreen.tsx  (MOD)  + "트리아지 규칙" row → RulesScreen
  screens/PicksScreen.tsx     (MOD)  + camera/library capture entry (→ scan → CaptureSheet)
  components/PickCard.tsx     (MOD)  swipe-to-dismiss
  navigation/index.tsx        (MOD)  + Rules route, + scan params on CaptureSheet
  navigation/types.ts         (MOD)  + Rules route, + CaptureSheet scan params
```

## Conventions (match these exactly)

1. **Versions are pinned by the existing lockfiles.** Add native deps with `cd app && npx expo install expo-image-picker react-native-gesture-handler` — never raw `npm install` (Expo SDK 56 compatibility).
2. **Backend tests** use a **seeded copy** of `softbook_paper.db` via the `SOFTBOOK_PAPER_DB` / `SOFTBOOK_EV_DB` env vars pointed at a `tmp_path` SQLite file — never the live `data/bot.db` (7 GB). Pattern: set env → `adb.init_schema(adb.connect())` → seed rows → call under test. Use FastAPI `TestClient(app)` with `headers={"Authorization": f"Bearer {os.environ['APP_API_TOKEN']}"}`.
3. **App tests** are colocated `*.test.ts(x)`, run with `cd app && npx jest <path> --watchAll=false`. Mock expo/navigation modules **before** import. RNTL v14: `await render(...)` and `await fireEvent...(...)` (un-awaited calls leave dangling promises that poison the next test). Circular mocks use `jest.requireActual`.
4. **Commits:** `feat(softbook-app): <what>` / `feat(softbook): <what>` for pure-backend; explicit paths in `git add` (the user may have parallel uncommitted edits — never `git add .`).
5. **No URL hand-building** — deep links come from the server `deeplink.build()`; the scan endpoint never invents an `event_id` it can't match.
6. **Honesty over coverage** — the scan confirm sheet shows what the model extracted and lets the user correct it; price-move push reports the odds move, never a fabricated cashout dollar amount; mute rules filter the feed but never delete `app_bet` rows.
7. **Restart the API after backend-API edits** (see Prerequisite 3). Push-worker / db-module edits ride the next 5-min oneshot — no restart.

---

## Part A — Phase-1 Closeout & Soak

These finish Phase 1. Tasks 1 and the verification half of Task 2 are **manual** (need the device + real bets); the Telegram decommission is a **no-action-in-plan runbook** executed later. Only the `VERIFIED_PREFILL_BOOKS` edit and its regression test are code.

### Task 1: On-device badge + filter verification (MANUAL — no code)

**Files:** none (verification only).

This confirms the 6/13 first-entry work (`8269042`, `6715a88`) renders correctly on the shipped build.

- [ ] **Step 1: Update the device build.** On the NY iPhone, open TestFlight → update `@allinsimple/softbook-ev` to v1.0.1 (build #2). Launch; confirm the picks feed loads (credentials already in Keychain from Phase 1).

- [ ] **Step 2: Verify the first-entry badge.** With at least one first-entry High pick live (or wait for one), confirm the pick card shows the **🟣 (first-entry)** badge and a late-crosser High shows **⚪**. If no first-entry pick is live, send a test push or wait for the next `*:0/5` scan; do not fake data.

- [ ] **Step 3: Verify the "first-entry only" filter.** Toggle the first-entry filter in the picks filter bar; confirm only 🟣 picks remain and the count updates. Toggle off; confirm ⚪ picks return.

- [ ] **Step 4: Record the result.** If badge + filter both render correctly, note it in the PR/commit description for this plan ("Task 1: on-device badge+filter verified on v1.0.1 build #2, <date>"). If either is wrong, **stop and file a bug** — it's a Phase-1 regression, not a Phase-2 task; do not proceed to Part B until the feed renders correctly, since Phase 2 builds on it.

### Task 2: Deep-link prefill verification → `VERIFIED_PREFILL_BOOKS`

**Files:**
- Manual: on-device verification per `app/DEEPLINK_VERIFICATION.md`.
- Modify: `app/src/config.ts` (only if a book verifies).
- Test: `app/src/deeplink.test.ts` (regression lock on the label gate).

The server already returns `tier:"prefill"` links for FanDuel/DraftKings, but a prefill link only proves the *app opens* — not that the *slip fills*. `VERIFIED_PREFILL_BOOKS` is the honest gate: empty today, so every row shows "앱 열기 + 복사". A book is added **only** after the slip is confirmed to pre-fill on the real device.

- [ ] **Step 1: Verify on device (manual).** Follow `app/DEEPLINK_VERIFICATION.md`: open a High pick → 픽 상세 → tap the FanDuel row, then the DraftKings row. For each, confirm the sportsbook app opens **and the exact selection + line is pre-loaded in the bet slip** (not the app home / event page). Use real single-leg picks via the system handler (`Linking.openURL`), never an in-app webview.

- [ ] **Step 2: Edit `config.ts` for whichever books passed.** If FanDuel pre-filled, set the list to `["FanDuel"]`; if both, `["FanDuel","DraftKings"]`; if neither, **leave `[]`** (the honest fallback is correct UX). Exact edit:

```typescript
// app/src/config.ts — replace the empty array with the verified set (or leave [] if none verified)
export const VERIFIED_PREFILL_BOOKS: string[] = ["FanDuel"];   // ← only books confirmed on-device in Step 1
```

- [ ] **Step 3: Add a regression test that the label gate honours the list.** Append to `app/src/deeplink.test.ts`:

```typescript
import { isVerifiedPrefill } from "./deeplink";
import { VERIFIED_PREFILL_BOOKS } from "./config";

const PREFILL_DL = { tier: "prefill" as const, url: "https://x", copy_text: "FanDuel · NYM ML @ 2.20" };
const EVENT_DL = { tier: "event" as const, url: "https://x", copy_text: "DraftKings · NYM ML @ 2.20" };

test("verified prefill label only shows for tier=prefill AND a verified book", () => {
  // A verified book with a prefill link → label is allowed
  for (const book of VERIFIED_PREFILL_BOOKS) {
    expect(isVerifiedPrefill(book, PREFILL_DL)).toBe(true);
  }
  // Any book with a non-prefill tier → never the verified label
  expect(isVerifiedPrefill(VERIFIED_PREFILL_BOOKS[0] ?? "FanDuel", EVENT_DL)).toBe(false);
  // An unverified book name → never the verified label even on a prefill link
  expect(isVerifiedPrefill("BetMGM", PREFILL_DL)).toBe(false);
});
```

- [ ] **Step 4: Run the test.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/deeplink.test.ts --watchAll=false`
Expected: PASS. (If `VERIFIED_PREFILL_BOOKS` is `[]`, the `for` loop is empty and the two negative assertions still pass — the test is valid either way.)

- [ ] **Step 5: Commit.**

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
git add app/src/config.ts app/src/deeplink.test.ts
git commit -m "feat(softbook-app): verified deep-link prefill books + label-gate regression test"
```

- [ ] **Step 6: Rebuild + re-upload to TestFlight only if `config.ts` changed.** `cd app && eas build -p ios --profile production --auto-submit --non-interactive` (credentials in EAS). If `VERIFIED_PREFILL_BOOKS` stayed `[]`, no rebuild is needed.

### Soak & Telegram decommission (NO-ACTION-IN-PLAN runbook)

During the soak, keep **both** the Telegram alert/fill-listener path **and** the app push path running in parallel so no alert is missed. The push worker (`kalshi-softbook-push.timer`) already replaces the alert path; in-app one-tap + scan capture replaces the fill-listener; `real_fill` rows were migrated to `app_bet` in Plan 1.

Decommission only **after** the soak confirms: pushes arrive, the feed loads, capture writes `app_bet`, `/mybets` shows PnL, and price-move push (Part B) is verified. Then, in a **separate ops session** (not this plan):

```bash
# EC2, after soak — disable the Telegram-side units (push worker + app capture supersede them)
sudo systemctl disable --now kalshi-softbook-fill-listener.service   # fill-listener superseded by in-app capture
# (Leave the alert-sender disabling for last; verify push parity for a full day first.)
```

Archiving the Telegram chat and retiring the bot script are user decisions, out of scope here. **Do not disable any Telegram unit inside this plan.**

## Part B — Phase 2

### Feature 1 — Screenshot-AI capture (Tasks 3–7)

The off-feed problem: ~5% of the user's bets are not from the High feed (the `pikkit/` slips in the repo are exactly these) and never reach `app_bet`, biasing realized-ROI measurement low. Screenshot-AI closes that gap: snap the bet-slip → Claude vision parses it → the user confirms in the existing capture sheet → it writes `app_bet` with `source:"scan"`. The endpoint **only parses** (never auto-inserts) — the human-in-the-loop confirm is preserved.

#### Task 3: Backend — `softbook_scan.parse_bet_slip` (Claude vision)

**Files:**
- Create: `scripts/softbook_scan.py`
- Create: `tests/test_softbook_scan.py`
- Modify: `requirements.txt` (add `anthropic`), `.env.example` (add `ANTHROPIC_API_KEY=`)

The vision call: `claude-opus-4-8`, base64 image block + extraction prompt, structured output via `output_config.format` (raw JSON schema; `json.loads` the text block), adaptive thinking. The Anthropic client is **injectable** so tests never hit the network.

- [ ] **Step 1: Write the failing test.** `tests/test_softbook_scan.py`:

```python
import json
import softbook_scan as scan


class _Blk:
    def __init__(self, t, text=None):
        self.type = t
        self.text = text


class _Resp:
    def __init__(self, content):
        self.content = content


class _Msgs:
    def __init__(self, payload):
        self._payload = payload
        self.kw = None

    def create(self, **kw):
        self.kw = kw
        # Claude may emit a thinking block before the text block — generator must skip it.
        return _Resp([_Blk("thinking"), _Blk("text", json.dumps(self._payload))])


class FakeClient:
    def __init__(self, payload):
        self.messages = _Msgs(payload)


def test_parse_bet_slip_returns_parsed_dict_and_sends_image():
    payload = {
        "sport": "baseball_mlb", "home": "COL", "away": "CHC",
        "market": "batter_total_bases", "side": "Over", "point": 1.5,
        "player": "Nico Hoerner", "book": "FanDuel", "fill_odds": 2.0,
        "stake": 2.0, "label": "Nico Hoerner TB Over 1.5", "notes": "single leg",
    }
    fake = FakeClient(payload)
    out = scan.parse_bet_slip("QkFTRTY0", media_type="image/png", client=fake)
    assert out == payload
    # The image was sent as a base64 block with the given media_type, model is opus-4-8.
    assert fake.messages.kw["model"] == "claude-opus-4-8"
    img = fake.messages.kw["messages"][0]["content"][0]
    assert img["type"] == "image"
    assert img["source"] == {"type": "base64", "media_type": "image/png", "data": "QkFTRTY0"}
    # Structured output is constrained by the schema.
    assert fake.messages.kw["output_config"]["format"]["type"] == "json_schema"
```

- [ ] **Step 2: Run it to verify it fails.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb && python -m pytest tests/test_softbook_scan.py::test_parse_bet_slip_returns_parsed_dict_and_sends_image -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'softbook_scan'`.

- [ ] **Step 3: Write the module.** `scripts/softbook_scan.py`:

```python
"""Parse a sports-betting bet-slip screenshot into a structured draft via Claude vision.

Off-feed bets (placed outside the High picks feed) never reach app_bet via one-tap
capture. This module lets the app snap the slip and get back a BetIn-shaped draft the
user confirms — closing the measurement gap without sportsbook credentials.

The Anthropic client is injectable (client=...) so tests run offline. The default client
reads ANTHROPIC_API_KEY from the environment; import is lazy so a missing key/package only
matters at real call time, not at import time."""

import json

MODEL = "claude-opus-4-8"   # skill-mandated default; vision-capable, ~cents/scan at personal volume

# Structured-output schema: every property listed in `required`, additionalProperties False,
# nullable via ["type","null"], no min/max constraints (unsupported by structured outputs).
SCAN_SCHEMA = {
    "type": "object",
    "properties": {
        "sport": {"type": ["string", "null"]},
        "home": {"type": ["string", "null"]},
        "away": {"type": ["string", "null"]},
        "market": {"type": ["string", "null"]},
        "side": {"type": ["string", "null"]},
        "point": {"type": ["number", "null"]},
        "player": {"type": ["string", "null"]},
        "book": {"type": ["string", "null"]},
        "fill_odds": {"type": ["number", "null"]},
        "stake": {"type": ["number", "null"]},
        "label": {"type": ["string", "null"]},
        "notes": {"type": "string"},
    },
    "required": ["sport", "home", "away", "market", "side", "point",
                 "player", "book", "fill_odds", "stake", "label", "notes"],
    "additionalProperties": False,
}

_PROMPT = (
    "This is a screenshot of a single-leg sports bet slip from a US sportsbook.\n"
    "Extract the bet into the schema. Conventions:\n"
    "- sport: an Odds-API-style key when identifiable (baseball_mlb, basketball_wnba, "
    "basketball_nba, icehockey_nhl, baseball_npb), else null.\n"
    "- market: an Odds-API market key (h2h, spread, total, batter_total_bases, "
    "batter_home_runs, pitcher_strikeouts, pitcher_outs), else null.\n"
    "- side: the outcome exactly as a book lists it (team name, Over, Under, Home, Away).\n"
    "- point: the line as a number (1.5, -5.5, 8.5) or null for moneyline.\n"
    "- fill_odds: DECIMAL odds (convert American: +150 -> 2.50, -170 -> 1.59).\n"
    "- stake: the wager amount in dollars (number only), or null if not shown.\n"
    "- home/away: the two team abbreviations or names if shown, else null.\n"
    "- label: a short human-readable description (e.g. 'Nico Hoerner TB Over 1.5').\n"
    "- notes: anything ambiguous or a second leg you are ignoring.\n"
    "Use null for any field you cannot read confidently. Do not guess odds."
)


def _default_client():
    import anthropic
    return anthropic.Anthropic()   # reads ANTHROPIC_API_KEY from the environment


def parse_bet_slip(image_b64, media_type="image/jpeg", client=None):
    """Return a dict matching SCAN_SCHEMA parsed from a base64 bet-slip image."""
    client = client or _default_client()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        thinking={"type": "adaptive"},
        output_config={"format": {"type": "json_schema", "schema": SCAN_SCHEMA}},
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                {"type": "text", "text": _PROMPT},
            ],
        }],
    )
    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text)
```

- [ ] **Step 4: Run the test to verify it passes.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb && python -m pytest tests/test_softbook_scan.py -q`
Expected: PASS (1 test).

- [ ] **Step 5: Add the dependency + env placeholder.** Append `anthropic` to `requirements.txt` (one line, no version pin to match the repo's style — or `anthropic>=0.40` if the repo pins). Append `ANTHROPIC_API_KEY=` to `.env.example`. Then on EC2 (deploy step): `cd /home/ubuntu/kalshi-arb && ./venv/bin/pip install anthropic` and add the real key to `.env`.

- [ ] **Step 6: Commit.**

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
git add scripts/softbook_scan.py tests/test_softbook_scan.py requirements.txt .env.example
git commit -m "feat(softbook): bet-slip vision parser (claude-opus-4-8, injectable client)"
```

#### Task 4: Backend — `softbook_scan.match_event` (extracted teams → event_id)

**Files:**
- Modify: `scripts/softbook_scan.py` (add `match_event`)
- Modify: `tests/test_softbook_scan.py` (add a seeded-db test)

A scanned bet matched to a known `event_id` gets live-grading + settlement; an unmatched off-feed bet still records (sport/home/away from the image), and settle skips it (no sport metadata) exactly like a migrated `real_fill` row. Matching is best-effort name-contains over recent `softbook_ev` events.

- [ ] **Step 1: Write the failing test.** Append to `tests/test_softbook_scan.py`:

```python
import sqlite3


def _seed_ev(path):
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE softbook_ev(recorded_at TEXT, sport TEXT, event_id TEXT, "
        "commence_time TEXT, home TEXT, away TEXT, market TEXT, side TEXT, point REAL, "
        "player TEXT, tier TEXT)")
    db.execute(
        "INSERT INTO softbook_ev(recorded_at, sport, event_id, commence_time, home, away) "
        "VALUES (datetime('now'), 'baseball_mlb', 'EVT123', datetime('now','+2 hours'), "
        "'Colorado Rockies', 'Chicago Cubs')")
    db.commit()
    return db


def test_match_event_finds_recent_event_by_team_names(tmp_path):
    db = _seed_ev(str(tmp_path / "ev.db"))
    assert scan.match_event(db, {"home": "COL Rockies", "away": "Chicago Cubs"}) == "EVT123"
    # No usable team names -> no match (avoid false positives).
    assert scan.match_event(db, {"home": None, "away": None}) is None
    # Names that don't overlap any recent event -> None.
    assert scan.match_event(db, {"home": "Boston", "away": "Yankees"}) is None
```

- [ ] **Step 2: Run it to verify it fails.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb && python -m pytest tests/test_softbook_scan.py::test_match_event_finds_recent_event_by_team_names -q`
Expected: FAIL — `AttributeError: module 'softbook_scan' has no attribute 'match_event'`.

- [ ] **Step 3: Add `match_event` to `scripts/softbook_scan.py`.**

```python
def _overlap(a, b):
    """Loose team-name match for slip→event: case-insensitive whole-string containment
    OR a shared significant token (length >= 4). Real slips show abbreviated/partial names
    ('COL Rockies') vs Odds-API full names ('Colorado Rockies'); the shared token ('rockies')
    bridges them. match_event requires BOTH legs to overlap, which guards false positives."""
    a = (a or "").strip().lower()
    b = (b or "").strip().lower()
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    ta = {t for t in a.split() if len(t) >= 4}
    tb = {t for t in b.split() if len(t) >= 4}
    return bool(ta & tb)


def match_event(ev_conn, draft):
    """Best-effort map a scanned draft to a recent softbook_ev event_id, or None.

    Requires BOTH home and away to overlap the same event to avoid false positives.
    Scans only events commencing within the last day forward (recent slate)."""
    home = draft.get("home")
    away = draft.get("away")
    if not (home and away):
        return None
    rows = ev_conn.execute(
        "SELECT DISTINCT event_id, home, away FROM softbook_ev "
        "WHERE datetime(commence_time) >= datetime('now','-1 day') "
        "ORDER BY recorded_at DESC LIMIT 500").fetchall()
    for eid, h, a in rows:
        if _overlap(home, h) and _overlap(away, a):
            return eid
    return None
```

- [ ] **Step 4: Run the test to verify it passes.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb && python -m pytest tests/test_softbook_scan.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit.**

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
git add scripts/softbook_scan.py tests/test_softbook_scan.py
git commit -m "feat(softbook): match scanned bet-slip to recent event by team names"
```

#### Task 5: Backend — `POST /bets/scan` endpoint

**Files:**
- Modify: `scripts/softbook_app_api.py` (add `ScanIn`, `import softbook_scan as scan`, the route)
- Create: `tests/test_softbook_scan_endpoint.py`

The endpoint parses (via the injectable module, monkeypatched in tests), matches the event, and returns a `{bet, matched, notes}` draft — it does **not** write. The app reviews and POSTs to the existing `/bets` with `source:"scan"`.

- [ ] **Step 1: Write the failing test.** `tests/test_softbook_scan_endpoint.py`:

```python
import os
import sqlite3
import pytest

os.environ.setdefault("APP_API_TOKEN", "test-token")
os.environ.pop("CF_ACCESS_TEAM_DOMAIN", None)   # disable CF check in tests


@pytest.fixture()
def client(tmp_path, monkeypatch):
    paper = str(tmp_path / "paper.db")
    ev = str(tmp_path / "ev.db")
    monkeypatch.setenv("SOFTBOOK_PAPER_DB", paper)
    monkeypatch.setenv("SOFTBOOK_EV_DB", ev)
    # Seed a recent event to match against.
    edb = sqlite3.connect(ev)
    edb.execute(
        "CREATE TABLE softbook_ev(recorded_at TEXT, sport TEXT, event_id TEXT, "
        "commence_time TEXT, home TEXT, away TEXT, market TEXT, side TEXT, point REAL, "
        "player TEXT, tier TEXT)")
    edb.execute(
        "INSERT INTO softbook_ev(recorded_at, sport, event_id, commence_time, home, away) "
        "VALUES (datetime('now'), 'baseball_mlb', 'EVT123', datetime('now','+2 hours'), "
        "'Colorado Rockies', 'Chicago Cubs')")
    edb.commit(); edb.close()

    import softbook_app_db as adb
    adb.init_schema(adb.connect())          # build app tables in the tmp paper db

    import softbook_app_api as api
    # Monkeypatch the vision parser — no network in tests.
    monkeypatch.setattr(api.scan, "parse_bet_slip", lambda *a, **k: {
        "sport": "baseball_mlb", "home": "Colorado Rockies", "away": "Chicago Cubs",
        "market": "batter_total_bases", "side": "Over", "point": 1.5,
        "player": "Nico Hoerner", "book": "FanDuel", "fill_odds": 2.0, "stake": 2.0,
        "label": "Nico Hoerner TB Over 1.5", "notes": "single leg",
    })
    from fastapi.testclient import TestClient
    c = TestClient(api.app)
    c.headers.update({"Authorization": "Bearer test-token"})
    return c


def test_scan_returns_draft_and_matches_event(client):
    r = client.post("/bets/scan", json={"image_base64": "QkFTRTY0", "media_type": "image/jpeg"})
    assert r.status_code == 200
    body = r.json()
    assert body["matched"] is True
    assert body["bet"]["event_id"] == "EVT123"
    assert body["bet"]["source"] == "scan"
    assert body["bet"]["player"] == "Nico Hoerner"
    assert body["bet"]["fill_odds"] == 2.0


def test_scan_unmatched_event_still_returns_draft(client, monkeypatch):
    import softbook_app_api as api
    monkeypatch.setattr(api.scan, "parse_bet_slip", lambda *a, **k: {
        "sport": None, "home": "Some Team", "away": "Other Team",
        "market": "h2h", "side": "Some Team", "point": None, "player": None,
        "book": "DraftKings", "fill_odds": 1.8, "stake": None,
        "label": "Some Team ML", "notes": "",
    })
    r = client.post("/bets/scan", json={"image_base64": "QkFTRTY0"})
    assert r.status_code == 200
    body = r.json()
    assert body["matched"] is False
    assert body["bet"]["event_id"] is None
    assert body["bet"]["source"] == "scan"


def test_scan_parse_failure_is_422(client, monkeypatch):
    import softbook_app_api as api
    def _boom(*a, **k):
        raise ValueError("unreadable")
    monkeypatch.setattr(api.scan, "parse_bet_slip", _boom)
    r = client.post("/bets/scan", json={"image_base64": "QkFTRTY0"})
    assert r.status_code == 422
```

- [ ] **Step 2: Run it to verify it fails.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb && python -m pytest tests/test_softbook_scan_endpoint.py -q`
Expected: FAIL — 404 on `/bets/scan` (route not defined) / `AttributeError` on `api.scan`.

- [ ] **Step 3: Add the import, model, and route to `scripts/softbook_app_api.py`.** Near the other `import` lines add `import softbook_scan as scan`. Near the other `BaseModel` request models add:

```python
class ScanIn(BaseModel):
    image_base64: str
    media_type: str = "image/jpeg"
```

Add the route next to `POST /bets` (reuse the existing `_ev_conn()` helper used by `_enrich_first_entry`):

```python
@app.post("/bets/scan")
def scan_bet(body: ScanIn, _=Depends(require_auth)):
    """Parse a bet-slip image into a BetIn-shaped draft for the confirm sheet.
    Does NOT write app_bet — the app reviews, then POSTs /bets with source='scan'."""
    try:
        draft = scan.parse_bet_slip(body.image_base64, body.media_type)
    except Exception as e:                       # parse / vision failure -> client retries
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
```

- [ ] **Step 4: Run the tests to verify they pass.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb && python -m pytest tests/test_softbook_scan_endpoint.py -q`
Expected: PASS (3 tests). Then run the existing suite to confirm no regression: `python -m pytest tests/test_softbook_app_api.py -q` → PASS.

- [ ] **Step 5: Commit.**

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
git add scripts/softbook_app_api.py tests/test_softbook_scan_endpoint.py
git commit -m "feat(softbook-app): POST /bets/scan — vision draft + event match (no auto-insert)"
```

#### Task 6: App — `ScanDraft`/`BetInDraft` types + `api.scanBet`

**Files:**
- Modify: `app/src/api/types.ts`
- Modify: `app/src/api/client.ts`
- Modify: `app/src/api/client.test.ts`

- [ ] **Step 1: Write the failing test.** Append to `app/src/api/client.test.ts`:

```typescript
test("scanBet posts the image and parses a ScanDraft", async () => {
  const draft = {
    bet: {
      event_id: "EVT123", sport: "baseball_mlb", home: "COL", away: "CHC",
      commence_time: null, market: "batter_total_bases", side: "Over", point: 1.5,
      player: "Nico Hoerner", label: "Nico Hoerner TB Over 1.5", book: "FanDuel",
      rec_odds: null, fill_odds: 2.0, stake: 2.0, source: "scan",
    },
    matched: true, notes: "single leg",
  };
  const fetchMock = jest.fn(async () => ({ ok: true, status: 200, json: async () => draft }));
  (global as unknown as { fetch: typeof fetch }).fetch = fetchMock as never;

  const { api } = require("./client");
  const out = await api.scanBet("QkFTRTY0", "image/png");
  expect(out.matched).toBe(true);
  expect(out.bet.event_id).toBe("EVT123");
  const [url, init] = fetchMock.mock.calls[0];
  expect(String(url)).toContain("/bets/scan");
  expect(init.method).toBe("POST");
  expect(JSON.parse(init.body)).toEqual({ image_base64: "QkFTRTY0", media_type: "image/png" });
});
```

(Match the credential/baseUrl mocking already at the top of `client.test.ts`; if that file mocks `storage`, keep that mock — this test only adds the new method assertion.)

- [ ] **Step 2: Run it to verify it fails.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/api/client.test.ts --watchAll=false`
Expected: FAIL — `api.scanBet is not a function`.

- [ ] **Step 3: Add the types.** Append to `app/src/api/types.ts`:

```typescript
// ===== Screenshot-AI scan =====
export interface BetInDraft {
  event_id: string | null;
  sport: string | null;
  home: string | null;
  away: string | null;
  commence_time: string | null;
  market: string | null;
  side: string | null;
  point: number | null;
  player: string | null;
  label: string | null;
  book: string | null;
  rec_odds: number | null;
  fill_odds: number | null;
  stake: number | null;
  source: string;
}

export interface ScanDraft {
  bet: BetInDraft;
  matched: boolean;
  notes: string;
}
```

- [ ] **Step 4: Add the client method.** In `app/src/api/client.ts`, import `ScanDraft` from `./types` and add to the `api` object:

```typescript
  scanBet: (image_base64: string, media_type = "image/jpeg") =>
    request<ScanDraft>("/bets/scan", {
      method: "POST",
      body: JSON.stringify({ image_base64, media_type }),
    }),
```

- [ ] **Step 5: Run the test to verify it passes.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/api/client.test.ts --watchAll=false`
Expected: PASS.

- [ ] **Step 6: Commit.**

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
git add app/src/api/types.ts app/src/api/client.ts app/src/api/client.test.ts
git commit -m "feat(softbook-app): ScanDraft types + api.scanBet client method"
```

#### Task 7: App — capture-from-photo flow (`scan.ts`) + CaptureSheet scan mode + PicksScreen entry

**Files:**
- Create: `app/src/scan.ts`, `app/src/scan.test.ts`
- Modify: `app/src/screens/CaptureSheet.tsx`, `app/src/screens/CaptureSheet.test.tsx`
- Modify: `app/src/screens/PicksScreen.tsx`, `app/src/screens/PicksScreen.test.tsx`
- Modify: `app/src/navigation/types.ts` (CaptureSheet param adds optional `scanDraft`)
- Native dep: `expo-image-picker`

- [ ] **Step 1: Install the native dep.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx expo install expo-image-picker`
Then add the iOS permission strings to `app/app.json` under `expo.ios.infoPlist`: `NSCameraUsageDescription` and `NSPhotoLibraryUsageDescription` (e.g. `"베팅 슬립 캡처를 위해 카메라/사진 접근이 필요합니다."`).

- [ ] **Step 2: Write the failing test.** `app/src/scan.test.ts`:

```typescript
jest.mock("expo-image-picker", () => ({
  MediaTypeOptions: { Images: "Images" },
  requestCameraPermissionsAsync: jest.fn(async () => ({ granted: true })),
  requestMediaLibraryPermissionsAsync: jest.fn(async () => ({ granted: true })),
  launchCameraAsync: jest.fn(),
  launchImageLibraryAsync: jest.fn(),
}));

import * as ImagePicker from "expo-image-picker";
import { pickImage, draftToBetIn } from "./scan";
import type { BetInDraft } from "./api/types";

const DRAFT: BetInDraft = {
  event_id: "EVT123", sport: "baseball_mlb", home: "COL", away: "CHC",
  commence_time: null, market: "batter_total_bases", side: "Over", point: 1.5,
  player: "Nico Hoerner", label: "Nico Hoerner TB Over 1.5", book: "FanDuel",
  rec_odds: null, fill_odds: 2.0, stake: 2.0, source: "scan",
};

test("pickImage returns base64 + mediaType on a successful library pick", async () => {
  (ImagePicker.launchImageLibraryAsync as jest.Mock).mockResolvedValueOnce({
    canceled: false, assets: [{ base64: "QkFTRTY0", mimeType: "image/png" }],
  });
  await expect(pickImage("library")).resolves.toEqual({ base64: "QkFTRTY0", mediaType: "image/png" });
});

test("pickImage returns null when permission denied", async () => {
  (ImagePicker.requestCameraPermissionsAsync as jest.Mock).mockResolvedValueOnce({ granted: false });
  await expect(pickImage("camera")).resolves.toBeNull();
});

test("pickImage returns null when the user cancels", async () => {
  (ImagePicker.launchImageLibraryAsync as jest.Mock).mockResolvedValueOnce({ canceled: true });
  await expect(pickImage("library")).resolves.toBeNull();
});

test("draftToBetIn maps draft fields and applies user inputs (source stays scan)", () => {
  const bet = draftToBetIn(DRAFT, { stake: 5, fillOdds: 2.05 });
  expect(bet).toMatchObject({
    event_id: "EVT123", market: "batter_total_bases", side: "Over",
    player: "Nico Hoerner", stake: 5, fill_odds: 2.05, source: "scan",
  });
});

test("draftToBetIn falls back to draft values and empty event_id when unmatched", () => {
  const bet = draftToBetIn({ ...DRAFT, event_id: null, stake: null }, {});
  expect(bet.event_id).toBe("");
  expect(bet.fill_odds).toBe(2.0);
  expect(bet.stake).toBeNull();
});
```

- [ ] **Step 3: Run it to verify it fails.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/scan.test.ts --watchAll=false`
Expected: FAIL — cannot find module `./scan`.

- [ ] **Step 4: Write `app/src/scan.ts`.**

```typescript
import * as ImagePicker from "expo-image-picker";
import type { BetIn, BetInDraft } from "./api/types";

export interface PickedImage {
  base64: string;
  mediaType: string;
}

/** Launch the camera or photo library and return a base64 image, or null on deny/cancel. */
export async function pickImage(source: "camera" | "library"): Promise<PickedImage | null> {
  const perm =
    source === "camera"
      ? await ImagePicker.requestCameraPermissionsAsync()
      : await ImagePicker.requestMediaLibraryPermissionsAsync();
  if (!perm.granted) return null;

  const res =
    source === "camera"
      ? await ImagePicker.launchCameraAsync({ base64: true, quality: 0.6 })
      : await ImagePicker.launchImageLibraryAsync({
          base64: true,
          quality: 0.6,
          mediaTypes: ImagePicker.MediaTypeOptions.Images,
        });

  const asset = res.canceled ? undefined : res.assets?.[0];
  if (!asset?.base64) return null;
  const mediaType = asset.mimeType ?? (asset.uri?.endsWith(".png") ? "image/png" : "image/jpeg");
  return { base64: asset.base64, mediaType };
}

/** Build a POST /bets body from a scan draft + the user's confirmed stake/odds/book. */
export function draftToBetIn(
  draft: BetInDraft,
  inputs: { stake?: number | null; fillOdds?: number | null; book?: string | null },
): BetIn {
  return {
    event_id: draft.event_id ?? "",
    sport: draft.sport,
    home: draft.home,
    away: draft.away,
    commence_time: draft.commence_time ?? null,
    market: draft.market ?? "",
    side: draft.side ?? "",
    point: draft.point,
    player: draft.player,
    label: draft.label,
    book: inputs.book ?? draft.book,
    rec_odds: draft.rec_odds,
    fill_odds: inputs.fillOdds ?? draft.fill_odds,
    stake: inputs.stake ?? draft.stake ?? null,
    source: draft.source || "scan",
  };
}
```

- [ ] **Step 5: Run the test to verify it passes.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/scan.test.ts --watchAll=false`
Expected: PASS (5 tests).

- [ ] **Step 6: Add the CaptureSheet param + scan mode.** In `app/src/navigation/types.ts`, widen the CaptureSheet param to allow a scan draft (keep `pickId` optional too):

```typescript
// in PicksStackParamList
CaptureSheet: { pickId?: string; scanDraft?: BetInDraft };
```

(Import `BetInDraft` from `../api/types` in `navigation/types.ts`.)

In `app/src/screens/CaptureSheet.tsx`, branch on the param. When `route.params.scanDraft` is present, skip `api.getPick`, seed state from the draft, and submit via `draftToBetIn`. Add near the top:

```typescript
import { draftToBetIn } from "../scan";
// ...
const scanDraft = route.params?.scanDraft;
// In the load effect: if (scanDraft) { setStake(scanDraft.stake); setFillOdds(scanDraft.fill_odds); /* skip getPick */ }
// In the submit handler:
//   const betIn = scanDraft
//     ? draftToBetIn(scanDraft, { stake, fillOdds, book })
//     : buildBetIn(pick, { stake, fillOdds, book });
//   await api.postBet(betIn);
```

Render the draft's `label` / `book` / `side`+`point` as read-only context above the editable stake + fill-odds inputs, and surface `scanDraft.event_id == null` as a small "오프피드 — 정산 수동" note so the user knows an unmatched bet won't auto-grade.

- [ ] **Step 7: Add the CaptureSheet scan-mode test.** Append to `app/src/screens/CaptureSheet.test.tsx`:

```typescript
test("scan mode prefills from the draft and posts with source=scan", async () => {
  const scanDraft = {
    event_id: "EVT123", sport: "baseball_mlb", home: "COL", away: "CHC",
    commence_time: null, market: "batter_total_bases", side: "Over", point: 1.5,
    player: "Nico Hoerner", label: "Nico Hoerner TB Over 1.5", book: "FanDuel",
    rec_odds: null, fill_odds: 2.0, stake: 2.0, source: "scan",
  };
  const route = { params: { scanDraft } } as never;
  await render(<CaptureSheet route={route} />);
  await waitFor(() => expect(screen.getByText("Nico Hoerner TB Over 1.5")).toBeOnTheScreen());
  await fireEvent.changeText(screen.getByPlaceholderText("스테이크 ($)"), "5");
  await fireEvent.press(screen.getByText("기록"));
  await waitFor(() => expect(api.postBet).toHaveBeenCalled());
  const body = (api.postBet as jest.Mock).mock.calls[0][0];
  expect(body).toMatchObject({ event_id: "EVT123", market: "batter_total_bases", stake: 5, source: "scan" });
  expect(api.getPick).not.toHaveBeenCalled();   // scan mode skips the pick fetch
});
```

- [ ] **Step 8: Add the PicksScreen capture entry.** In `app/src/screens/PicksScreen.tsx`, add a "📷 슬립 캡처" button (header right or a row above the list) whose handler picks an image, scans it, and navigates to CaptureSheet in scan mode:

```typescript
import { pickImage } from "../scan";
import { api } from "../api/client";
// ...
const onCapture = async (source: "camera" | "library") => {
  const img = await pickImage(source);
  if (!img) return;
  try {
    const draft = await api.scanBet(img.base64, img.mediaType);
    navigation.navigate("CaptureSheet", { scanDraft: draft.bet });
  } catch {
    /* surface a toast/Alert: "슬립을 읽지 못했어요. 다시 시도하세요." */
  }
};
// Wire a button with testID="scan-capture-library" → onCapture("library").
```

- [ ] **Step 9: Add the PicksScreen test.** Append to `app/src/screens/PicksScreen.test.tsx` (mock `../scan` and `../api/client` consistent with the file's existing mocks):

```typescript
jest.mock("../scan", () => ({
  pickImage: jest.fn(async () => ({ base64: "QkFTRTY0", mediaType: "image/jpeg" })),
}));

test("capture button scans the picked image and routes to the confirm sheet", async () => {
  const navigate = jest.fn();
  (api.scanBet as jest.Mock) = jest.fn(async () => ({
    bet: { event_id: "EVT123", market: "h2h", side: "COL", source: "scan" }, matched: true, notes: "",
  }));
  await render(<PicksScreen navigation={{ navigate, push: jest.fn() } as never} />);
  await fireEvent.press(screen.getByTestId("scan-capture-library"));
  await waitFor(() => expect(api.scanBet).toHaveBeenCalledWith("QkFTRTY0", "image/jpeg"));
  await waitFor(() =>
    expect(navigate).toHaveBeenCalledWith("CaptureSheet", { scanDraft: expect.objectContaining({ event_id: "EVT123" }) }),
  );
});
```

(Adjust the `navigation`/`api` mock shape to match how `PicksScreen.test.tsx` already injects them — reuse the file's existing fixtures and mocks rather than duplicating.)

- [ ] **Step 10: Run the full app suite + tsc.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest --watchAll=false && npx tsc --noEmit`
Expected: all suites PASS, `tsc` clean.

- [ ] **Step 11: Commit.**

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
git add app/src/scan.ts app/src/scan.test.ts app/src/screens/CaptureSheet.tsx app/src/screens/CaptureSheet.test.tsx app/src/screens/PicksScreen.tsx app/src/screens/PicksScreen.test.tsx app/src/navigation/types.ts app/app.json app/package.json app/package-lock.json
git commit -m "feat(softbook-app): screenshot-AI capture — pick image, scan, confirm-sheet prefill"
```

### Feature 2 — Price-move / cashout push (Tasks 8–11)

For open bets, poll current odds and push when the line has moved materially from the user's fill. A **drop** in the available odds for the same side means the market now rates the outcome likelier — the user beat the market and should consider the book's cashout (kind `cashout`); a **rise** means the line moved against them (kind `move`). The push reports the **odds move only** — never a fabricated dollar cashout (we don't have the book's oracle). It folds into the existing 5-min push worker, credit-capped to the top N open bets by stake.

#### Task 8: Backend — `app_bet` price columns + `pushed_price_move` + db helpers

**Files:**
- Modify: `scripts/softbook_app_db.py`
- Create: `tests/test_softbook_price_move.py` (db-helper half)

`init_schema` uses `CREATE TABLE IF NOT EXISTS` (won't alter the live `app_bet`), so add new columns via an idempotent PRAGMA-guarded `ALTER`. Updating `current_best_odds`/`last_odds_check_at` is an **advisory live-column write** (like `live_status`) — it does not touch the bet-record fields, so the ledger rule is respected.

- [ ] **Step 1: Write the failing test.** `tests/test_softbook_price_move.py`:

```python
import os
import sqlite3


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("SOFTBOOK_PAPER_DB", str(tmp_path / "paper.db"))
    import softbook_app_db as adb
    db = adb.connect()
    adb.init_schema(db)          # idempotent: also runs the column ALTERs
    adb.init_schema(db)          # second call must not raise (columns already present)
    return adb, db


def test_init_schema_adds_price_columns_and_dedup_table(tmp_path, monkeypatch):
    adb, db = _fresh_db(tmp_path, monkeypatch)
    cols = {r[1] for r in db.execute("PRAGMA table_info(app_bet)")}
    assert {"current_best_odds", "last_odds_check_at"} <= cols
    # dedup table exists
    db.execute("SELECT 1 FROM pushed_price_move LIMIT 1")


def test_record_current_odds_and_pricing_helpers(tmp_path, monkeypatch):
    adb, db = _fresh_db(tmp_path, monkeypatch)
    bid = adb.insert_app_bet(db, {
        "event_id": "EVT1", "market": "h2h", "side": "COL", "book": "FanDuel",
        "fill_odds": 2.0, "stake": 5.0, "placed_at": "2026-06-13T00:00:00Z", "source": "one_tap",
    })
    bets = adb.open_bets_for_pricing(db, limit=15)
    assert any(b["id"] == bid and b["fill_odds"] == 2.0 for b in bets)
    adb.record_current_odds(db, bid, 1.8, "2026-06-13T00:05:00Z")
    row = db.execute("SELECT current_best_odds, last_odds_check_at FROM app_bet WHERE id=?", (bid,)).fetchone()
    assert row[0] == 1.8 and row[1] == "2026-06-13T00:05:00Z"
    assert adb.price_move_pushed(db, bid, "cashout") is False
    adb.mark_price_move_pushed(db, bid, "cashout", "2026-06-13T00:05:00Z")
    assert adb.price_move_pushed(db, bid, "cashout") is True
    assert adb.price_move_pushed(db, bid, "move") is False     # per-kind dedup
```

- [ ] **Step 2: Run it to verify it fails.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb && python -m pytest tests/test_softbook_price_move.py -q`
Expected: FAIL — missing columns / `open_bets_for_pricing` undefined.

- [ ] **Step 3: Edit `scripts/softbook_app_db.py`.** In `init_schema(db)`, after the existing `CREATE TABLE IF NOT EXISTS` statements, add the dedup table + column ensure, then commit:

```python
    db.execute(
        "CREATE TABLE IF NOT EXISTS pushed_price_move("
        "app_bet_id INTEGER, kind TEXT, notified_at TEXT, "
        "PRIMARY KEY(app_bet_id, kind))")
    _ensure_app_bet_columns(db)
    db.commit()
```

Add the helpers at module scope:

```python
def _ensure_app_bet_columns(db):
    """Idempotently add advisory price columns to an existing app_bet table."""
    have = {r[1] for r in db.execute("PRAGMA table_info(app_bet)")}
    for name, decl in (("current_best_odds", "REAL"), ("last_odds_check_at", "TEXT")):
        if name not in have:
            db.execute(f"ALTER TABLE app_bet ADD COLUMN {name} {decl}")


_PRICING_COLS = ("id", "event_id", "sport", "market", "side", "point", "player",
                 "book", "fill_odds", "current_best_odds", "last_odds_check_at", "label")


def open_bets_for_pricing(db, limit=15):
    """Open bets eligible for a live-odds check: settled=0, matched event, known book+fill.
    Ordered by stake desc so the credit budget covers the bets that matter most."""
    rows = db.execute(
        "SELECT " + ",".join(_PRICING_COLS) + " FROM app_bet "
        "WHERE settled=0 AND event_id IS NOT NULL AND event_id!='' "
        "AND book IS NOT NULL AND fill_odds IS NOT NULL "
        "ORDER BY COALESCE(stake,0) DESC LIMIT ?", (limit,)).fetchall()
    return [dict(zip(_PRICING_COLS, r)) for r in rows]


def record_current_odds(db, app_bet_id, odds, ts):
    """Advisory live-column write — NOT a bet-record edit (ledger rule preserved)."""
    db.execute("UPDATE app_bet SET current_best_odds=?, last_odds_check_at=? WHERE id=?",
               (odds, ts, app_bet_id))
    db.commit()


def price_move_pushed(db, app_bet_id, kind):
    return db.execute("SELECT 1 FROM pushed_price_move WHERE app_bet_id=? AND kind=?",
                      (app_bet_id, kind)).fetchone() is not None


def mark_price_move_pushed(db, app_bet_id, kind, ts):
    db.execute("INSERT OR IGNORE INTO pushed_price_move(app_bet_id, kind, notified_at) "
               "VALUES (?,?,?)", (app_bet_id, kind, ts))
    db.commit()
```

- [ ] **Step 4: Run the test to verify it passes.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb && python -m pytest tests/test_softbook_price_move.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit.**

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
git add scripts/softbook_app_db.py tests/test_softbook_price_move.py
git commit -m "feat(softbook): app_bet price columns + pushed_price_move dedup + pricing helpers"
```

#### Task 9: Backend — `project_price_move` (pure detector)

**Files:**
- Modify: `scripts/softbook_push_worker.py` (add the constant + pure function)
- Modify: `tests/test_softbook_price_move.py` (add the pure-function test)

- [ ] **Step 1: Write the failing test.** Append to `tests/test_softbook_price_move.py`:

```python
def test_project_price_move_classifies_by_direction():
    import softbook_push_worker as w
    # Available odds dropped ≥5% below your fill → you beat the market → cashout opportunity.
    assert w.project_price_move(2.0, 1.8)["kind"] == "cashout"
    # Available odds rose ≥5% above your fill → line moved against you → heads-up.
    assert w.project_price_move(2.0, 2.2)["kind"] == "move"
    # Inside the band → no push.
    assert w.project_price_move(2.0, 2.05)["kind"] is None
    # Missing odds → no push, no crash.
    assert w.project_price_move(None, 1.8)["kind"] is None
    assert w.project_price_move(2.0, None)["kind"] is None
```

- [ ] **Step 2: Run it to verify it fails.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb && python -m pytest tests/test_softbook_price_move.py::test_project_price_move_classifies_by_direction -q`
Expected: FAIL — `AttributeError: ... has no attribute 'project_price_move'`.

- [ ] **Step 3: Add to `scripts/softbook_push_worker.py`** (module scope, near the top):

```python
PRICE_MOVE_LIMIT = 15           # max open bets priced per run (credit budget)
PRICE_MOVE_THRESHOLD = 0.05     # |move| ≥ 5% to push
CATEGORY_PRICE_MOVE = "price_move"


def project_price_move(fill_odds, current_odds, threshold=PRICE_MOVE_THRESHOLD):
    """Classify an open bet's odds move. move = current/fill - 1.
    drop (move ≤ -threshold) → 'cashout' (you beat the market);
    rise (move ≥ +threshold) → 'move' (line against you); else None."""
    if not fill_odds or not current_odds:
        return {"kind": None, "move_pct": 0.0}
    move = current_odds / fill_odds - 1.0
    if move <= -threshold:
        return {"kind": "cashout", "move_pct": move}
    if move >= threshold:
        return {"kind": "move", "move_pct": move}
    return {"kind": None, "move_pct": move}
```

- [ ] **Step 4: Run the test to verify it passes.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb && python -m pytest tests/test_softbook_price_move.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit.**

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
git add scripts/softbook_push_worker.py tests/test_softbook_price_move.py
git commit -m "feat(softbook): project_price_move pure detector (cashout vs adverse move)"
```

#### Task 10: Backend — `_price_move_messages` wired into `run_once`

**Files:**
- Modify: `scripts/softbook_push_worker.py`
- Modify: `tests/test_softbook_price_move.py` (worker-integration test with an injected odds fetcher)

- [ ] **Step 1: Write the failing test.** Append to `tests/test_softbook_price_move.py`:

```python
def test_price_move_messages_pushes_once_per_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("SOFTBOOK_PAPER_DB", str(tmp_path / "paper.db"))
    import softbook_app_db as adb
    import softbook_push_worker as w
    db = adb.connect(); adb.init_schema(db)
    bid = adb.insert_app_bet(db, {
        "event_id": "EVT1", "sport": "baseball_mlb", "market": "h2h", "side": "COL",
        "book": "FanDuel", "fill_odds": 2.0, "stake": 5.0,
        "placed_at": "2026-06-13T00:00:00Z", "source": "one_tap", "label": "COL ML",
    })
    db.execute("INSERT INTO push_token(token, updated_at, active) VALUES('TOK', 't', 1)")
    db.commit()

    # Injected fetcher: current best odds dropped to 1.8 → cashout.
    fetch = lambda *a, **k: 1.8
    msgs = w._price_move_messages(db, ["TOK"], "2026-06-13T00:05:00Z", fetch_odds=fetch)
    assert len(msgs) == 1
    assert msgs[0]["categoryId"] == w.CATEGORY_PRICE_MOVE
    assert msgs[0]["data"] == {"app_bet_id": bid}
    # current odds were recorded
    assert db.execute("SELECT current_best_odds FROM app_bet WHERE id=?", (bid,)).fetchone()[0] == 1.8
    # Second run with the same kind → deduped (no message)
    msgs2 = w._price_move_messages(db, ["TOK"], "2026-06-13T00:10:00Z", fetch_odds=fetch)
    assert msgs2 == []
```

- [ ] **Step 2: Run it to verify it fails.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb && python -m pytest tests/test_softbook_price_move.py::test_price_move_messages_pushes_once_per_kind -q`
Expected: FAIL — `_price_move_messages` undefined.

- [ ] **Step 3: Add `_price_move_messages` + the default fetcher and wire `run_once`.** Ensure the worker imports `import softbook_app_db as adb` (add if absent; `core`/`feed`/`push` are already imported). Add:

```python
def _markets_for(market):
    """Mirror softbook_app_api._markets_for_pick EXACTLY: a comma-joined STRING (event_odds
    interpolates it straight into the URL — a list would stringify to "['x','y']"), and only
    append "_alternate" when market is in core.PROP_ALTS. Requesting a non-existent alternate
    key 422s the WHOLE Odds API call (load-bearing PROP_ALTS guard), so never blindly add it."""
    if market in core.ALL_PROPS:
        keys = [market] + ([market + "_alternate"] if market in core.PROP_ALTS else [])
        return ",".join(keys)
    return core.GAME_MARKETS


def _default_fetch_odds(sport, event_id, market, side, point, player, book):
    """Current best available odds for this exact selection at the user's book, or None."""
    key = core.read_key()
    data, _ = core.event_odds(sport, event_id, key, _markets_for(market))
    _, quotes = core.line_quotes(data, market, side, point, player)
    return dict(quotes).get(book)


def _price_move_messages(pdb, tokens, now, fetch_odds=None):
    """Price open bets (top PRICE_MOVE_LIMIT by stake) and push on a material move.
    One push per (app_bet, kind). Records current odds even when no push fires."""
    fetch_odds = fetch_odds or _default_fetch_odds
    total = pdb.execute(
        "SELECT COUNT(*) FROM app_bet WHERE settled=0 AND event_id IS NOT NULL "
        "AND event_id!='' AND book IS NOT NULL AND fill_odds IS NOT NULL").fetchone()[0]
    if total > PRICE_MOVE_LIMIT:
        print(f"[price-move] {total} priceable open bets; capping at {PRICE_MOVE_LIMIT} by stake")
    msgs = []
    for b in adb.open_bets_for_pricing(pdb, limit=PRICE_MOVE_LIMIT):
        try:
            cur = fetch_odds(b["sport"], b["event_id"], b["market"],
                             b["side"], b["point"], b["player"], b["book"])
        except Exception as e:
            print(f"[price-move] fetch failed for bet {b['id']}: {e}")
            cur = None
        if cur is None:
            continue
        adb.record_current_odds(pdb, b["id"], cur, now)
        proj = project_price_move(b["fill_odds"], cur)
        kind = proj["kind"]
        if not kind or adb.price_move_pushed(pdb, b["id"], kind):
            continue
        title = "💸 캐시아웃 기회" if kind == "cashout" else "⚠️ 라인 이동"
        arrow = "↓" if kind == "cashout" else "↑"
        body = (f"{b['label'] or b['side']} · {b['book']} "
                f"{b['fill_odds']:.2f}{arrow}{cur:.2f} ({proj['move_pct'] * 100:+.0f}%)")
        for tok in tokens:
            msgs.append(push.build_message(tok, title, body,
                                           data={"app_bet_id": b["id"]},
                                           category=CATEGORY_PRICE_MOVE))
        adb.mark_price_move_pushed(pdb, b["id"], kind, now)
    return msgs
```

In `run_once(ev, pdb, send=None)`, after the high + settle messages are assembled and before `send(...)`, add the price-move messages:

```python
    msgs += _price_move_messages(pdb, tokens, now)
```

(Use the `tokens` and `now` already computed in `run_once`; the price-move call is best-effort and must not raise out of `run_once` — the per-bet `try/except` already contains fetch errors.)

- [ ] **Step 4: Run the tests + the existing push suite.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb && python -m pytest tests/test_softbook_price_move.py tests/test_softbook_push.py -q`
Expected: all PASS (price-move 4 tests; existing push suite unchanged).

- [ ] **Step 5: Commit.**

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
git add scripts/softbook_push_worker.py tests/test_softbook_price_move.py
git commit -m "feat(softbook): price-move/cashout push folded into the 5-min worker (credit-capped)"
```

#### Task 11: App — `price_move` category registration + routing lock

**Files:**
- Modify: `app/src/config.ts`
- Modify: `app/src/push.ts`
- Modify: `app/src/push.test.ts`

`notifTarget` already routes any `data.app_bet_id` → MyBets, so a `price_move` push (which carries `app_bet_id`) navigates correctly with no routing change. This task adds the category constant + registration (so iOS recognises it) and a regression test locking the routing.

- [ ] **Step 1: Write the failing test.** Append to `app/src/push.test.ts`:

```typescript
import { CATEGORY_PRICE_MOVE } from "./config";

test("a price_move push routes to MyBets via app_bet_id", () => {
  const resp = {
    actionIdentifier: "default",
    notification: { request: { content: {
      categoryIdentifier: CATEGORY_PRICE_MOVE,
      data: { app_bet_id: 7 },
    } } },
  };
  expect(notifTarget(resp as never)).toEqual({ kind: "mybets", appBetId: 7 });
});

test("registerCategories includes price_move (no action buttons)", async () => {
  await registerCategories();
  const Notifications = require("expo-notifications");
  const ids = (Notifications.setNotificationCategoryAsync as jest.Mock).mock.calls.map((c) => c[0]);
  expect(ids).toContain(CATEGORY_PRICE_MOVE);
});
```

(Match the existing `push.test.ts` import line for `notifTarget`/`registerCategories` and its `expo-notifications` mock — this test reuses them.)

- [ ] **Step 2: Run it to verify it fails.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/push.test.ts --watchAll=false`
Expected: FAIL — `CATEGORY_PRICE_MOVE` undefined / category not registered.

- [ ] **Step 3: Add the constant + registration.** In `app/src/config.ts`:

```typescript
export const CATEGORY_PRICE_MOVE = "price_move";
```

In `app/src/push.ts` `registerCategories()`, register the new category alongside `high_pick`/`settle` with no actions:

```typescript
import { CATEGORY_PRICE_MOVE } from "./config";
// inside registerCategories(), after the existing setNotificationCategoryAsync calls:
await Notifications.setNotificationCategoryAsync(CATEGORY_PRICE_MOVE, []);
```

- [ ] **Step 4: Run the test to verify it passes.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/push.test.ts --watchAll=false`
Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
git add app/src/config.ts app/src/push.ts app/src/push.test.ts
git commit -m "feat(softbook-app): register price_move push category + routing regression test"
```

### Feature 3 — Saved mute-rules / advanced triage (Tasks 12–14)

Beyond `filter_prefs` (min-EV, sport toggles, book mute), persistent rules let the user mute a **specific player, market, event, or sport** so those picks never surface again. Rules live in a new `mute_rule` table; the server feed composer drops matching picks. The Rules editor renders **inline in Settings** (no new navigator — keeps the change small and testable).

#### Task 12: Backend — `mute_rule` table + helpers

**Files:**
- Modify: `scripts/softbook_app_db.py`
- Create: `tests/test_softbook_mute_rules.py` (db-helper half)

- [ ] **Step 1: Write the failing test.** `tests/test_softbook_mute_rules.py`:

```python
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("SOFTBOOK_PAPER_DB", str(tmp_path / "paper.db"))
    import softbook_app_db as adb
    db = adb.connect(); adb.init_schema(db)
    return adb, db


def test_mute_rule_crud_helpers(tmp_path, monkeypatch):
    adb, db = _db(tmp_path, monkeypatch)
    rid = adb.add_mute_rule(db, "player", "Nico Hoerner", "2026-06-13T00:00:00Z")
    rules = adb.list_mute_rules(db)
    assert any(r["id"] == rid and r["field"] == "player" and r["value"] == "Nico Hoerner" for r in rules)
    assert ("player", "Nico Hoerner") in adb.active_mute_rules(db)
    adb.delete_mute_rule(db, rid)
    assert adb.list_mute_rules(db) == []
```

- [ ] **Step 2: Run it to verify it fails.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb && python -m pytest tests/test_softbook_mute_rules.py -q`
Expected: FAIL — `add_mute_rule` undefined / no `mute_rule` table.

- [ ] **Step 3: Edit `scripts/softbook_app_db.py`.** In `init_schema`, add the table:

```python
    db.execute(
        "CREATE TABLE IF NOT EXISTS mute_rule("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, field TEXT, value TEXT, created_at TEXT)")
```

Add helpers at module scope:

```python
def add_mute_rule(db, field, value, ts):
    cur = db.execute("INSERT INTO mute_rule(field, value, created_at) VALUES (?,?,?)",
                     (field, value, ts))
    db.commit()
    return cur.lastrowid


def list_mute_rules(db):
    rows = db.execute("SELECT id, field, value, created_at FROM mute_rule ORDER BY id").fetchall()
    return [dict(zip(("id", "field", "value", "created_at"), r)) for r in rows]


def active_mute_rules(db):
    """[(field, value), …] for the feed filter."""
    return [(r[0], r[1]) for r in db.execute("SELECT field, value FROM mute_rule")]


def delete_mute_rule(db, rule_id):
    db.execute("DELETE FROM mute_rule WHERE id=?", (rule_id,))
    db.commit()
```

- [ ] **Step 4: Run the test to verify it passes.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb && python -m pytest tests/test_softbook_mute_rules.py -q`
Expected: PASS (1 test).

- [ ] **Step 5: Commit.**

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
git add scripts/softbook_app_db.py tests/test_softbook_mute_rules.py
git commit -m "feat(softbook): mute_rule table + CRUD helpers"
```

#### Task 13: Backend — feed honours mute rules + `/rules` CRUD

**Files:**
- Modify: `scripts/softbook_app_feed.py` (add `_is_muted_row` + filter in `compose`)
- Modify: `scripts/softbook_app_api.py` (add `RuleIn`, `/rules` routes, pass rules into `compose`)
- Modify: `tests/test_softbook_mute_rules.py` (add the filter unit test + endpoint tests)

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_softbook_mute_rules.py`:

```python
def test_is_muted_row_matches_field_to_value(tmp_path):
    import softbook_app_feed as feed
    row = {"event_id": "E1", "sport": "baseball_mlb", "market": "batter_total_bases",
           "side": "Over", "point": 1.5, "player": "Nico Hoerner"}
    assert feed._is_muted_row(row, [("player", "Nico Hoerner")]) is True
    assert feed._is_muted_row(row, [("market", "batter_total_bases")]) is True
    assert feed._is_muted_row(row, [("event", "E1")]) is True       # 'event' → event_id
    assert feed._is_muted_row(row, [("sport", "basketball_nba")]) is False
    assert feed._is_muted_row(row, []) is False


def test_rules_endpoints(tmp_path, monkeypatch):
    import os
    os.environ.setdefault("APP_API_TOKEN", "test-token")
    os.environ.pop("CF_ACCESS_TEAM_DOMAIN", None)
    monkeypatch.setenv("SOFTBOOK_PAPER_DB", str(tmp_path / "paper.db"))
    import softbook_app_db as adb
    adb.init_schema(adb.connect())
    import softbook_app_api as api
    from fastapi.testclient import TestClient
    c = TestClient(api.app); c.headers.update({"Authorization": "Bearer test-token"})

    r = c.post("/rules", json={"field": "player", "value": "Nico Hoerner"})
    assert r.status_code == 200
    rid = r.json()["id"]
    assert any(x["id"] == rid for x in c.get("/rules").json()["rules"])
    assert c.post("/rules", json={"field": "bogus", "value": "x"}).status_code == 400
    assert c.delete(f"/rules/{rid}").status_code == 200
    assert c.get("/rules").json()["rules"] == []
```

- [ ] **Step 2: Run it to verify it fails.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb && python -m pytest tests/test_softbook_mute_rules.py -q`
Expected: FAIL — `_is_muted_row` undefined, `/rules` 404.

- [ ] **Step 3: Add the filter to `scripts/softbook_app_feed.py`.** Read `compose(...)` first. Add the helper at module scope:

```python
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
```

Add an optional `mute_rules=()` parameter to `compose(...)` (keep it last so existing callers/tests are unaffected) and, at the **top** of `compose`, drop muted rows before any classification:

```python
def compose(rows, states, prefs, now, mute_rules=()):
    if mute_rules:
        rows = [r for r in rows if not _is_muted_row(r, mute_rules)]
    # … existing body unchanged …
```

- [ ] **Step 4: Add `RuleIn` + the routes to `scripts/softbook_app_api.py` and pass rules into `/picks`.** Add the model near the others:

```python
class RuleIn(BaseModel):
    field: str
    value: str
```

Add the routes:

```python
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
```

In `get_picks` (`GET /picks`), pass the active rules into `compose`:

```python
        picks = feed.compose(_latest_high_rows(), adb.pick_states(pdb),
                             _prefs(pdb), now, adb.active_mute_rules(pdb))
```

- [ ] **Step 5: Run the tests + the existing feed/api suites.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb && python -m pytest tests/test_softbook_mute_rules.py tests/test_softbook_app_feed.py tests/test_softbook_app_api.py -q`
Expected: all PASS (mute-rules 3 tests; existing feed/api suites unchanged — the new `compose` param defaults to `()`).

- [ ] **Step 6: Commit.**

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
git add scripts/softbook_app_feed.py scripts/softbook_app_api.py tests/test_softbook_mute_rules.py
git commit -m "feat(softbook-app): /rules CRUD + feed drops picks matching mute rules"
```

#### Task 14: App — `MuteRule` type + api + inline Rules editor in Settings

**Files:**
- Modify: `app/src/api/types.ts`, `app/src/api/client.ts`, `app/src/api/client.test.ts`
- Create: `app/src/screens/RulesScreen.tsx` (exports `RulesEditor`), `app/src/screens/RulesScreen.test.tsx`
- Modify: `app/src/screens/SettingsScreen.tsx`

- [ ] **Step 1: Write the failing api test.** Append to `app/src/api/client.test.ts`:

```typescript
test("rules CRUD client methods hit the right paths", async () => {
  const fetchMock = jest.fn(async (url: string, init?: { method?: string }) => {
    if (String(url).endsWith("/rules") && (!init || init.method === undefined))
      return { ok: true, status: 200, json: async () => ({ rules: [{ id: 1, field: "player", value: "Nico Hoerner", created_at: "t" }] }) };
    if (String(url).endsWith("/rules") && init?.method === "POST")
      return { ok: true, status: 200, json: async () => ({ id: 2 }) };
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  });
  (global as unknown as { fetch: typeof fetch }).fetch = fetchMock as never;
  const { api } = require("./client");
  expect((await api.getRules()).rules[0].field).toBe("player");
  expect((await api.addRule("market", "total")).id).toBe(2);
  expect((await api.deleteRule(2)).ok).toBe(true);
  expect(fetchMock.mock.calls.some(([u, i]) => String(u).endsWith("/rules/2") && i?.method === "DELETE")).toBe(true);
});
```

- [ ] **Step 2: Run it to verify it fails.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/api/client.test.ts --watchAll=false`
Expected: FAIL — `api.getRules is not a function`.

- [ ] **Step 3: Add the type + client methods.** In `app/src/api/types.ts`:

```typescript
export interface MuteRule {
  id: number;
  field: "player" | "market" | "sport" | "event";
  value: string;
  created_at: string;
}
```

In `app/src/api/client.ts` (import `MuteRule`):

```typescript
  getRules: () => request<{ rules: MuteRule[] }>("/rules"),
  addRule: (field: MuteRule["field"], value: string) =>
    request<{ id: number }>("/rules", { method: "POST", body: JSON.stringify({ field, value }) }),
  deleteRule: (id: number) =>
    request<{ ok: boolean }>(`/rules/${id}`, { method: "DELETE" }),
```

- [ ] **Step 4: Run the api test to verify it passes.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/api/client.test.ts --watchAll=false`
Expected: PASS.

- [ ] **Step 5: Write the failing RulesEditor test.** `app/src/screens/RulesScreen.test.tsx`:

```typescript
jest.mock("../api/client", () => ({
  api: {
    getRules: jest.fn(async () => ({ rules: [{ id: 1, field: "player", value: "Nico Hoerner", created_at: "t" }] })),
    addRule: jest.fn(async () => ({ id: 2 })),
    deleteRule: jest.fn(async () => ({ ok: true })),
  },
}));

import { render, screen, waitFor, fireEvent } from "@testing-library/react-native";
import { RulesEditor } from "./RulesScreen";
import { api } from "../api/client";

test("lists existing rules and adds a new one", async () => {
  await render(<RulesEditor />);
  await waitFor(() => expect(screen.getByText(/Nico Hoerner/)).toBeOnTheScreen());
  await fireEvent.changeText(screen.getByPlaceholderText("값 (예: Aaron Judge / total / EVT123)"), "total");
  await fireEvent.press(screen.getByText("규칙 추가"));
  await waitFor(() => expect(api.addRule).toHaveBeenCalled());
});

test("deletes a rule", async () => {
  await render(<RulesEditor />);
  await waitFor(() => expect(screen.getByText(/Nico Hoerner/)).toBeOnTheScreen());
  await fireEvent.press(screen.getByTestId("delete-rule-1"));
  await waitFor(() => expect(api.deleteRule).toHaveBeenCalledWith(1));
});
```

- [ ] **Step 6: Run it to verify it fails.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/screens/RulesScreen.test.tsx --watchAll=false`
Expected: FAIL — cannot find `./RulesScreen`.

- [ ] **Step 7: Write `app/src/screens/RulesScreen.tsx`.**

```tsx
import React, { useCallback, useEffect, useState } from "react";
import { View, Text, TextInput, Pressable, FlatList, StyleSheet } from "react-native";
import { api } from "../api/client";
import type { MuteRule } from "../api/types";

const FIELDS: MuteRule["field"][] = ["player", "market", "sport", "event"];
const FIELD_LABEL: Record<MuteRule["field"], string> = {
  player: "선수", market: "마켓", sport: "종목", event: "경기",
};

export function RulesEditor() {
  const [rules, setRules] = useState<MuteRule[]>([]);
  const [field, setField] = useState<MuteRule["field"]>("player");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try { setRules((await api.getRules()).rules); } catch { /* keep current */ }
  }, []);
  useEffect(() => { load(); }, [load]);

  const add = async () => {
    if (!value.trim() || busy) return;
    setBusy(true);
    try { await api.addRule(field, value.trim()); setValue(""); await load(); }
    finally { setBusy(false); }
  };
  const remove = async (id: number) => {
    setBusy(true);
    try { await api.deleteRule(id); await load(); }
    finally { setBusy(false); }
  };

  return (
    <View style={styles.wrap}>
      <View style={styles.fieldRow}>
        {FIELDS.map((f) => (
          <Pressable key={f} onPress={() => setField(f)}
            style={[styles.chip, field === f && styles.chipOn]}>
            <Text style={[styles.chipText, field === f && styles.chipTextOn]}>{FIELD_LABEL[f]}</Text>
          </Pressable>
        ))}
      </View>
      <View style={styles.addRow}>
        <TextInput style={styles.input} value={value} onChangeText={setValue}
          placeholder="값 (예: Aaron Judge / total / EVT123)" autoCapitalize="none" />
        <Pressable onPress={add} style={styles.addBtn}><Text style={styles.addBtnText}>규칙 추가</Text></Pressable>
      </View>
      <FlatList
        data={rules}
        keyExtractor={(r) => String(r.id)}
        ListEmptyComponent={<Text style={styles.empty}>저장된 규칙이 없습니다.</Text>}
        renderItem={({ item }) => (
          <View style={styles.ruleRow}>
            <Text style={styles.ruleText}>{FIELD_LABEL[item.field]} · {item.value}</Text>
            <Pressable testID={`delete-rule-${item.id}`} onPress={() => remove(item.id)}>
              <Text style={styles.del}>삭제</Text>
            </Pressable>
          </View>
        )}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: 8 },
  fieldRow: { flexDirection: "row", gap: 8 },
  chip: { paddingVertical: 6, paddingHorizontal: 12, borderRadius: 16, backgroundColor: "#eee" },
  chipOn: { backgroundColor: "#4f46e5" },
  chipText: { color: "#333" },
  chipTextOn: { color: "#fff" },
  addRow: { flexDirection: "row", gap: 8, alignItems: "center" },
  input: { flex: 1, borderWidth: 1, borderColor: "#ccc", borderRadius: 8, padding: 10 },
  addBtn: { backgroundColor: "#4f46e5", borderRadius: 8, paddingVertical: 10, paddingHorizontal: 14 },
  addBtnText: { color: "#fff", fontWeight: "600" },
  ruleRow: { flexDirection: "row", justifyContent: "space-between", paddingVertical: 10 },
  ruleText: { color: "#222" },
  del: { color: "#dc2626" },
  empty: { color: "#888", paddingVertical: 12 },
});
```

- [ ] **Step 8: Run the RulesEditor test to verify it passes.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/screens/RulesScreen.test.tsx --watchAll=false`
Expected: PASS (2 tests).

- [ ] **Step 9: Render it inside Settings.** In `app/src/screens/SettingsScreen.tsx`, import `RulesEditor` and add a section (heading "트리아지 규칙 (Mute rules)") rendering `<RulesEditor />` below the existing filter prefs / credentials sections. No navigation change.

- [ ] **Step 10: Run the full app suite + tsc.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest --watchAll=false && npx tsc --noEmit`
Expected: all PASS, `tsc` clean.

- [ ] **Step 11: Commit.**

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
git add app/src/api/types.ts app/src/api/client.ts app/src/api/client.test.ts app/src/screens/RulesScreen.tsx app/src/screens/RulesScreen.test.tsx app/src/screens/SettingsScreen.tsx
git commit -m "feat(softbook-app): inline mute-rule editor in Settings (MuteRule type + api)"
```

### Feature 4 — Polish: swipe-to-dismiss (Task 15)

#### Task 15: App — swipe-to-dismiss on the pick card

**Files:**
- Modify: `app/src/components/PickCard.tsx`, `app/src/components/PickCard.test.tsx` (or `PicksScreen.test.tsx` if PickCard is tested there)
- Modify: `app/App.tsx` (ensure `GestureHandlerRootView` wraps the tree — required for gestures)
- Native dep: `react-native-gesture-handler` (already transitively present via React Navigation; install explicitly to pin)

A reveal-on-swipe "관심없음" action that calls the same `onDismiss(pickId)` the inline button already uses — the inline button **stays** as an accessible fallback (swipe is the polish, not a removal). Gesture simulation isn't reliable in jest, so the test presses the revealed action's `testID` to lock the wiring.

- [ ] **Step 1: Install / pin the dep.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx expo install react-native-gesture-handler`

- [ ] **Step 2: Write the failing test.** Append to the PickCard test file (`app/src/components/PickCard.test.tsx`; create it if PickCard had no standalone test, mirroring the PICK fixture used in `PicksScreen.test.tsx`):

```typescript
import { render, screen, fireEvent } from "@testing-library/react-native";
import { PickCard } from "./PickCard";

const PICK = {
  id: "E1|h2h|NYM|0|", status: "active" as const, event_id: "E1", sport: "baseball_mlb",
  home: "MIA", away: "NYM", commence_time: "2099-01-01T00:00:00Z", market: "h2h",
  side: "NYM", point: null, player: null, label: "NYM ML", ev: 0.07, book: "FanDuel",
  odds: 2.2, conf: 60, n_books: 9, first_entry: true,
};

test("the swipe dismiss action calls onDismiss with the pick id", async () => {
  const onDismiss = jest.fn();
  await render(<PickCard pick={PICK} onPress={jest.fn()} onDismiss={onDismiss} />);
  await fireEvent.press(screen.getByTestId("swipe-dismiss"));
  expect(onDismiss).toHaveBeenCalledWith("E1|h2h|NYM|0|");
});
```

- [ ] **Step 3: Run it to verify it fails.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/components/PickCard.test.tsx --watchAll=false`
Expected: FAIL — no `swipe-dismiss` testID.

- [ ] **Step 4: Wrap the card in `Swipeable`.** In `app/src/components/PickCard.tsx`, import `Swipeable` from `react-native-gesture-handler` and wrap the existing card body; render a right action that calls `onDismiss(pick.id)`:

```tsx
import { Swipeable } from "react-native-gesture-handler";
import { Pressable, Text, View } from "react-native";

// inside PickCard render, wrap the existing card JSX:
const renderRightActions = () => (
  <Pressable
    testID="swipe-dismiss"
    onPress={() => onDismiss(pick.id)}
    style={{ backgroundColor: "#dc2626", justifyContent: "center", paddingHorizontal: 24 }}
  >
    <Text style={{ color: "#fff", fontWeight: "600" }}>관심없음</Text>
  </Pressable>
);

return (
  <Swipeable renderRightActions={renderRightActions}>
    {/* …existing card body, including the inline dismiss button (kept as fallback)… */}
  </Swipeable>
);
```

If `PickCard`'s props don't already include `onDismiss`, add it to the prop type and thread it from `PicksScreen` (which already has a dismiss handler for the inline button).

- [ ] **Step 5: Ensure `GestureHandlerRootView` wraps the app.** In `app/App.tsx`, import `GestureHandlerRootView` from `react-native-gesture-handler` and wrap the root (inside or around `SafeAreaProvider`) with `<GestureHandlerRootView style={{ flex: 1 }}>…</GestureHandlerRootView>` if not already present. (React Navigation native-stack needs it too; this is safe and idempotent.)

- [ ] **Step 6: Run the test + full suite + tsc.**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest --watchAll=false && npx tsc --noEmit`
Expected: all PASS, `tsc` clean. If `jest-expo` doesn't auto-mock `react-native-gesture-handler`'s `Swipeable`, add `import "react-native-gesture-handler/jestSetup";` to `app/jest.setup.ts`.

- [ ] **Step 7: Commit.**

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
git add app/src/components/PickCard.tsx app/src/components/PickCard.test.tsx app/App.tsx app/jest.setup.ts app/package.json app/package-lock.json
git commit -m "feat(softbook-app): swipe-to-dismiss on the pick card (inline button kept as fallback)"
```

## Deployment (after the code lands on `master`)

**Backend** (EC2 `ubuntu@3.146.180.145`, project `/home/ubuntu/kalshi-arb`):

```bash
git fetch && git reset --hard origin/master
./venv/bin/pip install anthropic            # Task 3 new dep
# add ANTHROPIC_API_KEY=sk-ant-… to .env (Prerequisite 1)
sudo systemctl restart kalshi-softbook-api.service   # REQUIRED — /bets/scan + /rules live in the API
# push-worker change (price-move) rides the next *:0/5 oneshot — no restart needed
```

Verify: `curl -s -XPOST https://api.keepmypick.com/rules -H "Authorization: Bearer $APP_API_TOKEN" -H 'content-type: application/json' -d '{"field":"sport","value":"icehockey_nhl"}'` → `{"id":N}`; then `GET /rules` shows it; `DELETE /rules/N`. For `/bets/scan`, post a small base64 jpeg and confirm a `{bet,matched,notes}` shape (this consumes Anthropic tokens — do it once).

**App** (rebuild only after the JS changes land): `cd app && eas build -p ios --profile production --auto-submit --non-interactive` (credentials in EAS). Then update the device build from TestFlight and run the on-device checks (camera/library scan → confirm sheet; a price-move push tapping through to MyBets; a mute rule hiding a pick).

**Odds-API credit note (Open Question #3):** price-move adds ≤ `PRICE_MOVE_LIMIT` (15) event-odds fetches per 5-min run (~1 credit each, ~180/hr worst case when 15+ bets are open). Watch the `/picks/{id}/refresh` `x-requests-remaining` header after rollout; if the plan tier is tight, lower `PRICE_MOVE_LIMIT` or widen `PRICE_MOVE_THRESHOLD`.

---

## Self-Review

**1. Spec coverage** — every Phase-2 spec item + closeout item maps to a task:

| Spec / closeout item | Task(s) |
|---|---|
| On-device 🟣/⚪ badge + first-entry filter verification | Task 1 (manual) |
| Deep-link prefill verify → `VERIFIED_PREFILL_BOOKS` | Task 2 |
| Telegram decommission (after soak) | Runbook (no-action-in-plan) |
| **Screenshot-AI capture** (`POST /bets/scan` + vision; app capture → confirm) | Tasks 3, 4, 5 (backend) + 6, 7 (app) |
| **Price-move / cashout push** | Tasks 8, 9, 10 (backend) + 11 (app) |
| **Saved mute-rules / advanced triage** | Tasks 12, 13 (backend) + 14 (app) |
| **Polish** (swipe-to-dismiss) | Task 15 |

Open Questions from the spec: real_fill→app_bet migration — **resolved in Plan 1**; FanDuel betslip host — **Task 2 on-device**; Odds-API tier after on-demand load — **metered, see credit note**; live-grade timer placement — **unchanged (Phase 1 owns it)**.

**2. Placeholder scan** — every code step ships real code. The only intentionally-prose steps are the genuinely-manual ones (Task 1 device checks; Task 2 Step 1 on-device verify; Task 7 Step 6/8 / Task 14 Step 9 wiring into existing screens whose full bodies live in the repo — the implementer reads the file and inserts the shown block). No "TBD", "add error handling", or "similar to Task N".

**3. Type / name consistency** (verified across tasks):
- `BetInDraft` fields are identical in the backend `/bets/scan` response (Task 5), `api/types.ts` (Task 6), and `scan.ts` `draftToBetIn` (Task 7): `event_id, sport, home, away, commence_time, market, side, point, player, label, book, rec_odds, fill_odds, stake, source`. `ScanDraft = {bet, matched, notes}` matches the endpoint.
- `CATEGORY_PRICE_MOVE = "price_move"` is the same string in `softbook_push_worker.py` (Task 9) and `config.ts` (Task 11); the push carries `data={app_bet_id}` which the **existing** `notifTarget` already routes to MyBets (Task 11 only locks it).
- Mute-rule field vocabulary is one set everywhere: backend `_RULE_FIELDS=("player","market","sport","event")` (Task 13), `feed._MUTE_COL` maps `event→event_id` (Task 13), app `MuteRule["field"]` union + `RulesEditor` `FIELDS` (Task 14).
- DB helpers are defined once (Task 8: `open_bets_for_pricing`, `record_current_odds`, `price_move_pushed`, `mark_price_move_pushed`, `_ensure_app_bet_columns`; Task 12: `add_mute_rule`, `list_mute_rules`, `active_mute_rules`, `delete_mute_rule`) and consumed by the worker (Task 10) / api (Task 13) under those exact names.
- `parse_bet_slip` / `match_event` / `SCAN_SCHEMA` (Tasks 3–4) consumed by the endpoint as `scan.*` (Task 5). `project_price_move` (Task 9) consumed by `_price_move_messages` (Task 10).

**4. Backend assumptions this plan relies on** (true on master `8269042`; the implementer should confirm by reading the file before editing):
- `softbook_app_api.py` exposes `_ev_conn()` (used by `_enrich_first_entry`) and the module aliases `adb`/`core`/`feed`/`deeplink`, helper `_now()`, and `get_picks` composing via `feed.compose(_latest_high_rows(), adb.pick_states(pdb), _prefs(pdb), now)`. Task 13 adds the 5th positional arg.
- `softbook_app_feed.compose(rows, states, prefs, now)` takes those four positional args and operates on softbook_ev-shaped row dicts with keys `event_id, sport, market, side, point, player`. Task 13 appends an optional `mute_rules=()` param — **read `compose` first** to place the top-of-function filter correctly.
- `softbook_ev_core` exposes `read_key()`, `event_odds(sport, event_id, key, markets)`, `line_quotes(data, market, side, point, player)`, and `ALL_PROPS`/`GAME_MARKETS`. The price-move **default** fetcher (Task 10 Step 3) uses these; CI never exercises it (tests inject `fetch_odds`), so **smoke-test the live path on EC2** after deploy.
- `softbook_push_worker.run_once(ev, pdb, send=None)` computes `tokens` + `now` before sending — Task 10 appends `_price_move_messages(pdb, tokens, now)` there.
- `softbook_app_db.init_schema(db)` is called on every `connect()` and uses `CREATE TABLE IF NOT EXISTS` — the new `ALTER`s (Task 8) are PRAGMA-guarded so they're safe on the live `app_bet`.
- App: `notifTarget` routes `data.app_bet_id`→mybets and `data.pick_id`→pickDetail/capture; `api/client.ts` `request<T>` attaches Keychain bearer + CF headers; `CaptureSheet` takes a route param and `buildBetIn` builds the one-tap body — Task 7 adds the parallel `scanDraft`/`draftToBetIn` path without touching the pick path.

**5. Ledger & honesty invariants held:** no task DELETEs or repricing-UPDATEs an `app_bet` bet-record field (price columns are advisory live-state, like `live_status`); mute rules filter the feed but never touch `app_bet`; price-move push reports the odds move only (no fabricated cashout dollar amount); `/bets/scan` never auto-inserts (human confirms). Carries forward `[[feedback-ledger-rows-are-bet-records]]`.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-13-ev-companion-app-phase2.md`. Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration. REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**2. Inline Execution** — execute tasks in this session with checkpoints. REQUIRED SUB-SKILL: superpowers:executing-plans.

**Suggested order & gating:**
- **Tasks 3–15 are fully codeable + testable offline now** (mocks/injection); take the whole backend + app to a green suite + clean `tsc` before any paid/device step.
- **Task 1** (device badge/filter) is a Phase-1 regression gate — do it first; don't build Phase 2 on a broken feed.
- **Task 2 Step 1** (deep-link verify), the **Anthropic key + `pip install`** (Prerequisite 1), and the **EAS rebuild** are the human/paid steps — batch them after the code is green.
- Recommended build sequence: Feature 1 (3→7) → Feature 2 (8→11) → Feature 3 (12→14) → Feature 4 (15). Each feature lands independently green; deploy the backend once after Features 1–3, rebuild the app once after all app tasks.
- **Telegram decommission stays out of this plan** — it's a separate ops session after the soak confirms push + scan + price-move parity.




