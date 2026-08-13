jest.mock("../api/client", () => ({
  api: {
    getMyBets: jest.fn(async () => ({
      calendar: { "2026-08-09": 12.5 },
      month: "2026-08",
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
import type { MyBetsResponse } from "../api/types";
import { MyBetsScreen, localMonth, monthPnl, summaryMonth } from "./MyBetsScreen";

// The fallback below is a LOCAL-zone derivation, so one assertion in
// `localMonth` can only tell "local" from "UTC" on a host that is not UTC. That
// is not left to luck: app/jest.globalSetup.js pins TZ=America/New_York in the
// jest parent before any worker boots, so the discriminating assertion is
// deterministic on every machine — including a UTC CI/EC2 host, and including
// --runInBand. (Setting process.env.TZ inside a test module would NOT work:
// Node/V8 caches the zone at process boot.) Each zone-sensitive assertion below
// is paired with one that holds in every zone, so a broken pin degrades the
// suite loudly rather than silently.

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

test("이번 달 is the month the server sent, in the frame the keys are in", () => {
  // The calendar's keys are Eastern days now, and the server sends the Eastern
  // month alongside them. Nothing about the month is re-derived on the device:
  // whatever the server says is what gets summed, even a month the device's own
  // clock disagrees with.
  const at = (month?: string) => ({ month, calendar: {} } as unknown as MyBetsResponse);
  expect(summaryMonth(at("2026-07"))).toBe("2026-07");
  expect(summaryMonth(at("2099-12"))).toBe("2099-12");
});

test("a response predating `month` falls back to the DEVICE's local month", () => {
  // Deploy skew only (JS shipped by OTA ahead of the API, or an API rollback).
  // Local, not UTC: UTC was correct only while the day keys were UTC, and they
  // no longer are. For this user — in New York — local IS the server's frame.
  const noMonth = { calendar: {} } as unknown as MyBetsResponse;
  expect(summaryMonth(noMonth, new Date("2026-08-15T12:00:00Z"))).toBe("2026-08");

  // Midday mid-month: every zone on earth agrees, so this pins the format and
  // the off-by-one on any host, pin or no pin.
  expect(localMonth(new Date("2026-08-15T12:00:00Z"))).toBe("2026-08");
  expect(localMonth(new Date("2026-01-15T12:00:00Z"))).toBe("2026-01");

  // 2026-08-31 21:00 ET. This is the pair that discriminates: a UTC derivation
  // answers "2026-09" and drops that evening out of the August total the user
  // is looking at. Deterministic here because globalSetup pins the zone.
  expect(localMonth(new Date("2026-09-01T01:00:00Z"))).toBe("2026-08");
  expect(localMonth(new Date("2026-01-01T02:00:00Z"))).toBe("2025-12");
});

test("the summary sums the server's month, not the device's", async () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { api } = require("../api/client");
  // A calendar spanning two Eastern months, and a server month that is neither
  // the current one nor the one a UTC/device derivation would pick. Only code
  // that actually reads response.month can produce +7.25.
  (api.getMyBets as jest.Mock).mockResolvedValueOnce({
    calendar: { "2026-06-30": 500, "2026-07-04": 7.25, "2026-08-01": 900 },
    month: "2026-07", open: [], settled: [], not_placed: [],
  });
  await render(<MyBetsScreen />);
  await waitFor(() => expect(screen.getByText("+7.25")).toBeOnTheScreen());
});

test("the summary still shows a number when `month` is missing", async () => {
  // The alternative — blanking the headline — would turn a minutes-long deploy
  // skew into a screen that looks broken. Keyed off localMonth so the assertion
  // does not depend on what day the suite happens to run.
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { api } = require("../api/client");
  (api.getMyBets as jest.Mock).mockResolvedValueOnce({
    calendar: { [`${localMonth()}-05`]: 3.5 }, open: [], settled: [], not_placed: [],
  });
  await render(<MyBetsScreen />);
  await waitFor(() => expect(screen.getByText("+3.50")).toBeOnTheScreen());
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
