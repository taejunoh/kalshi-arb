# +EV Betting Companion App — Design

**Date:** 2026-06-12
**Status:** Approved design, pending implementation plan
**Owner:** Taejun (single user, personal app)

## Problem

The softbook +EV pipeline now surfaces ~60 High picks/day. Acting on them through
Telegram is slow: each pick means manually opening a sportsbook, searching for the
selection, and entering the bet — and odds lines move while that happens, eroding the
edge that made the pick +EV. Bet records, PnL, and live status live in Telegram replies
and the dashboard, none of which is built for fast in-the-moment action on a phone.

This project replaces Telegram with a personal native iOS app that: shows High picks with
per-book live odds, deep-links into the sportsbook to place the bet with minimum friction,
captures what was actually bet, tracks each bet live in-game, and shows PnL on a calendar.

## Goals

- Surface the softbook +EV High picks feed on a phone, triageable at ~60/day.
- One screen per pick showing per-book odds (fresh on open), with a tap that opens the
  sportsbook — pre-filling the bet slip where technically possible.
- Capture the actual bet (stake + filled odds) with near-one-tap effort, because the app
  recommended the pick and already knows everything except stake and final odds.
- Live in-game tracking and auto-grading of open bets ("needs 2 more total bases, Mid 2nd").
- Calendar PnL of bets actually placed.
- Push notifications replacing Telegram alerts, with actionable buttons.
- Fully replace the Telegram alert + fill-listener-reply flow.

## Non-Goals

- **Automated bet placement.** No app does this; sportsbook write-APIs are partner/regulator
  gated. We deep-link and the user confirms in the book. (Out of scope, permanently.)
- **Credential-based auto bet-sync (Pikkit BookSync).** Requires a B2B aggregator
  (SharpSports, ~$500/mo, business-gated) or handing 8 sportsbook logins to a third party
  with ToS/account-closure risk. Not viable or desirable for a solo personal app. See
  Capture section for the chosen alternative.
- **Full pick analytics / strategy measurement.** Stays in the existing dashboard. The app
  shows only the live feed and bets actually placed.
- **Multi-user / App Store distribution.** Single user, TestFlight install. (DubClub-style
  productization is a possible far-future follow-up, explicitly out of scope here.)

## Key Decisions (and why)

| Decision | Choice | Rationale |
|---|---|---|
| Platform | Native iOS via Expo / React Native | Most stable push (APNs) and best UX; deep-links open via the system handler (not an in-app webview, which breaks prefill — a Telegram failure mode). |
| Telegram | Full replacement | Push for alerts; in-app capture for fills. Existing `real_fill` data carried forward. |
| Odds freshness | Single-event re-fetch on pick-detail open + manual refresh | The 20-min scan snapshot goes stale before betting; single-event refetch is cheap (~3-5 credits scoped). |
| Deep-link | Tiered: FD/DK bet-slip prefill (best-effort), all others open app/event + clipboard copy | Primary-source-confirmed prefill exists only for FanDuel + DraftKings, and is fragile; everything else has no public prefill. Honest labels, always a copyable-text fallback. |
| PnL source of truth | Bets actually placed only (`app_bet`) | Matches Pikkit; "my money's P&L." Full High-pick virtual ledger stays in the dashboard. |
| Live tracking | In-game scores + app-side auto-grade, in MVP | Computed app-side by joining open bets to a sports-data feed (the same way Pikkit does it). ~80% of the data layer already exists in this repo. |
| Capture | One-tap confirm (prefilled from our pick) + screenshot AI (Phase 2) | 95% of bets are our own recommended picks, so we already know player/market/line/side/book — the user confirms stake + filled odds only. Screenshot-AI catches off-feed bets later. |
| Security / access | Cloudflare Tunnel + Access (service token), zero inbound ports | Outbound-only QUIC; no EC2 inbound ports open; service token auth for the app. Requires a domain on Cloudflare. |
| Domain | Buy ~$10/yr at Cloudflare Registrar | Named persistent tunnel needs a Cloudflare-hosted domain; at-cost registration. |
| Push | Apple Developer Program ($99/yr) + Expo Push + TestFlight install | $99 is mandatory for APNs (no free workaround). Expo brokers APNs; TestFlight is the durable single-device install path. |
| Tabs | 픽 / 내 베팅 (calendar top, live bottom) / 설정 | Calendar + live merged into one "my bets" screen per user preference. |

