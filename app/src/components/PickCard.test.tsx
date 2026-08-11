// app/src/components/PickCard.test.tsx
import { render, screen, fireEvent } from "@testing-library/react-native";
import { PickCard } from "./PickCard";
import type { Pick } from "../api/types";

const PICK: Pick = {
  id: "E1|batter_total_bases|Over|1.5|Brandon Nimmo",
  status: "active",
  event_id: "E1",
  sport: "baseball_mlb",
  home: "MIA",
  away: "NYM",
  commence_time: "2099-01-01T00:00:00Z",
  market: "batter_total_bases",
  side: "Over",
  point: 1.5,
  player: "Brandon Nimmo",
  label: "Brandon Nimmo TB Over 1.5",
  ev: 0.074,
  book: "theScore",
  odds: 2.2,
  conf: 60,
  n_books: 9,
  // Served by /picks (core.odds_bars), not derived in the app. These are that
  // function's actual output for the seeded ev=0.074 @ 2.2 (fair≈0.4882).
  min_odds: 2.068901,
  be_odds: 2.048417,
};

test("renders label, book@odds and EV", async () => {
  await render(<PickCard pick={PICK} onOpen={() => {}} onBet={() => {}} onDismiss={() => {}} />);
  expect(screen.getByText("Brandon Nimmo TB Over 1.5")).toBeOnTheScreen();
  expect(screen.getByText(/theScore/)).toBeOnTheScreen();
  expect(screen.getByText("+7.4%")).toBeOnTheScreen();
});

test("shows the 최소/본전 odds bars so you can shop any book (same numbers as the Telegram alert)", async () => {
  await render(<PickCard pick={PICK} onOpen={() => {}} onBet={() => {}} onDismiss={() => {}} />);
  expect(screen.getByText(/최소 \+107 · 본전 \+105/)).toBeOnTheScreen();
});

test("omits the 최소/본전 line when the API sent no bars (unknown ev/price, or an older payload)", async () => {
  await render(<PickCard pick={{ ...PICK, min_odds: null, be_odds: null }} onOpen={() => {}} onBet={() => {}} onDismiss={() => {}} />);
  expect(screen.queryByText(/본전/)).toBeNull();
});

test("does NOT derive the bars from ev/odds — a payload without them shows nothing", async () => {
  // Guards the whole point of serving them: if the app ever recomputes locally it
  // reintroduces a second copy of EV_MIN that can silently drift from the policy.
  const { min_odds, be_odds, ...noBars } = PICK;
  await render(<PickCard pick={noBars} onOpen={() => {}} onBet={() => {}} onDismiss={() => {}} />);
  expect(screen.queryByText(/최소/)).toBeNull();
});

test("shows the matchup so you can tell which game it is", async () => {
  await render(<PickCard pick={PICK} onOpen={() => {}} onBet={() => {}} onDismiss={() => {}} />);
  expect(screen.getByText("NYM @ MIA")).toBeOnTheScreen();
});

test("omits the matchup line when teams are unknown", async () => {
  await render(<PickCard pick={{ ...PICK, home: null, away: null }} onOpen={() => {}} onBet={() => {}} onDismiss={() => {}} />);
  expect(screen.queryByText(/@/)).toBeNull();
});

test("fires onBet and onDismiss", async () => {
  const onBet = jest.fn();
  const onDismiss = jest.fn();
  await render(<PickCard pick={PICK} onOpen={() => {}} onBet={onBet} onDismiss={onDismiss} />);
  await fireEvent.press(screen.getByText("베팅"));
  // The inline 관심없음 button (kept as the accessible fallback to the swipe action).
  await fireEvent.press(screen.getByTestId("inline-dismiss"));
  expect(onBet).toHaveBeenCalledWith(PICK);
  expect(onDismiss).toHaveBeenCalledWith(PICK);
});

test("shows the 🟣 BET badge when should_bet is true", async () => {
  // Exact string, not a regex: the retire-watch edit (2026-08-05) left the
  // ternary's second line rendering as LITERAL SOURCE (": pick.should_bet ?
  // \"🟣 BET…\"}") and /🟣 BET/ matched inside it — the broken badge passed
  // its own test for six days and shipped over OTA.
  await render(<PickCard pick={{ ...PICK, should_bet: true }} onOpen={() => {}} onBet={() => {}} onDismiss={() => {}} />);
  expect(await screen.findByText("🟣 BET · first-entry game")).toBeOnTheScreen();
  expect(screen.queryByText(/pick\.should_bet/)).toBeNull();   // no source text on screen
});

test("legacy regex-shaped check kept for the SKIP path", async () => {
  await render(<PickCard pick={{ ...PICK, should_bet: true }} onOpen={() => {}} onBet={() => {}} onDismiss={() => {}} />);
  expect(await screen.findByText(/BET/)).toBeOnTheScreen();
});

test("shows the ⚪ SKIP badge when should_bet is false", async () => {
  await render(<PickCard pick={{ ...PICK, should_bet: false }} onOpen={() => {}} onBet={() => {}} onDismiss={() => {}} />);
  expect(await screen.findByText(/SKIP/)).toBeOnTheScreen();
});

test("shows the flat unit stake on the bet button for BET picks", async () => {
  await render(<PickCard pick={{ ...PICK, should_bet: true }} unitStake={20} onOpen={() => {}} onBet={() => {}} onDismiss={() => {}} />);
  expect(await screen.findByText("베팅 $20")).toBeOnTheScreen();
});

test("does NOT show a stake on SKIP picks (not recommended)", async () => {
  await render(<PickCard pick={{ ...PICK, should_bet: false }} unitStake={20} onOpen={() => {}} onBet={() => {}} onDismiss={() => {}} />);
  expect(screen.getByTestId("bet-button")).toHaveTextContent("베팅");
  expect(screen.queryByText("베팅 $20")).toBeNull();
});

test("shows no BET/SKIP badge when the flag is absent", async () => {
  await render(<PickCard pick={PICK} onOpen={() => {}} onBet={() => {}} onDismiss={() => {}} />);
  expect(screen.queryByText(/🟣 BET|⚪ SKIP/)).toBeNull();
});

// ---- the hold advisory is retired (2026-08-05) ----
// ML at EV>=5% carried a "🟡 관찰 · 베팅 보류" badge from 7/22, because High ML
// in that band had run 4-21. High ML is no longer bet at all, and re-measured on
// the cohort we DO bet the pathology does not reproduce (Med, n=32, WR 50.0% vs
// BE 42.0%, +$22.23). The band now rides with every other BET.

test("a former watch pick renders as a plain BET, stake and all", async () => {
  await render(<PickCard pick={{ ...PICK, market: "h2h", ev: 0.07, should_bet: true }}
    unitStake={20} onOpen={() => {}} onBet={() => {}} onDismiss={() => {}} />);
  expect(await screen.findByText(/🟣 BET/)).toBeOnTheScreen();
  expect(screen.getByText("베팅 $20")).toBeOnTheScreen();
});

test("no surface still renders the hold badge or its note", async () => {
  await render(<PickCard pick={{ ...PICK, market: "h2h", ev: 0.07, should_bet: true }}
    unitStake={20} onOpen={() => {}} onBet={() => {}} onDismiss={() => {}} />);
  expect(screen.queryByText(/🟡 관찰/)).toBeNull();
  expect(screen.queryByText(/전진관찰/)).toBeNull();
  expect(screen.queryByText(/보류/)).toBeNull();
});
