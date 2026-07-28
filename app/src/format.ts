// app/src/format.ts
export function americanOdds(px: number | null | undefined): string {
  // Decimal -> the American number US books actually show (+underdog / -favorite),
  // so the app matches the sportsbook screen 1:1. Mirrors the backend
  // softbook_ev_recorder._american (Telegram/push use the same).
  if (px == null || px <= 1) return "—";
  const a = px >= 2 ? Math.round((px - 1) * 100) : Math.round(-100 / (px - 1));
  return a > 0 ? `+${a}` : `${a}`;
}

// Parse a user-entered fill price: American ("+105" / "-120" / bare "150") OR
// decimal ("1.98") -> decimal odds, null if not a valid odds. Mirrors the Telegram
// reply parser softbook_fill_listener._to_decimal so the app and Telegram accept
// the exact same input (decimal odds live in 1.0–100; bare ints >=100 are american).
export function parseOddsToDecimal(input: string | null | undefined): number | null {
  if (input == null) return null;
  const tok = input.trim();
  if (!tok) return null;
  if (/^[+-]\d+$/.test(tok)) {
    const am = parseInt(tok, 10);
    if (Math.abs(am) < 100) return null;
    return am > 0 ? 1 + am / 100 : 1 + 100 / -am;
  }
  const v = Number(tok);
  if (!Number.isFinite(v)) return null;
  if (v >= 100 && Number.isInteger(v)) return 1 + v / 100; // unsigned american, e.g. "150"
  return v > 1.0 && v < 100 ? v : null;
}

// Floor bar of the softbook pipeline (softbook_ev_core.EV_MIN). A pick is "+EV
// enough" for the ROI≥1% policy only while priced at or above this EV. Mirrored
// locally so the card can show the same 최소/본전 the Telegram alert does.
const EV_MIN = 0.01;

// From a pick's EV and the decimal price it was quoted at, recover the sharp fair
// win-probability and return the two american-odds bars the Telegram alert shows:
//   • be  (본전)  — breakeven: the price where EV hits 0 (1/fair).
//   • min (최소)  — floor: the worst price that still clears the ROI≥1% bar
//                  ((1+EV_MIN)/fair). "어느 북이든 최소 위면 OK."
// Mirrors softbook_ev_recorder._min_odds_line. Returns null when ev/odds are
// missing or the price isn't a valid (>1) decimal.
export function minBreakevenOdds(
  ev: number | null | undefined,
  odds: number | null | undefined,
): { min: string; be: string } | null {
  if (ev == null || odds == null || odds <= 1) return null;
  const fair = (1 + ev) / odds; // sharp fair win probability implied by the EV
  if (!(fair > 0)) return null;
  return { min: americanOdds((1 + EV_MIN) / fair), be: americanOdds(1 / fair) };
}

export function evPct(ev: number | null | undefined): string {
  if (ev == null) return "—";
  return `${ev >= 0 ? "+" : "-"}${Math.abs(ev * 100).toFixed(1)}%`;
}

// "Away @ Home" — the game a pick belongs to. core.label() only describes the
// market (e.g. "Over 167.5" / "Boston Celtics ML"), so for totals & spreads you
// can't tell which game it is without this. Away first = standard US convention.
// null when no teams are known (e.g. a screenshot draft before it's matched).
export function matchup(away: string | null | undefined, home: string | null | undefined): string | null {
  const a = away?.trim();
  const h = home?.trim();
  if (a && h) return `${a} @ ${h}`;
  return a || h || null;
}

export function countdown(commenceISO: string, now: Date = new Date()): string {
  const start = new Date(commenceISO).getTime();
  const diffMs = start - now.getTime();
  if (diffMs <= 0) return "LIVE";
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "<1m";
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

export function countdownUrgent(commenceISO: string, now: Date = new Date()): boolean {
  const diffMs = new Date(commenceISO).getTime() - now.getTime();
  return diffMs > 0 && diffMs < 60 * 60000;
}
