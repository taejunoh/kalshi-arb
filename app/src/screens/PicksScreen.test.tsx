// app/src/screens/PicksScreen.test.tsx
jest.mock("../api/client", () => ({
  api: {
    getPicks: jest.fn(async () => ({
      picks: [
        {
          id: "E1|h2h|NYM|0|", status: "active", event_id: "E1", sport: "baseball_mlb",
          home: "MIA", away: "NYM", commence_time: "2099-01-01T00:00:00Z", market: "h2h",
          side: "NYM", point: 0, player: "", label: "NYM ML", ev: 0.05, book: "FanDuel",
          odds: 2.1, conf: 55, n_books: 8,
        },
      ],
      counts: { active: 1, cooled: 0 },
      cooled: [],
    })),
    dismissPick: jest.fn(async () => ({ ok: true })),
    putSettings: jest.fn(async () => ({ ok: true })),
    getSettings: jest.fn(async () => ({ min_ev: 0, sports: null, muted_books: null, sort: "ev" })),
  },
}));
jest.mock("@react-navigation/native", () => ({
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  useFocusEffect: (cb: () => void) => { require("react").useEffect(cb, []); },
  useNavigation: () => ({ navigate: jest.fn(), push: jest.fn() }),
}));

import { fireEvent, render, screen, waitFor } from "@testing-library/react-native";
import { api } from "../api/client";
import { PicksScreen } from "./PicksScreen";

test("renders the active feed from the API", async () => {
  await render(<PicksScreen />);
  await waitFor(() => expect(screen.getByText("NYM ML")).toBeOnTheScreen());
});

test("changing a filter preserves existing sport and book prefs (no clobber)", async () => {
  (api.getSettings as jest.Mock).mockResolvedValueOnce({
    min_ev: 0, sports: ["baseball_mlb"], muted_books: ["BallyBet"], sort: "ev",
  });
  await render(<PicksScreen />);
  await waitFor(() => expect(api.getSettings).toHaveBeenCalled());
  await fireEvent.press(screen.getByText("시간순"));
  await waitFor(() => expect(api.putSettings).toHaveBeenCalled());
  const sent = (api.putSettings as jest.Mock).mock.calls.at(-1)?.[0];
  expect(sent.sports).toEqual(["baseball_mlb"]);
  expect(sent.muted_books).toEqual(["BallyBet"]);
  expect(sent.sort).toBe("time");
});
