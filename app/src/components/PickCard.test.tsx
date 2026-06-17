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

// Gesture simulation isn't reliable in jest, so we press the revealed swipe
// action directly to lock the wiring. onDismiss takes the Pick (same as the
// inline button), so we assert it's called with the pick.
test("the swipe dismiss action calls onDismiss with the pick", async () => {
  const onDismiss = jest.fn();
  await render(<PickCard pick={PICK} onOpen={() => {}} onBet={() => {}} onDismiss={onDismiss} />);
  await fireEvent.press(screen.getByTestId("swipe-dismiss"));
  expect(onDismiss).toHaveBeenCalledWith(PICK);
});
