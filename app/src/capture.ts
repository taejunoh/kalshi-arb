// app/src/capture.ts
import type { BetIn, Pick } from "./api/types";

export interface CaptureInputs {
  book?: string;
  stake?: number;
  fillOdds?: number;
}

/** Build the POST /bets body from a recommended pick + the only three fields the
 *  user edits (book / stake / filled odds). Everything else is carried from the pick. */
export function buildBetIn(pick: Pick, inputs: CaptureInputs): BetIn {
  return {
    event_id: pick.event_id,
    sport: pick.sport,
    home: pick.home,
    away: pick.away,
    commence_time: pick.commence_time,
    market: pick.market,
    side: pick.side,
    point: pick.point,
    player: pick.player,
    label: pick.label,
    book: inputs.book ?? pick.book,
    rec_odds: pick.odds,
    fill_odds: inputs.fillOdds ?? pick.odds,
    stake: inputs.stake ?? null,
    source: "one_tap",
  };
}