## Architecture

Existing pipeline is **unchanged**. We add an app layer on top and swap the Telegram
sender/listener for a push worker + app capture.

```
┌────────────────────┐    Cloudflare Tunnel (outbound-only QUIC, 0 inbound ports)
│  iOS app (Expo/RN)  │◄──HTTPS + CF Access service token──►  api.<domain>  ──► FastAPI :8000 (EC2)
│  - Picks feed       │                                                          │
│  - Pick detail      │                                          reads/writes:   │
│  - 내 베팅 (cal+live)│◄── Expo Push ──┐                         softbook_paper.db
│  - Settings         │                │                          ├─ paper_bet_high  (existing: scan High picks)
└────────────────────┘                │                          ├─ pick_state       (NEW: bet/dismissed overrides)
                            exp.host/--/api/v2/push/send          ├─ app_bet          (NEW: captured bets + live status)
                                       ▲                          ├─ filter_prefs     (NEW)
                          ┌────────────┴────────────┐             └─ push_token       (NEW)
                          │  EC2 push worker         │           softbook_ev.db
                          │ (replaces Telegram send) │             └─ softbook_ev     (existing: EV snapshots)
                          │  triggers: new High /    │     ┌──────────────────────────┐
                          │  reverse-middle /        │     │ EC2 live-grade poller     │
                          │  settlement result       │     │ open app_bet ⨝ MLB StatsAPI│
                          └──────────────────────────┘     │ /ESPN box score → progress│
                                                           │ + auto-grade (app-side)   │
   Existing 20-min scan / EV calc / settlement ──unchanged──┴──────────────────────────┘
```

### Components and responsibilities

Each component has one purpose, a defined interface, and can be tested independently.

1. **FastAPI read/write service** (`scripts/softbook_app_api.py`, new) — HTTP API over the
   SQLite DBs. Stateless; reuses `softbook_ev_core.event_odds()` for on-demand odds. No
   business logic beyond composing feed = current High picks − pick_state + cooled/expired
   classification.
2. **Deep-link builder** (`core/softbook/deeplink.py`, new) — pure function: given a pick +
   provider link/SID data from The Odds API (`includeLinks=true&includeSids=true`), returns a
   tiered link (FD/DK betslip prefill → event link → app home) plus a copyable text string.
   No I/O; unit-testable.
3. **Push worker** (`scripts/softbook_push_worker.py`, new) — replaces the Telegram sender.
   Watches for new High picks / reverse-middle pairs / settlement results and POSTs to the
   Expo push API. Prunes tokens reported `DeviceNotRegistered`.
4. **Live-grade poller** (`scripts/softbook_live_grade.py`, new) — for each open `app_bet`,
   maps it to a live game via the existing MLB StatsAPI / ESPN collectors, computes
   current-stat-vs-line progress, and projects status (winning/losing/won/lost). App-side and
   advisory; final settlement still comes from the existing settlement backfill.
5. **Capture endpoints** — `POST /bets` (one-tap confirm) writes `app_bet`; `POST /bets/scan`
   (Phase 2) takes a bet-slip image, calls a vision model, returns a structured bet for
   confirmation.
6. **iOS app** (`app/`, new Expo project) — 3 tabs. Talks only to the FastAPI service; holds
   the CF Access service token + push token in the Keychain.

### Data flow — placing a bet (the core loop)

1. Push worker detects a new High pick → Expo push ("New High: Nimmo Bases Over 1.5, EV 7.4%").
2. User opens the app → **픽 feed** (current High picks minus bet/dismissed, filtered).
3. Taps a pick → **detail**; app calls `GET /picks/{id}/refresh` → single-event Odds API refetch
   → per-book fresh odds + deep-link/SID data.
4. Taps a book row → deep-link builder opens FanDuel/DraftKings with the slip prefilled (or
   opens the app/event + copies bet text for other books).
5. User places the bet in the book, returns, taps **"베팅함"** → confirm sheet pre-filled
   (player/market/line/side/book/recommended odds known) → user adjusts stake + filled odds →
   `POST /bets` writes `app_bet`. Pick leaves the feed (`pick_state` = bet).
