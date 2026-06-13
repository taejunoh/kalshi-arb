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
jest.mock("../deeplink", () => ({ openOrCopy: jest.fn(async () => "opened"), isVerifiedPrefill: () => false }));
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

test("tapping a book row opens or copies its deep-link", async () => {
  await render(<PickDetailScreen route={route} />);
  await waitFor(() => expect(screen.getByText(/FanDuel/)).toBeOnTheScreen());
  fireEvent.press(screen.getByText(/FanDuel/));
  expect(openOrCopy).toHaveBeenCalled();
});
