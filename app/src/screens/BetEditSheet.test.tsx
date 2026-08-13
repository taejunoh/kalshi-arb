jest.mock("../api/client", () => ({
  api: {
    patchBet: jest.fn(async () => ({ ok: true })),
    unmarkBet: jest.fn(async () => ({ ok: true, pnl_delta: null, settled_day: null })),
    remarkBet: jest.fn(async () => ({ ok: true })),
  },
}));
const mockPopToTop = jest.fn();
jest.mock("@react-navigation/native", () => ({
  useNavigation: () => ({ popToTop: mockPopToTop, goBack: jest.fn() }),
}));

import { fireEvent, render, screen, waitFor } from "@testing-library/react-native";
import { Alert } from "react-native";
import { api } from "../api/client";
import type { AppBet } from "../api/types";
import { BetEditSheet } from "./BetEditSheet";

// spyOn the public Alert, not react-native's internal module path — the internal
// path moves between Expo SDKs and a mock of it fails silently as a no-op.
const alertSpy = jest.spyOn(Alert, "alert").mockImplementation(() => {});

/** Press the destructive button in the last Alert the component raised. */
function confirmLastAlert() {
  const buttons = alertSpy.mock.calls.at(-1)?.[2] as
    { text?: string; onPress?: () => void }[] | undefined;
  buttons?.find((b) => b.text === "처리")?.onPress?.();
}

const bet: AppBet = {
  id: 7, event_id: "E1", sport: "baseball_mlb", home: "MIA", away: "NYM",
  commence_time: "2099-01-01T00:00:00Z", market: "h2h", side: "NYM", point: 0,
  player: "", label: "NYM ML", book: "FanDuel", fill_odds: 2.1, stake: 20,
  live_status: "pending", live_progress: null, settled: 0, won: null, pnl: null,
  settled_at: null, not_placed: 0, not_placed_at: null,
};

const routeFor = (b: AppBet) => ({ params: { bet: b } }) as never;

beforeEach(() => jest.clearAllMocks());

// fireEvent.changeText/.press are async in @testing-library/react-native v14
// (they wrap the handler in `act()`) — every call below is awaited so a state
// update commits before the next line reads it. Matches CaptureSheet.test.tsx.

test("shows the immutable context and the three editable fields", async () => {
  await render(<BetEditSheet route={routeFor(bet)} />);
  expect(screen.getByText("NYM ML")).toBeOnTheScreen();
  expect(screen.getByText("NYM @ MIA")).toBeOnTheScreen();
  expect(screen.getByText("경기·마켓은 수정할 수 없습니다")).toBeOnTheScreen();
  expect(screen.getByTestId("edit-book").props.value).toBe("FanDuel");
  expect(screen.getByTestId("edit-stake").props.value).toBe("20");
  expect(screen.getByTestId("edit-odds").props.value).toBe("+110");
});

test("saving sends only the three fields", async () => {
  // Every field is edited here so all three appear: an untouched odds field is
  // deliberately omitted (see the reprice tests below), so leaving it alone
  // would no longer exercise "the patch carries fill_odds and nothing extra".
  await render(<BetEditSheet route={routeFor(bet)} />);
  await fireEvent.changeText(screen.getByTestId("edit-book"), "DraftKings");
  await fireEvent.changeText(screen.getByTestId("edit-stake"), "25");
  await fireEvent.changeText(screen.getByTestId("edit-odds"), "+105");
  await fireEvent.press(screen.getByText("저장"));
  await waitFor(() => expect(api.patchBet).toHaveBeenCalledWith(7, {
    book: "DraftKings", stake: 25, fill_odds: 2.05,
  }));
  expect(mockPopToTop).toHaveBeenCalled();
});

test("an emptied stake is omitted, never sent as null", async () => {
  // The server 422s an explicit null stake — a placed bet without one is not a
  // record of anything, and the settle worker multiplies by it.
  await render(<BetEditSheet route={routeFor(bet)} />);
  await fireEvent.changeText(screen.getByTestId("edit-stake"), "");
  await fireEvent.press(screen.getByText("저장"));
  // fill_odds is absent because the odds field was never touched, not because
  // it was emptied — that is the reprice guard, covered on its own below.
  await waitFor(() => expect(api.patchBet).toHaveBeenCalledWith(7, {
    book: "FanDuel",
  }));
});

test("a non-numeric stake is omitted, never sent as NaN/null", async () => {
  // Number("abc") is NaN, and JSON.stringify serializes NaN as null — the one
  // payload the server 422s. A pasted or hardware-keyboard non-numeric value
  // must not defeat the omit-don't-null invariant the same way an emptied
  // field does.
  await render(<BetEditSheet route={routeFor(bet)} />);
  await fireEvent.changeText(screen.getByTestId("edit-stake"), "abc");
  await fireEvent.press(screen.getByText("저장"));
  await waitFor(() => expect(api.patchBet).toHaveBeenCalledWith(7, {
    book: "FanDuel",
  }));
});

test("an unparseable odds field is omitted, never sent as null", async () => {
  // Mirrors the stake omit invariant: a bad fill_odds must be dropped from
  // the patch, not sent through as null and rejected by the server 422.
  await render(<BetEditSheet route={routeFor(bet)} />);
  await fireEvent.changeText(screen.getByTestId("edit-odds"), "abc");
  await fireEvent.press(screen.getByText("저장"));
  await waitFor(() => expect(api.patchBet).toHaveBeenCalledWith(7, {
    book: "FanDuel", stake: 20,
  }));
});

