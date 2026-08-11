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
    undismissPick: jest.fn(async () => ({ ok: true })),
    putSettings: jest.fn(async () => ({ ok: true })),
    getSettings: jest.fn(async () => ({ min_ev: 0, sports: null, muted_books: null, sort: "ev" })),
    scanBet: jest.fn(async () => ({ bet: { event_id: "EVT123", market: "batter_total_bases", source: "scan" }, matched: true, notes: "" })),
  },
}));
jest.mock("../scan", () => ({ pickImage: jest.fn(async () => ({ base64: "QkFTRTY0", mediaType: "image/png" })) }));
jest.mock("../storage", () => ({ getUnitStake: jest.fn(async () => 20) }));
const mockNavigate = jest.fn();
jest.mock("@react-navigation/native", () => ({
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  useFocusEffect: (cb: () => void) => { require("react").useEffect(cb, []); },
  useNavigation: () => ({ navigate: mockNavigate, push: jest.fn() }),
}));

import { fireEvent, render, screen, waitFor } from "@testing-library/react-native";
import { api } from "../api/client";
import { pickImage } from "../scan";
import { PicksScreen } from "./PicksScreen";

test("renders the active feed from the API", async () => {
  await render(<PicksScreen />);
  await waitFor(() => expect(screen.getByText("NYM ML")).toBeOnTheScreen());
});

test("the feed shows what the API sends — SKIP filtering now lives server-side", async () => {
  // The screen used to carry a "BET만" chip. SKIP picks (props, late-crossers)
  // no longer leave the API at all (user, 2026-08-05), so the chip filtered a
  // list that could not contain them; the screen renders the feed as given.
  (api.getPicks as jest.Mock).mockResolvedValueOnce({
    picks: [
      {
        id: "E1|spread|NYM|-1.5|", status: "active", event_id: "E1", sport: "baseball_mlb",
        home: "MIA", away: "NYM", commence_time: "2099-01-01T00:00:00Z", market: "spread",
        side: "NYM", point: -1.5, player: "", label: "BET PICK", ev: 0.05,
        book: "FanDuel", odds: 2.1, conf: 55, n_books: 8, first_entry: true, should_bet: true,
      },
    ],
    counts: { active: 1, cooled: 0 },
    cooled: [],
  });
  await render(<PicksScreen />);
  await waitFor(() => expect(screen.getByText("BET PICK")).toBeOnTheScreen());
  expect(screen.queryByText(/BET만/)).toBeNull();   // the chip is gone
});

test("dismissing a cooled pick removes it immediately", async () => {
  // The reported bug: optimistic removal only filtered the active list, so a
  // cooled-pick dismiss sat on screen until the next refetch — reading as the
  // button not working.
  (api.getPicks as jest.Mock).mockResolvedValueOnce({
    picks: [], counts: {},
    cooled: [{
      id: "C1|spread|PHX|2.5|", status: "cooled", event_id: "C1", sport: "basketball_wnba",
      home: "Los Angeles Sparks", away: "Phoenix Mercury", commence_time: "2099-01-01T00:00:00Z",
      market: "spread", side: "Phoenix Mercury", point: 2.5, player: "",
      label: "COOLED PICK", ev: 0.02, book: "FanDuel", odds: 1.86, conf: 40,
      n_books: 3, first_entry: true, should_bet: true,
    }],
  });
  (api.dismissPick as jest.Mock).mockResolvedValue({ ok: true });
  await render(<PicksScreen />);
  await fireEvent.press(screen.getByText(/식은 픽/));            // expand the section
  await waitFor(() => expect(screen.getByText("COOLED PICK")).toBeOnTheScreen());
  await fireEvent.press(screen.getAllByText("관심없음")[0]); // button + swipe action both render the label
  await waitFor(() => expect(screen.queryByText("COOLED PICK")).toBeNull());
});

test("dismiss shows a 5-second undo which restores via the API", async () => {
  // Once x2 (mount + the post-undo reload), NOT a permanent mockResolvedValue —
  // that leaks this payload into every later test in the file.
  const payload = {
    picks: [{
      id: "E1|h2h|SEA|0|", status: "active", event_id: "E1", sport: "baseball_mlb",
      home: "DET", away: "SEA", commence_time: "2099-01-01T00:00:00Z",
      market: "h2h", side: "SEA", point: 0, player: "", label: "UNDO PICK",
      ev: 0.02, book: "Caesars", odds: 1.87, conf: 40, n_books: 8,
      first_entry: true, should_bet: true,
    }],
    counts: {}, cooled: [],
  };
  (api.getPicks as jest.Mock).mockResolvedValueOnce(payload).mockResolvedValueOnce(payload);
  (api.dismissPick as jest.Mock).mockResolvedValue({ ok: true });
  (api.undismissPick as jest.Mock).mockResolvedValue({ ok: true });
  await render(<PicksScreen />);
  await waitFor(() => expect(screen.getByText("UNDO PICK")).toBeOnTheScreen());
  await fireEvent.press(screen.getAllByText("관심없음")[0]); // button + swipe action both render the label
  await waitFor(() => expect(screen.getByText("실행취소")).toBeOnTheScreen());
  await fireEvent.press(screen.getByText("실행취소"));
  expect(api.undismissPick).toHaveBeenCalledWith("E1|h2h|SEA|0|");
  await waitFor(() => expect(screen.getByText("UNDO PICK")).toBeOnTheScreen()); // reload restored it
});

