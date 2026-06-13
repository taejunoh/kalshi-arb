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

import { render, screen, waitFor } from "@testing-library/react-native";
import { PicksScreen } from "./PicksScreen";

test("renders the active feed from the API", async () => {
  await render(<PicksScreen />);
  await waitFor(() => expect(screen.getByText("NYM ML")).toBeOnTheScreen());
});
