// app/src/api/types.ts
export type PickStatus = "active" | "cooled" | "expired" | "bet" | "dismissed";

export interface Pick {
  id: string; // "event_id|market|side|point|player"
  status: PickStatus;
  event_id: string;
  sport: string;
  home: string | null;
  away: string | null;
  commence_time: string;
  market: string;
  side: string;
  point: number | null;
  player: string | null;
  label: string;
  ev: number | null;
  book: string | null;
  odds: number | null;
  conf: number | null;
  n_books: number | null;
  // 최소/본전 bars in DECIMAL odds, computed server-side by core.odds_bars (the
  // same call the Telegram alert makes) and served on /picks. They encode EV_MIN
  // — a policy constant — so the app must not derive them locally; optional
  // because a payload predating the field, or an unknown ev/price, omits them and
  // the card then hides the line.
  min_odds?: number | null;
  be_odds?: number | null;
  // True when this (event,market,player)'s earliest snapshot was already High
  // (first-entry). Informational; the actionable verdict is should_bet.
  first_entry?: boolean;
  // BET verdict = FIRST-ENTRY GAME ONLY (core.is_bet: h2h/spread/total & first-entry;
  // props and late-crossers are false). Drives the card badge and the bet filter.
  should_bet?: boolean;
  // What this pick looked like when it ALERTED vs what it is worth NOW.
  //
  // `ev`/`odds` above are the pick's last HIGH snapshot, which is not the same
  // thing as its current price: once a pick drops out of High it stops
  // producing High rows, so that number silently freezes. These fields close
  // that gap — alert_* is the first High snapshot (the one that fired the
  // Telegram/push), now_* is the latest snapshot at ANY tier.
  //
  // All optional: a pre-deploy API omits them and the card falls back to the
  // single ev/odds line.
  alerted_at?: string | null;
  alert_ev?: number | null;
  alert_odds?: number | null;
  alert_book?: string | null;
  now_at?: string | null;
  now_ev?: number | null;
  now_odds?: number | null;
  now_book?: string | null;
  now_tier?: string | null;
  // Why a cooled pick is in the cooled bucket. "faded" = re-priced below High.
  // "gone" = it stopped appearing while its last snapshot was still High —
  // the book pulled the market, or the line moved and it became a different
  // pick. Measured 2026-08-05: 9 of 17 cooled picks were "gone", not "faded",
  // so a single "cooled" label was telling the user a bet had gone cold when
  // its last known EV had not moved at all.
  cooled_reason?: "faded" | "gone";
}

export interface PicksResponse {
  picks: Pick[];
  counts: Record<string, number>;
  cooled: Pick[];
  // Recently-commenced High picks (game started). Kept so a pick you were alerted
  // about stays findable after start. Optional: a pre-deploy API omits it.
  expired?: Pick[];
}

export type DeeplinkTier = "prefill" | "event" | "home";
export interface Deeplink {
  tier: DeeplinkTier;
  url: string | null;
  /** The clean SEARCH entity (player / team / matchup name) to paste into the book's search
   *  box — no book prefix, odds, or line, which all break search. Copied on open+copy tiers. */
  copy_text: string;
}

export interface BookQuote {
  book: string;
  odds: number;
  ev: number | null;
  deeplink: Deeplink;
}

export interface RefreshResponse {
  fair: number | null;
  books: BookQuote[];
  credits: number;
}

export interface BetIn {
  event_id: string;
  sport?: string | null;
  home?: string | null;
  away?: string | null;
  commence_time?: string | null;
  market: string;
  side: string;
  point?: number | null;
  player?: string | null;
  label?: string | null;
  book?: string | null;
  rec_odds?: number | null;
  fill_odds?: number | null;
  stake?: number | null;
  source: string;
}

export type SortKey = "ev" | "time" | "n_books";
export interface Settings {
  min_ev: number;
  sports: string[] | null;
  muted_books: string[] | null;
  sort: SortKey;
}

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

// ===== Triage / mute rules =====
export interface MuteRule {
  id: number;
  field: "player" | "market" | "sport" | "event";
  value: string;
  created_at: string;
}

// ===== 내 베팅 (app_bet 원장) =====
// A row of the bet ledger. `not_placed` = the user said this bet never happened
// (mis-tap, or the book rejected it). The row is NEVER deleted — it is a record —
// so the app renders it struck through instead of hiding it.
export interface AppBet {
  id: number;
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
  fill_odds: number | null;
  stake: number | null;
  live_status: string | null;
  live_progress: unknown;
  settled: number;
  won: number | null;
  pnl: number | null;
  settled_at: string | null;
  not_placed: number;
  not_placed_at: string | null;
}

export interface MyBetsResponse {
  /** day (YYYY-MM-DD) -> settled PnL, not_placed excluded */
  calendar: Record<string, number>;
  open: AppBet[];
  settled: AppBet[];
  not_placed: AppBet[];
}

/** Both directions of /bets/{id}/unmark — POST (disown) and DELETE (restore)
 *  answer in this same shape. pnl_delta/settled_day are non-null only when the
 *  bet had already settled AND the call actually changed something (a repeat
 *  call reports null, so a client applying the delta to a cached calendar
 *  cannot double-count) — the app names the calendar change in a confirm
 *  dialog. DELETE's delta is the opposite sign of POST's: it puts the PnL back. */
export interface UnmarkResponse {
  ok: boolean;
  pnl_delta: number | null;
  settled_day: string | null;
}

/** The only three fields the server accepts — anything else is a 422.
 *
 *  `stake`/`fill_odds` are NOT nullable here even though the server field is
 *  optional: PATCH /bets/{id} 422s an explicitly-null stake or fill_odds,
 *  because a placed bet without them isn't a record of anything and the
 *  settlement worker multiplies by both. `book` stays nullable — it's
 *  descriptive metadata, and the server already accepts a null book on
 *  bet creation. Don't widen stake/fill_odds back to `| null`; a "clear
 *  this field" edit interaction would compile cleanly and then 422. */
export interface BetPatch {
  book?: string | null;
  stake?: number;
  fill_odds?: number;
}
