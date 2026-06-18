// app/src/screens/CaptureSheet.test.tsx
jest.mock("../api/client", () => ({
  api: {
    getPick: jest.fn(async () => ({
      id: "E1|h2h|NYM|0|", status: "active", event_id: "E1", sport: "baseball_mlb",
      home: "MIA", away: "NYM", commence_time: "2099-01-01T00:00:00Z", market: "h2h",
      side: "NYM", point: 0, player: "", label: "NYM ML", ev: 0.05, book: "FanDuel",
      odds: 2.1, conf: 55, n_books: 8,
    })),
    postBet: jest.fn(async () => ({ id: 9 })),
  },
}));
jest.mock("@react-navigation/native", () => ({ useNavigation: () => ({ popToTop: jest.fn(), goBack: jest.fn() }) }));

import { render, screen, waitFor, fireEvent } from "@testing-library/react-native";
import { CaptureSheet } from "./CaptureSheet";
import { api } from "../api/client";

const route = { params: { pickId: "E1|h2h|NYM|0|" } } as never;

test("prefills from the pick and posts a bet", async () => {
  await render(<CaptureSheet route={route} />);
  await waitFor(() => expect(screen.getByText("NYM ML")).toBeOnTheScreen());
  await fireEvent.changeText(screen.getByPlaceholderText("스테이크 ($)"), "5");
  await fireEvent.press(screen.getByText("기록"));
  await waitFor(() => expect(api.postBet).toHaveBeenCalled());
  const body = (api.postBet as jest.Mock).mock.calls[0][0];
  expect(body).toMatchObject({ event_id: "E1", stake: 5, fill_odds: 2.1, source: "one_tap" });
});

test("prefills american and saves an american-entered fill as decimal", async () => {
  (api.postBet as jest.Mock).mockClear();
  await render(<CaptureSheet route={route} />);
  await waitFor(() => expect(screen.getByText("NYM ML")).toBeOnTheScreen());
  const fillInput = screen.getByDisplayValue("+110");           // 2.1 prefilled as american
  await fireEvent.changeText(fillInput, "-110");
  await fireEvent.changeText(screen.getByPlaceholderText("스테이크 ($)"), "5");
  await fireEvent.press(screen.getByText("기록"));
  await waitFor(() => expect(api.postBet).toHaveBeenCalled());
  const body = (api.postBet as jest.Mock).mock.calls.at(-1)?.[0];
  expect(body.fill_odds).toBeCloseTo(1.9091, 3);                // -110 -> decimal
});

test("scan mode prefills from the draft and posts with source=scan", async () => {
  (api.getPick as jest.Mock).mockClear();
  (api.postBet as jest.Mock).mockClear();
  const scanDraft = {
    event_id: "EVT123", sport: "baseball_mlb", home: "COL", away: "CHC",
    commence_time: null, market: "batter_total_bases", side: "Over", point: 1.5,
    player: "Nico Hoerner", label: "Nico Hoerner TB Over 1.5", book: "FanDuel",
    rec_odds: null, fill_odds: 2.0, stake: 2.0, source: "scan",
  };
  const scanRoute = { params: { scanDraft } } as never;
  await render(<CaptureSheet route={scanRoute} />);
  await waitFor(() => expect(screen.getByText("Nico Hoerner TB Over 1.5")).toBeOnTheScreen());
  await fireEvent.changeText(screen.getByPlaceholderText("스테이크 ($)"), "5");
  await fireEvent.press(screen.getByText("기록"));
  await waitFor(() => expect(api.postBet).toHaveBeenCalled());
  const body = (api.postBet as jest.Mock).mock.calls.at(-1)?.[0];
  expect(body).toMatchObject({ event_id: "EVT123", market: "batter_total_bases", stake: 5, source: "scan" });
  expect(api.getPick).not.toHaveBeenCalled();
});
