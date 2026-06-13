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
