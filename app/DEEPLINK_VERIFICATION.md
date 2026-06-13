<!-- app/DEEPLINK_VERIFICATION.md -->
# Deep-link prefill verification (gates the "✓ 슬립 프리필" label)

The server returns a `prefill`-tier link for FanDuel and DraftKings, but a prefill
link only *proves the app opens* — the AASA wildcard does NOT prove the slip fills.
Research (2026): FanDuel's `addToBetslip` universal link is confirmed but best-effort;
**DraftKings `dksb://` is unverified by any public source.** So a book's label stays
"앱 열기 + 복사" until you confirm the slip actually populates on YOUR phone.

## Procedure (per book: FanDuel, then DraftKings)
1. Install the production TestFlight build on the NY iPhone, with the sportsbook app
   installed and logged in.
2. Open a High pick → 픽 상세 → tap the FanDuel (then DraftKings) row.
3. Confirm the sportsbook app opens AND the exact selection + line is pre-loaded in
   the bet slip (not just the app home / event page).
4. If — and only if — the slip is correctly pre-filled, add that book's label to
   `VERIFIED_PREFILL_BOOKS` in `src/config.ts` (e.g. `["FanDuel"]`). Rebuild.
5. If it opens to home/event or the wrong selection, leave it OUT — the honest
   "앱 열기 + 복사" label + copyable text is the correct UX. Re-test after book-app
   updates; prefill can regress silently.

## Notes
- Always test via the system handler (tapping the row → `Linking.openURL`), never an
  in-app webview.
- Single-leg only (these picks are single legs — the best case for prefill).
- `LSApplicationQueriesSchemes: ["dksb", ...]` (app.json) must be present or
  `canOpenURL('dksb://…')` throws and the row silently falls back to copy.
