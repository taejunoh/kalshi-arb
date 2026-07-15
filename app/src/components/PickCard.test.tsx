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
};

test("renders label, book@odds and EV", async () => {
  await render(<PickCard pick={PICK} onOpen={() => {}} onBet={() => {}} onDismiss={() => {}} />);
  expect(screen.getByText("Brandon Nimmo TB Over 1.5")).toBeOnTheScreen();
  expect(screen.getByText(/theScore/)).toBeOnTheScreen();
  expect(screen.getByText("+7.4%")).toBeOnTheScreen();
});

test("shows the 최소/본전 odds bars so you can shop any book (mirrors the Telegram alert)", async () => {
  await render(<PickCard pick={PICK} onOpen={() => {}} onBet={() => {}} onDismiss={() => {}} />);
  // ev=0.074, odds=2.2 → 최소 +107 · 본전 +105 (see format.minBreakevenOdds).
  expect(screen.getByText(/최소 \+107 · 본전 \+105/)).toBeOnTheScreen();
});

test("omits the 최소/본전 line when ev or price is unknown", async () => {
  await render(<PickCard pick={{ ...PICK, ev: null }} onOpen={() => {}} onBet={() => {}} onDismiss={() => {}} />);
  expect(screen.queryByText(/본전/)).toBeNull();
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

// ---- ev_watch: a BET the backend asks us to hold (core.is_ev_watch, 2026-07-22) ----
// should_bet stays TRUE — the bet rule did not change, so these keep scoring in the
// paper ledger. The card only has to make the hold visible and drop the stake.

test("shows the 🟡 관찰 badge instead of BET when ev_watch is set", async () => {
  await render(<PickCard pick={{ ...PICK, should_bet: true, ev_watch: true }} onOpen={() => {}} onBet={() => {}} onDismiss={() => {}} />);
  // anchored on the emoji: the explainer line below also contains "관찰"
  expect(await screen.findByText(/🟡 관찰/)).toBeOnTheScreen();
  expect(screen.queryByText(/🟣 BET/)).toBeNull();
  expect(screen.queryByText(/SKIP/)).toBeNull();          // held, NOT demoted to skip
});

test("explains why a watched pick is held", async () => {
  await render(<PickCard pick={{ ...PICK, should_bet: true, ev_watch: true }} onOpen={() => {}} onBet={() => {}} onDismiss={() => {}} />);
  expect(await screen.findByText(/ML EV≥5%/)).toBeOnTheScreen();
});

test("does NOT show a stake on watched picks ('$20' would contradict '보류')", async () => {
  await render(<PickCard pick={{ ...PICK, should_bet: true, ev_watch: true }} unitStake={20} onOpen={() => {}} onBet={() => {}} onDismiss={() => {}} />);
  expect(screen.getByTestId("bet-button")).toHaveTextContent("베팅");
  expect(screen.queryByText("베팅 $20")).toBeNull();
});

test("ev_watch on a SKIP pick changes nothing (a skip is already not recommended)", async () => {
  await render(<PickCard pick={{ ...PICK, should_bet: false, ev_watch: true }} unitStake={20} onOpen={() => {}} onBet={() => {}} onDismiss={() => {}} />);
  expect(await screen.findByText(/SKIP/)).toBeOnTheScreen();
  expect(screen.queryByText(/🟡 관찰/)).toBeNull();
});

test("a plain BET is unaffected when ev_watch is false or absent", async () => {
  await render(<PickCard pick={{ ...PICK, should_bet: true, ev_watch: false }} unitStake={20} onOpen={() => {}} onBet={() => {}} onDismiss={() => {}} />);
  expect(await screen.findByText(/🟣 BET/)).toBeOnTheScreen();
  expect(screen.getByText("베팅 $20")).toBeOnTheScreen();
  expect(screen.queryByText(/🟡 관찰/)).toBeNull();
});

// Gesture simulation isn't reliable in jest, so we press the revealed swipe
// action directly to lock the wiring. onDismiss takes the Pick (same as the
// inline button), so we assert it's called with the pick.
test("the swipe dismiss action calls onDismiss with the pick", async () => {
  const onDismiss = jest.fn();
  await render(<PickCard pick={PICK} onOpen={() => {}} onBet={() => {}} onDismiss={onDismiss} />);
  await fireEvent.press(screen.getByTestId("swipe-dismiss"));
  expect(onDismiss).toHaveBeenCalledWith(PICK);
});
