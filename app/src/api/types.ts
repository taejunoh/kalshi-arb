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
  // True when this (event,market,player)'s earliest snapshot was already High — the
  // +16.6% ROI "first-entry" subset. False = late-crosser (drifted into High, ROI≈0).
  first_entry?: boolean;
}

export interface PicksResponse {
  picks: Pick[];
  counts: Record<string, number>;
  cooled: Pick[];
}

export type DeeplinkTier = "prefill" | "event" | "home";
export interface Deeplink {
  tier: DeeplinkTier;
  url: string | null;
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

export interface LiveProgress {
  current: number | null;
  needed: number;
  remaining?: number;
  game_state: string | null;
}

export interface OpenBet {
  id: number;
  event_id: string;
  sport: string | null;
  home: string | null;
  away: string | null;
  commence_time: string | null;
  market: string;
  side: string;
  point: number | null;
  player: string | null;
  label: string | null;
  book: string | null;
  fill_odds: number | null;
  stake: number | null;
  live_status: string | null;
  live_progress: LiveProgress | null;
}

export interface MyBets {
  calendar: Record<string, number>;
  open: OpenBet[];
}

export type SortKey = "ev" | "time" | "n_books";
export interface Settings {
  min_ev: number;
  sports: string[] | null;
  muted_books: string[] | null;
  sort: SortKey;
}