6. Live-grade poller tracks the open bet in-game; settlement backfill finalizes win/loss;
   push worker sends the settlement result.

## Data Model

New tables in `softbook_paper.db` (pipeline tables untouched). Pick identity is the existing
natural key `(event_id, market, side, point, player)`.

```sql
-- User overrides on the live scan feed. Without this, a dismissed/bet pick re-appears
-- every 20-min scan.
CREATE TABLE pick_state (
    event_id TEXT, market TEXT, side TEXT, point REAL, player TEXT,
    state TEXT NOT NULL,              -- 'bet' | 'dismissed'
    updated_at TEXT NOT NULL,
    PRIMARY KEY (event_id, market, side, point, player)
);

-- Bets actually placed. Source of truth for calendar PnL + live tracking.
-- Supersedes the Telegram-keyed real_fill table (real_fill data migrated in).
CREATE TABLE app_bet (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT, sport TEXT, home TEXT, away TEXT, commence_time TEXT,
    market TEXT, side TEXT, point REAL, player TEXT, label TEXT,
    book TEXT,                        -- book the bet was actually placed at
    rec_odds REAL,                    -- odds the pick recommended (for slippage)
    fill_odds REAL,                   -- actual odds the user got
    stake REAL,
    placed_at TEXT,
    source TEXT,                      -- 'one_tap' | 'screenshot' | 'manual'
    -- live + settlement
    live_status TEXT,                 -- 'pending'|'winning'|'losing'|'won'|'lost'|'push'|'void'
    live_progress TEXT,              -- JSON: {current, needed, game_state} for the ring
    settled INTEGER DEFAULT 0, won INTEGER, pnl REAL, settled_at TEXT
);

CREATE TABLE filter_prefs (
    id INTEGER PRIMARY KEY CHECK (id = 1),   -- single row
    min_ev REAL DEFAULT 0.0,
    sports_json TEXT,                 -- enabled sports
    muted_books_json TEXT,
    sort TEXT DEFAULT 'ev'            -- 'ev'|'time'|'n_books'
);

CREATE TABLE push_token (
    token TEXT PRIMARY KEY,           -- ExponentPushToken[...]
    updated_at TEXT, active INTEGER DEFAULT 1
);
```

**Feed composition (server-side):** active feed = rows from the latest scan's High picks
(`paper_bet_high` / `softbook_ev`) **minus** any `pick_state` in {bet, dismissed}, with each
remaining pick classified as `active` / `cooled` (present in pick_state? no — but EV dropped
below High threshold or line moved on latest scan) / `expired` (commence_time passed), then
filtered by `filter_prefs`. Cooled picks re-activate automatically if they re-qualify as High.

## Backend API (FastAPI)

