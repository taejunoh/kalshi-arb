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
  // Advisory overlay on a BET verdict (core.is_ev_watch, 2026-07-22): ML at stated
  // EV >= 5% is under forward review — that cell ran 4-21 (-60.8%) in both June and
  // July, while the same band was fine on spread/total. should_bet stays TRUE (the
  // rule did not change, so the paper ledger keeps scoring these and the forward
  // sample accrues); this flag only swaps the badge and hides the stake so the card
  // says hold. Optional: a pre-deploy API omits it.
  ev_watch?: boolean;
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
