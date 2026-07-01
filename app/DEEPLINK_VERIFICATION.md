<!-- app/DEEPLINK_VERIFICATION.md -->
# Deep-link prefill verification (gates the "✓ 슬립 프리필" label)

The server returns a `prefill`-tier link for FanDuel and DraftKings, but a prefill
link only *proves the app opens* — the AASA wildcard does NOT prove the slip fills.
Research (2026): FanDuel's `addToBetslip` universal link is confirmed but best-effort;
**DraftKings `dksb://` is unverified by any public source.** So a book's label stays
"복사 + 직접 앱 열기" until you confirm the slip actually populates on YOUR phone.

## Procedure (per book)
1. Install the production TestFlight build on the NY iPhone, with the sportsbook app
   installed and logged in.
2. Open a High pick → 픽 상세 → tap the book's row.
3. Confirm the exact selection + line is pre-loaded in the bet slip **in the native app**
   (not just the app home / event page, and not a mobile-web slip in Safari — see BetRivers
   below for why web-only prefill does NOT qualify).
4. If — and only if — the slip is correctly pre-filled, add that book's label to
   `VERIFIED_PREFILL_BOOKS` in `src/config.ts` (e.g. `["FanDuel"]`). Rebuild.
5. If it opens to home/event or the wrong selection, leave it OUT — the honest
   "복사 + 직접 앱 열기" label + copyable text is the correct UX. Re-test after book-app
   updates; prefill can regress silently.

## Per-book status (verified 2026-06-15)
- **FanDuel / DraftKings / Caesars** — server emits a `prefill` link; on-device confirmed →
  in `VERIFIED_PREFILL_BOOKS`. (Caesars link carries `{state}` → substituted to `ny`.)
- **BetRivers** — **COPY-ONLY as of 2026-07-01 (do not re-promote, do not re-chase a link).**
  History: a working Kambi coupon `prefill` link exists but `ny.betrivers.com` serves NO
  `applinks` AASA, so it only opened a mobile-WEB slip in Safari (demoted from prefill
  2026-06-30). The event-tier fallback ALSO opened Safari (no AASA), which the user rejected.
  On-device 2026-07-01 we then confirmed BetRivers exposes **no custom URL scheme** either —
  `betrivers://`, `betriverssportsbook://`, `rushstreet://`, `rushstreetinteractive://`,
  `rsi://`, `playsugarhouse://` are all unregistered. So there is **no way to open the native
  app** from a link. BetRivers is now in `COPY_ONLY_BOOKS` (`scripts/softbook_deeplink.py`):
  the server emits `url=None`, tapping it copies only the search entity and opens nothing (no
  Safari), and the row shows "복사 · 앱에서 검색". The user opens the BetRivers app manually
  and pastes. If a future BetRivers app version adds an AASA or a scheme, re-test then.
- **BetMGM** — prefill NOT achievable (do not re-chase). The `options=` betslip link
  301-redirects `sports.{state}.betmgm.com → www.betmgm.com`, which DROPS the `?options=`
  param → homepage (selection lost, before any app/AASA matters); BetMGM's AASA is also broken
  (redirect + malformed appIDs) and no custom URL scheme exists. Server keeps it at `event`
  tier — the event link survives the redirect to the correct game page. Leave OUT.
- **theScore (espnbet)** — prefill NOT achievable (do not re-chase). AASA `["*"]` so the app
  DOES open, but `market_selection_id[0]=…` is theScore's WEB SPA quick-bet format the native
  in-app router ignores; no custom scheme, and the real external path (Branch.io) isn't
  constructable from a raw selection id. Server keeps it at `event` tier (opens the app on the
  game page). Leave OUT.

## Notes
- Always test via the system handler (tapping the row → `Linking.openURL`), never an
  in-app webview.
- Single-leg only (these picks are single legs — the best case for prefill).
- `LSApplicationQueriesSchemes: ["dksb", ...]` (app.json) must be present or
  `canOpenURL('dksb://…')` throws and the row silently falls back to copy.
