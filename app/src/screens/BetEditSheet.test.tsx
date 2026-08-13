jest.mock("../api/client", () => ({
  api: {
    patchBet: jest.fn(async () => ({ ok: true })),
    unmarkBet: jest.fn(async () => ({ ok: true, pnl_delta: null, settled_day: null })),
    remarkBet: jest.fn(async () => ({ ok: true, pnl_delta: null, settled_day: null })),
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

/** Press the named confirm button in the last Alert the component raised. */
function confirmLastAlert(text = "처리") {
  const buttons = alertSpy.mock.calls.at(-1)?.[2] as
    { text?: string; onPress?: () => void }[] | undefined;
  buttons?.find((b) => b.text === text)?.onPress?.();
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
    settled_at: "2026-08-09T22:00:00+00:00", settled_day: "2026-08-09" };
  await render(<BetEditSheet route={routeFor(settled)} />);
  await fireEvent.press(screen.getByText("베팅 안 했음"));
  const [, message] = alertSpy.mock.calls[0] as unknown as [string, string];
  expect(message).toBe(
    "8월 9일 PnL이 +45.00 변경됩니다. 기록은 남고 집계에서만 빠집니다. 이 픽은 다시 피드에 나타납니다.");
});

// The settled_day cases that existed before all used 22:00Z, which is the same
// calendar day in UTC and Eastern — precisely why none of them caught the
// confirm dialog naming a UTC day. Everything below settles at 02:00Z, where
// the frames disagree: the old `bet.settled_at.slice(0,10)` answers "8월 13일"
// for a bet the calendar files under 8월 12일.
//
// These are pure string assertions (settled_day arrives as YYYY-MM-DD and
// MONTH_DAY only slices it) — no Date, no zone, so they read identically on a
// UTC host and an ET one.

test("the unmark dialog names the EASTERN day, not the UTC one", async () => {
  const lateNight: AppBet = { ...bet, settled: 1, won: 1, pnl: -45,
    settled_at: "2026-08-13T02:00:00+00:00", settled_day: "2026-08-12" };
  await render(<BetEditSheet route={routeFor(lateNight)} />);
  await fireEvent.press(screen.getByText("베팅 안 했음"));
  const [, message] = alertSpy.mock.calls[0] as unknown as [string, string];
  expect(message).toBe(
    "8월 12일 PnL이 +45.00 변경됩니다. 기록은 남고 집계에서만 빠집니다. 이 픽은 다시 피드에 나타납니다.");
  expect(message).not.toContain("8월 13일");
});

test("the 되돌리기 dialog names the EASTERN day too", async () => {
  const lateNightOff: AppBet = { ...bet, not_placed: 1,
    not_placed_at: "2026-08-14T10:00:00+00:00", settled: 1, won: 1, pnl: -45,
    settled_at: "2026-08-13T02:00:00+00:00", settled_day: "2026-08-12" };
  await render(<BetEditSheet route={routeFor(lateNightOff)} />);
  await fireEvent.press(screen.getByText("되돌리기"));
  const [, message] = alertSpy.mock.calls[0] as unknown as [string, string];
  expect(message).toBe(
    "8월 12일 PnL이 -45.00 변경됩니다. 이 베팅이 다시 집계에 포함됩니다. 이 픽은 피드에서 다시 숨겨집니다.");
  expect(message).not.toContain("8월 13일");
});

test("a row predating settled_day still confirms and names the amount, just no day", async () => {
  // Deploy skew (an OTA ahead of the API, or an API rollback): the row has no
  // settled_day yet. Whether to PROMPT (the money question: settled && pnl !=
  // null) and whether to NAME A DAY (settled_day present) are separate — this
  // row still moves a real PnL, so it still gets a confirm with the amount in
  // it; it just cannot say which day, because the device cannot convert the
  // instant itself and naming the UTC day would be the very bug this whole
  // change removes.
  const noDay: AppBet = { ...bet, settled: 1, won: 1, pnl: -45,
    settled_at: "2026-08-13T02:00:00+00:00" };
  await render(<BetEditSheet route={routeFor(noDay)} />);
  await fireEvent.press(screen.getByText("베팅 안 했음"));
  const [, message] = alertSpy.mock.calls[0] as unknown as [string, string];
  expect(message).toBe(
    "PnL이 +45.00 변경됩니다. 기록은 남고 집계에서만 빠집니다. 이 픽은 다시 피드에 나타납니다.");
  expect(message).not.toContain("월");
});

test("a settled bet cannot be edited, only unmarked", async () => {
  const settled: AppBet = { ...bet, settled: 1, won: 1, pnl: 26,
    settled_at: "2026-08-09T22:00:00+00:00", settled_day: "2026-08-09" };
  await render(<BetEditSheet route={routeFor(settled)} />);
  expect(screen.getByText("정산된 베팅은 수정할 수 없습니다")).toBeOnTheScreen();
  expect(screen.queryByText("저장")).toBeNull();
  expect(screen.getByText("베팅 안 했음")).toBeOnTheScreen();
});

const offBet: AppBet = { ...bet, not_placed: 1, not_placed_at: "2026-08-12T10:00:00+00:00" };

test("an unsettled 되돌리기 is a single tap — no confirm dialog", async () => {
  // The rule being protected is "a past number must never shift in silence",
  // and an unsettled bet has no past number. Restoring is also the UNDO of the
  // destructive direction, so a prompt here is friction with nothing behind it
  // — the same call 관심없음 makes with its 5-second undo instead of a confirm.
  await render(<BetEditSheet route={routeFor(offBet)} />);
  await fireEvent.press(screen.getByText("되돌리기"));
  expect(alertSpy).not.toHaveBeenCalled();
  await waitFor(() => expect(api.remarkBet).toHaveBeenCalledWith(7));
  expect(mockPopToTop).toHaveBeenCalled();
});

const settledOffBet: AppBet = { ...offBet, settled: 1, won: 1, pnl: -45,
  settled_at: "2026-08-09T22:00:00+00:00", settled_day: "2026-08-09" };

test("a settled 되돌리기 confirms first and names the restored PnL", async () => {
  // Same day and amount as the unmark dialog above, opposite sign: unmarking
  // this bet moved 8월 9일 by +45.00, so restoring it moves that day by -45.00.
  await render(<BetEditSheet route={routeFor(settledOffBet)} />);
  await fireEvent.press(screen.getByText("되돌리기"));
  expect(api.remarkBet).not.toHaveBeenCalled();      // dialog first, no API yet
  const [title, message] = alertSpy.mock.calls[0] as unknown as [string, string];
  expect(title).toBe("베팅 기록 되돌리기");
  expect(message).toBe(
    "8월 9일 PnL이 -45.00 변경됩니다. 이 베팅이 다시 집계에 포함됩니다. 이 픽은 피드에서 다시 숨겨집니다.");
  confirmLastAlert("되돌리기");
  await waitFor(() => expect(api.remarkBet).toHaveBeenCalledWith(7));
  expect(mockPopToTop).toHaveBeenCalled();
});

const settledOffBetNoDay: AppBet = { ...offBet, settled: 1, won: 1, pnl: -45,
  settled_at: "2026-08-13T02:00:00+00:00" };            // no settled_day

test("a settled 되돌리기 still confirms even with no settled_day to name", async () => {
  // Finding: movesSettledPnl used to be gated on settled_day, and remark()
  // reuses that predicate to decide whether to prompt AT ALL — so a row
  // predating settled_day (deploy skew) took the "nothing moved" branch and
  // called remarkBet immediately, moving a past PnL with zero disclosure.
  // Measured: settled:1, pnl:-45, settled_at set, no settled_day ->
  // alerts=0, remarkCalled=1. The money question (settled && pnl != null)
  // must gate the prompt on its own, independent of whether a day can be
  // named.
  await render(<BetEditSheet route={routeFor(settledOffBetNoDay)} />);
  await fireEvent.press(screen.getByText("되돌리기"));
  expect(api.remarkBet).not.toHaveBeenCalled();      // dialog first, no API yet
  const [title, message] = alertSpy.mock.calls[0] as unknown as [string, string];
  expect(title).toBe("베팅 기록 되돌리기");
  expect(message).toBe(
    "PnL이 -45.00 변경됩니다. 이 베팅이 다시 집계에 포함됩니다. 이 픽은 피드에서 다시 숨겨집니다.");
  expect(message).not.toContain("월");
  confirmLastAlert("되돌리기");
  await waitFor(() => expect(api.remarkBet).toHaveBeenCalledWith(7));
  expect(mockPopToTop).toHaveBeenCalled();
});

test("a settled bet with no pnl recorded does not raise a numberless dialog", async () => {
  // The gate is the same predicate the message text reads, so the two can never
  // disagree: if the dialog would have no amount to name, there is nothing to
  // disclose and no reason to prompt.
  const noPnl: AppBet = { ...settledOffBet, pnl: null, settled_at: null,
    settled_day: null };
  await render(<BetEditSheet route={routeFor(noPnl)} />);
  await fireEvent.press(screen.getByText("되돌리기"));
  expect(alertSpy).not.toHaveBeenCalled();
  await waitFor(() => expect(api.remarkBet).toHaveBeenCalledWith(7));
});

test("베팅 안 했음 still confirms even when unsettled — it is the destructive direction", async () => {
  // The asymmetry is deliberate: unmark drops the bet from PnL and re-exposes
  // the pick, restore is its undo. Only the restore side skips the prompt.
  await render(<BetEditSheet route={routeFor(bet)} />);
  await fireEvent.press(screen.getByText("베팅 안 했음"));
  expect(alertSpy).toHaveBeenCalled();
  expect(api.unmarkBet).not.toHaveBeenCalled();
});

// The confirm dialog above is built from the navigation-param snapshot taken
// when the list loaded. If the settlement worker settles the bet while the
// sheet is open, movesSettledPnl(snapshot) still reads false, so that dialog
// names no amount — but the server's response is authoritative and may carry
// a real pnl_delta/settled_day. A silent server-side PnL change is exactly
// what the confirm dialog exists to prevent, so a follow-up alert must
// disclose it after the fact.

test("서버가 정산 후 실제 PnL 변경을 보고하면 사후 안내를 띄운다", async () => {
  (api.unmarkBet as jest.Mock).mockResolvedValueOnce({
    ok: true, pnl_delta: -45, settled_day: "2026-08-09",
  });
  await render(<BetEditSheet route={routeFor(bet)} />);
  await fireEvent.press(screen.getByText("베팅 안 했음"));
  confirmLastAlert();
  await waitFor(() => expect(api.unmarkBet).toHaveBeenCalledWith(7));
  await waitFor(() => expect(alertSpy).toHaveBeenCalledTimes(2));
  const [message] = alertSpy.mock.calls[1] as unknown as [string];
  expect(message).toBe("8월 9일 PnL이 -45.00 변경되었습니다.");
  expect(mockPopToTop).toHaveBeenCalled();
});

test("사후 안내도 서버가 준 동부시간 날짜를 그대로 쓴다", async () => {
  // The post-hoc path takes its day straight from the response, so it is
  // correct as soon as the server stops slicing UTC — but nothing pinned that,
  // because the only case here used 2026-08-09, same day in both frames. This
  // is the response for a 10pm ET Aug 12 settlement.
  (api.unmarkBet as jest.Mock).mockResolvedValueOnce({
    ok: true, pnl_delta: -45, settled_day: "2026-08-12",
  });
  await render(<BetEditSheet route={routeFor(bet)} />);
  await fireEvent.press(screen.getByText("베팅 안 했음"));
  confirmLastAlert();
  await waitFor(() => expect(alertSpy).toHaveBeenCalledTimes(2));
  const [message] = alertSpy.mock.calls[1] as unknown as [string];
  expect(message).toBe("8월 12일 PnL이 -45.00 변경되었습니다.");
});

test("pnl_delta 가 null 이면 사후 안내를 띄우지 않는다", async () => {
  // unmarkBet's default mock in this file already resolves { pnl_delta: null,
  // settled_day: null } — the ordinary case where the snapshot was accurate.
  await render(<BetEditSheet route={routeFor(bet)} />);
  await fireEvent.press(screen.getByText("베팅 안 했음"));
  confirmLastAlert();
  await waitFor(() => expect(api.unmarkBet).toHaveBeenCalledWith(7));
  await waitFor(() => expect(mockPopToTop).toHaveBeenCalled());
  expect(alertSpy).toHaveBeenCalledTimes(1); // only the confirm dialog
});
