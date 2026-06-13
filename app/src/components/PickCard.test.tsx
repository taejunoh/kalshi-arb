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
  await fireEvent.press(screen.getByText("관심없음"));
  expect(onBet).toHaveBeenCalledWith(PICK);
  expect(onDismiss).toHaveBeenCalledWith(PICK);
});

test("shows the 🟣 first-entry badge when first_entry is true", async () => {
  await render(<PickCard pick={{ ...PICK, first_entry: true }} onOpen={() => {}} onBet={() => {}} onDismiss={() => {}} />);
  expect(await screen.findByText(/first-entry/)).toBeOnTheScreen();
});

test("shows the ⚪ late-crosser badge when first_entry is false", async () => {
  await render(<PickCard pick={{ ...PICK, first_entry: false }} onOpen={() => {}} onBet={() => {}} onDismiss={() => {}} />);
  expect(await screen.findByText(/late-crosser/)).toBeOnTheScreen();
});

test("shows no first-entry badge when the flag is absent", async () => {
  await render(<PickCard pick={PICK} onOpen={() => {}} onBet={() => {}} onDismiss={() => {}} />);
  expect(screen.queryByText(/first-entry|late-crosser/)).toBeNull();
});
