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