test("an untouched odds field is omitted — saving never reprices fill_odds", async () => {
  // americanOdds/parseOddsToDecimal do NOT round-trip below decimal 2.0, i.e.
  // for every favorite: 1.91 -> "-110" -> 1.9090909090909092. Re-parsing a field
  // the user never touched would rewrite the ledger's recorded fill price to a
  // number the sportsbook never showed — on a save made to fix the book name.
  // app_bet rows are bet records: never repriced (LEDGER RULE).
  const fav: AppBet = { ...bet, fill_odds: 1.91 };
  await render(<BetEditSheet route={routeFor(fav)} />);
  expect(screen.getByTestId("edit-odds").props.value).toBe("-110");
  await fireEvent.changeText(screen.getByTestId("edit-book"), "DraftKings");
  await fireEvent.press(screen.getByText("저장"));
  await waitFor(() => expect(api.patchBet).toHaveBeenCalledWith(7, {
    book: "DraftKings", stake: 20,
  }));
});

test("an edited odds field still sends the new value", async () => {
  // The other half of the omit rule: the field is still editable, and a real
  // edit must reach the server. -105 -> 1 + 100/105.
  const fav: AppBet = { ...bet, fill_odds: 1.91 };
  await render(<BetEditSheet route={routeFor(fav)} />);
  await fireEvent.changeText(screen.getByTestId("edit-odds"), "-105");
  await fireEvent.press(screen.getByText("저장"));
  await waitFor(() => expect(api.patchBet).toHaveBeenCalledWith(7, {
    book: "FanDuel", stake: 20, fill_odds: 1 + 100 / 105,
  }));
});

test("retyping the same odds text is still omitted", async () => {
  // The rule is "the TEXT differs from what was rendered", so a user who edits
  // the field and puts it back must not trigger a reprice either.
  const fav: AppBet = { ...bet, fill_odds: 1.91 };
  await render(<BetEditSheet route={routeFor(fav)} />);
  await fireEvent.changeText(screen.getByTestId("edit-odds"), "-120");
  await fireEvent.changeText(screen.getByTestId("edit-odds"), "-110");
  await fireEvent.press(screen.getByText("저장"));
  await waitFor(() => expect(api.patchBet).toHaveBeenCalledWith(7, {
    book: "FanDuel", stake: 20,
  }));
});

test("a whitespace-only book is sent as null, not a space", async () => {
  // book.trim() || null: " " is truthy but not a real value.
  await render(<BetEditSheet route={routeFor(bet)} />);
  await fireEvent.changeText(screen.getByTestId("edit-book"), "  ");
  await fireEvent.press(screen.getByText("저장"));
  await waitFor(() => expect(api.patchBet).toHaveBeenCalledWith(7, {
    book: null, stake: 20,
  }));
});

test("표시 해제 goes through a confirm dialog before calling the API", async () => {
  await render(<BetEditSheet route={routeFor(bet)} />);
  await fireEvent.press(screen.getByText("베팅 안 했음"));
  expect(api.unmarkBet).not.toHaveBeenCalled();      // dialog first, no API yet
  const [title, message] = alertSpy.mock.calls[0] as unknown as [string, string];
  expect(title).toBe("베팅 안 했음으로 처리");
  expect(message).toBe("기록은 남고 집계에서만 빠집니다. 이 픽은 다시 피드에 나타납니다.");
  confirmLastAlert();
  await waitFor(() => expect(api.unmarkBet).toHaveBeenCalledWith(7));
  expect(mockPopToTop).toHaveBeenCalled();
});

test("a settled bet's dialog names the PnL change", async () => {
  const settled: AppBet = { ...bet, settled: 1, won: 1, pnl: -45,
    settled_at: "2026-08-09T22:00:00+00:00" };
  await render(<BetEditSheet route={routeFor(settled)} />);
  await fireEvent.press(screen.getByText("베팅 안 했음"));
  const [, message] = alertSpy.mock.calls[0] as unknown as [string, string];
  expect(message).toBe(
    "8월 9일 PnL이 +45.00 변경됩니다. 기록은 남고 집계에서만 빠집니다. 이 픽은 다시 피드에 나타납니다.");
});

test("a settled bet cannot be edited, only unmarked", async () => {
  const settled: AppBet = { ...bet, settled: 1, won: 1, pnl: 26,
    settled_at: "2026-08-09T22:00:00+00:00" };
  await render(<BetEditSheet route={routeFor(settled)} />);
  expect(screen.getByText("정산된 베팅은 수정할 수 없습니다")).toBeOnTheScreen();
  expect(screen.queryByText("저장")).toBeNull();
  expect(screen.getByText("베팅 안 했음")).toBeOnTheScreen();
});

test("a not-placed bet offers 되돌리기 instead", async () => {
  const off: AppBet = { ...bet, not_placed: 1, not_placed_at: "2026-08-12T10:00:00+00:00" };
  await render(<BetEditSheet route={routeFor(off)} />);
  await fireEvent.press(screen.getByText("되돌리기"));
  await waitFor(() => expect(api.remarkBet).toHaveBeenCalledWith(7));
  expect(mockPopToTop).toHaveBeenCalled();
});
