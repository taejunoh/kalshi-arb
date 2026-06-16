// app/src/screens/PickDetailScreen.test.tsx
jest.mock("../api/client", () => ({
  api: {
    refreshPick: jest.fn(async () => ({
      fair: 0.46,
      books: [
        { book: "FanDuel", odds: 2.2, ev: 0.05, deeplink: { tier: "prefill", url: "https://fd/x", copy_text: "FanDuel · NYM ML @ 2.20" } },
        { book: "BetMGM", odds: 2.1, ev: 0.02, deeplink: { tier: "event", url: "https://mgm/e", copy_text: "BetMGM · NYM ML @ 2.10" } },
      ],
      credits: 4,
    })),
  },
}));
jest.mock("../deeplink", () => ({ openOrCopy: jest.fn(async () => "opened_and_copied"), isVerifiedPrefill: () => false }));
jest.mock("@react-navigation/native", () => ({ useNavigation: () => ({ push: jest.fn() }) }));

import { render, screen, waitFor, fireEvent } from "@testing-library/react-native";
import { PickDetailScreen } from "./PickDetailScreen";
import { openOrCopy } from "../deeplink";

const route = { params: { pickId: "E1|h2h|NYM|0|" } } as never;

test("loads refresh quotes and renders per-book rows", async () => {
  await render(<PickDetailScreen route={route} />);
  await waitFor(() => expect(screen.getByText(/FanDuel/)).toBeOnTheScreen());
  expect(screen.getByText(/BetMGM/)).toBeOnTheScreen();
});

test("tapping a book row opens its deep-link (with the prefill flag) and toasts on copy", async () => {
  await render(<PickDetailScreen route={route} />);
  await waitFor(() => expect(screen.getByText(/FanDuel/)).toBeOnTheScreen());
  await fireEvent.press(screen.getByText(/FanDuel/));
  // called with the book's deeplink AND the verified-prefill flag (false here)
  expect(openOrCopy).toHaveBeenCalledWith(expect.objectContaining({ tier: "prefill" }), false);
  // a non-prefill open copies the bet text -> the screen surfaces a "복사됨" toast (no silent no-op)
  await waitFor(() => expect(screen.getByText(/복사됨/)).toBeOnTheScreen());
});
