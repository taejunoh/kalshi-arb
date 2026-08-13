jest.mock("../api/client", () => ({
  api: {
    getMyBets: jest.fn(async () => ({
      calendar: { "2026-08-09": 12.5 },
      open: [{
        id: 1, event_id: "E1", sport: "baseball_mlb", home: "MIA", away: "NYM",
        commence_time: "2099-01-01T00:00:00Z", market: "h2h", side: "NYM",
        point: 0, player: "", label: "NYM ML", book: "FanDuel", fill_odds: 2.1,
        stake: 20, live_status: "pending", live_progress: null, settled: 0,
        won: null, pnl: null, settled_at: null, not_placed: 0, not_placed_at: null,
      }],
      settled: [{
        id: 2, event_id: "E2", sport: "baseball_mlb", home: "LAD", away: "SF",
        commence_time: "2026-08-09T20:00:00Z", market: "h2h", side: "SF",
        point: 0, player: "", label: "SF ML", book: "DraftKings", fill_odds: 2.3,
        stake: 20, live_status: null, live_progress: null, settled: 1,
        won: 1, pnl: 26, settled_at: "2026-08-09T23:00:00+00:00",
        not_placed: 0, not_placed_at: null,
      }],
      not_placed: [{
        id: 3, event_id: "E3", sport: "baseball_mlb", home: "BOS", away: "NYY",
        commence_time: "2026-08-08T20:00:00Z", market: "h2h", side: "NYY",
        point: 0, player: "", label: "NYY ML", book: "BetMGM", fill_odds: 1.9,
        stake: 20, live_status: null, live_progress: null, settled: 0,
        won: null, pnl: null, settled_at: null, not_placed: 1,
        not_placed_at: "2026-08-12T10:00:00+00:00",
      }],
    })),
  },
}));
const mockPush = jest.fn();
jest.mock("@react-navigation/native", () => ({
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  useFocusEffect: (cb: () => void) => { require("react").useEffect(cb, []); },
  useNavigation: () => ({ push: mockPush }),
}));

import { fireEvent, render, screen, waitFor } from "@testing-library/react-native";
import { MyBetsScreen, monthPnl, monthPrefix } from "./MyBetsScreen";

// The user is in New York and the calendar's day keys are UTC, so the two frames
// disagree for the last evening of every month. Pin the runner's zone to make
// that boundary bite deterministically on any machine, and restore it after —
// jest reuses one worker process across test files.
const realTZ = process.env.TZ;
process.env.TZ = "America/New_York";
afterAll(() => { process.env.TZ = realTZ; });

test("monthPnl sums only the requested month's days", () => {
  // The screen's headline number. Fixed prefix, never new Date(): a test for it
  // must not start failing on some future day.
  const calendar = { "2026-07-31": 100, "2026-08-01": 12.5,
                     "2026-08-09": -4.25, "2026-09-01": 999 };
  expect(monthPnl(calendar, "2026-08")).toBe(8.25);
  expect(monthPnl(calendar, "2026-09")).toBe(999);
  expect(monthPnl({}, "2026-08")).toBe(0);
  expect(monthPnl(calendar, "2026-10")).toBe(0);
});

test("이번 달 is derived in UTC — the frame the calendar keys are in", () => {
  // Day keys are substr(settled_at,1,10) of a UTC timestamp. 2026-08-31 21:00 ET
  // is 2026-09-01 01:00 UTC, so that evening's settlements are keyed
  // "2026-09-01"; reading the DEVICE's local month here would look for "2026-08"
  // and leave them out of both months' summaries.
  expect(monthPrefix(new Date("2026-09-01T01:00:00Z"))).toBe("2026-09");
  expect(monthPrefix(new Date("2026-08-01T03:00:00Z"))).toBe("2026-08");
});

test("renders the three sections with their exact headers", async () => {
  await render(<MyBetsScreen />);
  await waitFor(() => expect(screen.getByText("NYM ML")).toBeOnTheScreen());
  // Exact strings, not regex: a broken JSX ternary once rendered its own source
  // text and a /.../ assertion happily matched it (PR#75).
  expect(screen.getByText("미정산 1")).toBeOnTheScreen();
  expect(screen.getByText("정산됨 1")).toBeOnTheScreen();
  expect(screen.getByText("베팅 안 함 1")).toBeOnTheScreen();
  expect(screen.getByText("SF ML")).toBeOnTheScreen();
  expect(screen.getByText("NYY ML")).toBeOnTheScreen();
});

test("a not-placed row is struck through", async () => {
  await render(<MyBetsScreen />);
  await waitFor(() => expect(screen.getByText("NYY ML")).toBeOnTheScreen());
  expect(screen.getByText("NYY ML")).toHaveStyle({ textDecorationLine: "line-through" });
});

test("settled rows show signed PnL", async () => {
  await render(<MyBetsScreen />);
  await waitFor(() => expect(screen.getByText("+26.00")).toBeOnTheScreen());
});

test("an empty section is not rendered at all", async () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { api } = require("../api/client");
  (api.getMyBets as jest.Mock).mockResolvedValueOnce({
    calendar: {}, open: [], settled: [], not_placed: [],
  });
  await render(<MyBetsScreen />);
  await waitFor(() => expect(screen.getByText("기록된 베팅 없음")).toBeOnTheScreen());
  expect(screen.queryByText("미정산 0")).toBeNull();
});

test("tapping a row opens the edit sheet with that bet", async () => {
  await render(<MyBetsScreen />);
  await waitFor(() => expect(screen.getByText("NYM ML")).toBeOnTheScreen());
  fireEvent.press(screen.getByText("NYM ML"));
  expect(mockPush).toHaveBeenCalledWith("BetEdit", { bet: expect.objectContaining({ id: 1 }) });
});
