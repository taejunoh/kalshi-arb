// app/src/screens/MyBetsScreen.test.tsx
jest.mock("../api/client", () => ({
  api: {
    getMyBets: jest.fn(async () => ({
      calendar: { "2026-06-12": 2.4 },
      open: [
        {
          id: 1, event_id: "E1", sport: "baseball_mlb", home: "MIA", away: "NYM",
          commence_time: "2099-01-01T00:00:00Z", market: "batter_total_bases", side: "Over",
          point: 1.5, player: "Brandon Nimmo", label: "Nimmo TB Over 1.5", book: "FanDuel",
          fill_odds: 2.2, stake: 5, live_status: "winning",
          live_progress: { current: 2, needed: 1.5, remaining: 0, game_state: "Mid 2nd" },
        },
      ],
    })),
  },
}));
jest.mock("@react-navigation/native", () => ({
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  useFocusEffect: (cb: () => void) => { require("react").useEffect(cb, []); },
}));

import { render, screen, waitFor } from "@testing-library/react-native";
import { MyBetsScreen } from "./MyBetsScreen";

test("renders calendar total and a live open bet with progress", async () => {
  await render(<MyBetsScreen />);
  await waitFor(() => expect(screen.getByText("Nimmo TB Over 1.5")).toBeOnTheScreen());
  expect(screen.getByText(/2 \/ 1.5/)).toBeOnTheScreen();
  expect(screen.getAllByText(/\+2\.40/).length).toBeGreaterThanOrEqual(1); // calendar total
});
