// app/src/capture.test.ts
import { buildBetIn } from "./capture";
import type { Pick } from "./api/types";

const PICK: Pick = {
  id: "E1|batter_total_bases|Over|1.5|Brandon Nimmo", status: "active", event_id: "E1",
  sport: "baseball_mlb", home: "MIA", away: "NYM", commence_time: "2099-01-01T00:00:00Z",
  market: "batter_total_bases", side: "Over", point: 1.5, player: "Brandon Nimmo",
  label: "Brandon Nimmo TB Over 1.5", ev: 0.074, book: "theScore", odds: 2.2, conf: 60, n_books: 9,
};

test("buildBetIn carries pick fields and applies user inputs", () => {
  const bet = buildBetIn(PICK, { book: "FanDuel", stake: 5, fillOdds: 2.25 });
  expect(bet).toMatchObject({
    event_id: "E1", sport: "baseball_mlb", market: "batter_total_bases", side: "Over",
    point: 1.5, player: "Brandon Nimmo", label: "Brandon Nimmo TB Over 1.5",
    book: "FanDuel", rec_odds: 2.2, fill_odds: 2.25, stake: 5, source: "one_tap",
  });
});

test("buildBetIn defaults book/fill_odds to the pick's best when unedited", () => {
  const bet = buildBetIn(PICK, { stake: 2 });
  expect(bet.book).toBe("theScore");
  expect(bet.fill_odds).toBe(2.2);
  expect(bet.rec_odds).toBe(2.2);
});