test("an empty feed says where the picks went, not just that there are none", async () => {
  // Twice on 2026-08-05 a Telegram alert arrived and the pick was not in the app.
  // Both times it was one the user had already marked bet — the feed said only
  // "활성 픽 없음", which reads as "nothing found" rather than "already handled".
  (api.getPicks as jest.Mock).mockResolvedValueOnce({
    picks: [], counts: { bet: 26, dismissed: 3, expired: 25 }, cooled: [],
  });
  await render(<PicksScreen />);
  await waitFor(() => expect(screen.getByText("활성 픽 없음")).toBeOnTheScreen());
  expect(screen.getByText("표시함 26 · 무시함 3")).toBeOnTheScreen();
});

test("expired is left to its own section rather than named twice", async () => {
  (api.getPicks as jest.Mock).mockResolvedValueOnce({
    picks: [], counts: { bet: 2, expired: 25 }, cooled: [],
  });
  await render(<PicksScreen />);
  await waitFor(() => expect(screen.getByText("표시함 2")).toBeOnTheScreen());
});

test("a status with no picks is left out of the explanation", async () => {
  (api.getPicks as jest.Mock).mockResolvedValueOnce({
    picks: [], counts: { bet: 4 }, cooled: [],
  });
  await render(<PicksScreen />);
  await waitFor(() => expect(screen.getByText("표시함 4")).toBeOnTheScreen());
});

test("a genuinely empty day says nothing extra", async () => {
  (api.getPicks as jest.Mock).mockResolvedValueOnce({ picks: [], counts: {}, cooled: [] });
  await render(<PicksScreen />);
  await waitFor(() => expect(screen.getByText("활성 픽 없음")).toBeOnTheScreen());
  expect(screen.queryByText(/표시함|무시함/)).toBeNull();
});

test("지나간 픽 (expired) section reveals an alerted pick the user missed after game start", async () => {
  (api.getPicks as jest.Mock).mockResolvedValueOnce({
    picks: [],
    counts: { expired: 1 },
    cooled: [],
    expired: [
      {
        id: "E9|h2h|Lotte Giants|0|", status: "expired", event_id: "E9", sport: "baseball_kbo",
        home: "Kiwoom Heroes", away: "Lotte Giants", commence_time: "2020-01-01T00:00:00Z",
        market: "h2h", side: "Lotte Giants", point: 0, player: "", label: "MISSED PICK",
        ev: 0.071, book: "BetMGM", odds: 2.35, conf: 60, n_books: 8, first_entry: true, should_bet: true,
      },
    ],
  });
  await render(<PicksScreen />);
  await waitFor(() => expect(screen.getByText(/지나간 픽/)).toBeOnTheScreen());
  expect(screen.queryByText("MISSED PICK")).toBeNull(); // collapsed by default
  await fireEvent.press(screen.getByText(/지나간 픽/));
  await waitFor(() => expect(screen.getByText("MISSED PICK")).toBeOnTheScreen());
});

test("changing a filter preserves existing sport and book prefs (no clobber)", async () => {
  (api.getSettings as jest.Mock).mockResolvedValueOnce({
    min_ev: 0, sports: ["baseball_mlb"], muted_books: ["BallyBet"], sort: "ev",
  });
  await render(<PicksScreen />);
  await waitFor(() => expect(api.getSettings).toHaveBeenCalled());
  // Sort is a dropdown: tap to open, then pick an option (EV순 / 시간순 / 북수순).
  await fireEvent.press(screen.getByTestId("sort-dropdown"));
  await fireEvent.press(screen.getByTestId("sort-opt-time"));
  await waitFor(() => expect(api.putSettings).toHaveBeenCalled());
  const sent = (api.putSettings as jest.Mock).mock.calls.at(-1)?.[0];
  expect(sent.sports).toEqual(["baseball_mlb"]);
  expect(sent.muted_books).toEqual(["BallyBet"]);
  expect(sent.sort).toBe("time");
});

test("📷 슬립 캡처 scans and navigates to CaptureSheet with the draft", async () => {
  mockNavigate.mockClear();
  await render(<PicksScreen />);
  await waitFor(() => expect(screen.getByText("NYM ML")).toBeOnTheScreen());
  await fireEvent.press(screen.getByTestId("scan-capture-library"));
  await waitFor(() => expect(pickImage).toHaveBeenCalledWith("library"));
  await waitFor(() => expect(api.scanBet).toHaveBeenCalledWith("QkFTRTY0", "image/png"));
  await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith("CaptureSheet", { scanDraft: { event_id: "EVT123", market: "batter_total_bases", source: "scan" } }));
});
