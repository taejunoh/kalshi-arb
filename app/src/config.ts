// app/src/config.ts
import type { Settings } from "./api/types";

// Must match scripts/softbook_push_worker.py categories + the buttons this app registers.
export const CATEGORY_HIGH = "high_pick";
export const CATEGORY_SETTLE = "settle";
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
// to actually populate the slip. EMPTY until verified — see DEEPLINK_VERIFICATION.md.
// Until a book label is in here, even a server "prefill"-tier link is shown as plain
// "앱 열기", never "✓ 슬립 프리필". (Research: FanDuel addToBetslip is confirmed-but-best-effort;
// DraftKings dksb:// is UNVERIFIED by any source.)
export const VERIFIED_PREFILL_BOOKS: string[] = [];

export const DEFAULT_SETTINGS: Settings = { min_ev: 0, sports: null, muted_books: null, sort: "ev" };

// EV thresholds for the filter stepper (matches the pipeline's 1–8% band).
export const MIN_EV_STEPS = [0, 0.01, 0.02, 0.03, 0.04, 0.05];
