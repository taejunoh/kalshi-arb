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

// Books whose bet-slip PREFILL has been verified on the real NY iPhone (app installed)
// to actually populate the slip. Until a book label is in here, even a server "prefill"-tier
// link is shown as plain "앱 열기", never "✓ 슬립 프리필". See DEEPLINK_VERIFICATION.md.
// On-device verified (NY iPhone, 2026-06-15): these three open to a PRE-FILLED slip.
//   • BetRivers — the server now emits a fixed Kambi-coupon prefill link (was a malformed-URL
//     bug, not a dead end). It opens the mobile-WEB slip (no native applinks AASA). PENDING:
//     re-verify on-device, then add "BetRivers" here.
//   • BetMGM / theScore — prefill structurally NOT achievable (host redirect drops the
//     selection / native router ignores the web param). Stay out; honest "앱 열기 + 복사".
export const VERIFIED_PREFILL_BOOKS: string[] = ["FanDuel", "DraftKings", "Caesars"];

export const DEFAULT_SETTINGS: Settings = { min_ev: 0, sports: null, muted_books: null, sort: "ev" };

// EV thresholds for the filter stepper (matches the pipeline's 1–8% band).
export const MIN_EV_STEPS = [0, 0.01, 0.02, 0.03, 0.04, 0.05];