| Method + path | Purpose |
|---|---|
| `GET /picks` | Active feed (filters applied). Returns active + collapsed cooled/dismissed counts. |
| `GET /picks/{id}` | Pick detail with last-scan per-book odds. |
| `GET /picks/{id}/refresh` | On-demand single-event Odds API refetch (scoped to the pick's markets) → fresh per-book odds + deep-link/SID data. Reuses `softbook_ev_core.event_odds()`. |
| `POST /picks/{id}/dismiss` | Set `pick_state` = dismissed. Reverse-middle: dismissing/betting one side auto-dismisses the opposite. |
| `POST /bets` | One-tap confirm → write `app_bet`, set `pick_state` = bet. |
| `POST /bets/scan` | (Phase 2) image → vision model → structured bet for confirmation. |
| `GET /mybets` | Calendar PnL aggregation + open bets with live status. |
| `GET /settings` / `PUT /settings` | Read/write `filter_prefs`. |
| `POST /push/register` | Store/refresh the device's Expo push token (called every app launch). |

Auth: every request carries the CF Access service-token headers (validated at Cloudflare's
edge) plus a long-lived app bearer in `Authorization`. FastAPI also validates the
`Cf-Access-Jwt-Assertion` (aud + iss) so the origin enforces auth even if the edge policy is
misconfigured.

## Deep-Link Builder (the load-bearing, honest part)

Research (incl. adversarial verification) conclusion: bet-slip **prefill is only
primary-source-confirmed for FanDuel and DraftKings, and is fragile in practice** (can open to
app home, breaks inside in-app webviews, parlay links fail). Our bets are single +EV legs —
the case that works best. Design for graceful failure:

- **Tier 1 (FD / DK only, best-effort prefill):** emit the **provider-returned** link from
  The Odds API (`includeLinks=true&includeSids=true`) — do not hand-build the host (FanDuel uses
  per-state hosts; provider links absorb NY host drift). DraftKings `dksb://sb/addbet/<outcomeId>`
  with web `/event?outcomes=` fallback.
- **Tier 2 (all other books):** open the event page (universal link) or the app; no prefill
  promised. UI label says "앱 열기 + 정보복사", not "원탭 베팅".
- **Always:** render copyable bet text (book · market · selection · line · odds) so a failed
  prefill degrades to manual entry, never a dead end.
- **Open via the system handler** (`Linking.openURL`), never an in-app webview.
- **On-device verification gate:** "✓ 슬립 프리필" is shown for a book only after it's been
  verified to actually populate the slip on the real NY iPhone with the app installed. The AASA
  wildcard proves the app *opens*, not that the slip *fills*.

## Capture

True zero-touch sync is out of scope (see Non-Goals). The chosen model gets ~95% of the way
to "automatic" without credentials or $500/mo:

- **One-tap confirm (Phase 1, primary):** for our recommended picks, the confirm sheet is
  pre-filled with player/market/line/side/book/recommended-odds (we generated the pick). User
  adjusts **stake + actual filled odds** only → save. `rec_odds` vs `fill_odds` gives slippage
  for free (carries the existing fill-listener's measurement forward).
- **Screenshot AI (Phase 2, for off-feed bets):** snap the bet-slip → vision model parses
  selection/line/odds/stake → user confirms. No credential linking, covers any book/prop,
  ~cents/bet.
- Manual entry always available as the floor.

## Live Tracking & Auto-Grade

App-side, advisory; reuses repo assets. For each open `app_bet`:
1. Map to the live game by league + date + player/team (normalization layer; fall back to
   `pending` when unmatched — name spellings, doubleheaders, postponed games).
2. Poll MLB StatsAPI (no auth) / ESPN box score every ~30-60s for the player's current stat
   (total bases, K, HR, PTS/REB/AST) — the repo already collects these.
3. Compute progress = current vs line; project `live_status`. Store `live_progress` JSON for
   the ring ("0 of 2 total bases, Mid 2nd").
4. Final win/loss still comes from the existing settlement backfill; projected status is
   advisory and reconciled at settlement (handles pushes, voids, scratches, rain delays).

## Push Notifications

- **$99/yr Apple Developer Program** (mandatory; free personal teams cannot receive APNs).
- **Expo Push:** app calls `getExpoPushTokenAsync({projectId})` on the physical device each
  launch, POSTs the token to `POST /push/register`. Worker sends via
  `POST https://exp.host/--/api/v2/push/send`, pulls receipts, prunes `DeviceNotRegistered`.
- **Triggers:** new High pick + reverse-middle warning + settlement result (Phase 1);
  price-move/cashout opportunity (Phase 2).
- **Actionable categories:** "✓ 베팅함" / "✕ 관심없음" buttons on High-pick notifications
  (replaces the Telegram reply-to-record flow). "베팅함" routes into the confirm sheet.
- Use normal alert pushes (visible) for time-critical picks; do not rely on throttled silent
  pushes.
- **Install longevity:** TestFlight build (~90 days) — set a reminder to re-upload, or the
  alerting app silently stops.

## Security & Deployment

- **Cloudflare Tunnel** (`cloudflared` systemd service, config at `/etc/cloudflared/config.yml`)
  maps `api.<domain>` → `http://localhost:8000`. Outbound-only QUIC; **strip all inbound EC2
  security-group / ufw API rules** (tunnel needs none).
- **Cloudflare Access** self-hosted app over the tunnel hostname, one policy with action
  **Service Auth** + the service token. (Optional second policy: One-Time PIN + your email for
  occasional browser access.)
- **Domain:** ~$10/yr at Cloudflare Registrar, nameservers on Cloudflare Free DNS.
- iOS app stores `CF-Access-Client-Id` / `CF-Access-Client-Secret` in the Keychain, attaches
  them to every request.
- **Break-glass:** keep a documented way back in (temporary SG rule or a Tailscale sidecar) in
  case of a Cloudflare edge incident, since all inbound ports are closed.
- **Latency watch:** route through a Cloudflare colo near NYC (EC2 stays us-east-2); measure
  real round-trip vs direct — tunnel overhead is usually 2-8ms but can spike to 100-260ms on
  bad routing. If it ever hurts fills, switch to the Tailscale path.

## Costs

| Item | Cost |
|---|---|
| Apple Developer Program | $99 / year (mandatory for push) |
| Domain (Cloudflare Registrar) | ~$10 / year |
| Cloudflare Tunnel + Access (1 user) | $0 |
| The Odds API on-demand refetch | ~10-22k credits/mo — absorbed by existing plan |
| Vision model (Phase 2 screenshot) | ~cents/bet at personal volume |

## Phasing

**Phase 1 — MVP (full Telegram replacement):**
- FastAPI read/write API + Cloudflare Tunnel + Access + domain.
- Expo app: 픽 feed (filters from the start: min-EV, sport toggles, book mute, sort) +
  dismiss/cooled lifecycle; pick detail + on-demand odds refetch + tiered deep-link
  (FD/DK prefill, others open+copy); one-tap confirm capture.
- Push worker: new High + reverse-middle + settlement result, with actionable buttons.
- 내 베팅: calendar PnL (top) + **live in-game tracking & auto-grade** (bottom).
- Decommission Telegram alert + fill-listener once the app is verified.

**Phase 2 — convenience & richness:**
- Screenshot AI capture for off-feed bets.
- Price-move / cashout push.
- Filter-rule persistence (saved mute rules) and advanced triage.
- Polish.

## Risks & Mitigations

- **Deep-link prefill unreliable / book-specific.** Mitigate: provider-returned links only,
  single-leg, system handler, always copyable-text fallback, on-device verification gate before
  showing the prefill label. Treat as best-effort, never promised.
- **Push token death / install expiry (quiet failure).** Re-register token each launch, prune
  dead tokens, calendar reminder for TestFlight re-upload. (Mirrors the repo's past
  ufw-blocked-cloud-routine quiet-failure lesson.)
- **Cloudflare edge dependency** (all inbound closed). Keep a break-glass path.
- **Live-grade false positives** (push/void/scratch shown as won/lost). Status is advisory;
  reconcile against final settlement.
- **Player/game name mapping** is fiddly (spellings, doubleheaders, delays). Normalization
  layer + fall back to `pending` when unmatched.
- **Capture under-counting biases ROI.** One-tap confirm is near-100% fidelity for our picks;
  do not silently drop bets. (Mirrors the truncation-bias / n-too-small lessons.)
- **Ledger integrity.** `app_bet` rows are bet records; never delete/reprice them for
  measurement hygiene — segment instead. (Carries the existing ledger-rows-are-bet-records
  rule forward.)

## Open Questions (resolve during implementation)

- Migration of existing `real_fill` rows into `app_bet` (field mapping, dedup keys).
- Exact NY FanDuel betslip host (verify via provider-returned link on-device).
- Which Odds API plan tier after adding on-demand load (meter `x-requests-remaining`).
- Whether the live-grade poller runs as its own systemd timer or folds into an existing one.

## Testing Approach

- **Deep-link builder:** pure unit tests over provider link/SID fixtures (FD/DK prefill,
  fallback tiers, copyable text); plus a manual on-device verification checklist per book.
- **Feed composition:** unit tests that bet/dismissed/cooled/expired correctly filter the live
  scan set; reverse-middle auto-dismiss.
- **Live-grade:** fixture-based tests mapping a known box-score state to expected progress/status,
  including unmatched → pending and push/void edge cases.
- **API:** endpoint tests against a seeded SQLite copy (never the live 8.8M-row bot.db).
- **Push:** integration test sending to a real device token through the Expo API in a staging run.
- Reuse the repo's existing test patterns (`tests/test_softbook_*`).
