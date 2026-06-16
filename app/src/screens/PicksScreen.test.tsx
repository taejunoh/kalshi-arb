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
    scanBet: jest.fn(async () => ({ bet: { event_id: "EVT123", market: "batter_total_bases", source: "scan" }, matched: true, notes: "" })),
  },
}));
jest.mock("../scan", () => ({ pickImage: jest.fn(async () => ({ base64: "QkFTRTY0", mediaType: "image/png" })) }));
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

test("the 🟣 BET filter hides non-bet picks (props + late-crossers)", async () => {
  (api.getPicks as jest.Mock).mockResolvedValueOnce({
    picks: [
      {
        id: "E1|spread|NYM|-1.5|", status: "active", event_id: "E1", sport: "baseball_mlb",
        home: "MIA", away: "NYM", commence_time: "2099-01-01T00:00:00Z", market: "spread",
        side: "NYM", point: -1.5, player: "", label: "BET PICK", ev: 0.05,
        book: "FanDuel", odds: 2.1, conf: 55, n_books: 8, first_entry: true, should_bet: true,
      },
      {
        id: "E2|h2h|MIA|0|", status: "active", event_id: "E2", sport: "baseball_mlb",
        home: "MIA", away: "NYM", commence_time: "2099-01-01T00:00:00Z", market: "h2h",
        side: "MIA", point: 0, player: "", label: "SKIP PICK", ev: 0.04,
        book: "BetMGM", odds: 2.0, conf: 50, n_books: 7, first_entry: false, should_bet: false,
      },
    ],
    counts: { active: 2, cooled: 0 },
    cooled: [],
  });
  await render(<PicksScreen />);
  await waitFor(() => expect(screen.getByText("BET PICK")).toBeOnTheScreen());
  expect(screen.getByText("SKIP PICK")).toBeOnTheScreen(); // both shown by default
  await fireEvent.press(screen.getByText(/BET만/));
  await waitFor(() => expect(screen.queryByText("SKIP PICK")).toBeNull()); // hidden
  expect(screen.getByText("BET PICK")).toBeOnTheScreen(); // bet kept
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

test("📷 슬립 캡처 scans and navigates to CaptureSheet with the draft", async () => {
  mockNavigate.mockClear();
  await render(<PicksScreen />);
  await waitFor(() => expect(screen.getByText("NYM ML")).toBeOnTheScreen());
  await fireEvent.press(screen.getByTestId("scan-capture-library"));
  await waitFor(() => expect(pickImage).toHaveBeenCalledWith("library"));
  await waitFor(() => expect(api.scanBet).toHaveBeenCalledWith("QkFTRTY0", "image/png"));
  await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith("CaptureSheet", { scanDraft: { event_id: "EVT123", market: "batter_total_bases", source: "scan" } }));
});
