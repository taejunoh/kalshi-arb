// app/src/config.ts
import type { Settings } from "./api/types";

// Must match scripts/softbook_push_worker.py categories + the buttons this app registers.
export const CATEGORY_HIGH = "high_pick";
export const CATEGORY_SETTLE = "settle";
export const CATEGORY_PRICE_MOVE = "price_move";
export const ACTION_BET = "BET_PLACED";
export const ACTION_DISMISS = "NOT_INTERESTED";

// Canonical NY book labels (from core.NY_LEGAL values) — used by the mute UI.
export const ALL_BOOKS = [
  "FanDuel", "DraftKings", "BetMGM", "Caesars", "BetRivers", "Fanatics", "theScore", "BallyBet",
];

export const SPORT_LABELS: Record<string, string> = {
  baseball_mlb: "MLB",
  basketball_wnba: "WNBA",
  basketball_nba: "NBA",
};

// Books whose bet-slip PREFILL has been verified on the real NY iPhone (app installed) to
// actually populate the slip IN THE NATIVE APP. Until a book label is in here, even a server
// "prefill"-tier link is shown as plain "복사 + 직접 앱 열기", never "✓ 슬립 프리필". See
// DEEPLINK_VERIFICATION.md.
// On-device verified (NY iPhone): these open the native app to a PRE-FILLED slip.
//   • BetMGM / theScore — prefill structurally NOT achievable (host redirect drops the
//     selection / native router ignores the web param). Stay out; honest "복사 + 직접 앱 열기".
//   • BetRivers — COPY-ONLY as of 2026-07-01 (do not re-promote). No applinks AASA AND no
//     custom URL scheme (on-device verified: betrivers://, rushstreet://, rsi://, … all
//     unregistered), so there is NO way to open its native app — https only launches Safari.
//     The server emits no url (COPY_ONLY_BOOKS), so tapping it just copies the search entity
//     and the user opens the BetRivers app manually. Row shows "복사 · 앱에서 검색".
export const VERIFIED_PREFILL_BOOKS: string[] = ["FanDuel", "DraftKings", "Caesars"];

export const DEFAULT_SETTINGS: Settings = { min_ev: 0, sports: null, muted_books: null, sort: "ev" };

// EV thresholds for the filter stepper (matches the pipeline's 1–8% band).
export const MIN_EV_STEPS = [0, 0.01, 0.02, 0.03, 0.04, 0.05];

// Flat per-bet stake ($). The policy is "bet small, NO size-up" (≈BE bonus-clearing
// vehicle), so every BET pick shows the SAME unit — not an EV/Kelly-scaled amount.
// Stored app-locally (storage.getUnitStake); editable in Settings.
export const DEFAULT_UNIT_STAKE = 20;
