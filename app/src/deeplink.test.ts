// app/src/deeplink.test.ts
jest.mock("expo-linking", () => ({
  canOpenURL: jest.fn(),
  openURL: jest.fn(async () => true),
}));
jest.mock("expo-clipboard", () => ({ setStringAsync: jest.fn(async () => true) }));
jest.mock("./config", () => ({ VERIFIED_PREFILL_BOOKS: ["FanDuel"] }));

import * as Linking from "expo-linking";
import * as Clipboard from "expo-clipboard";
import { openOrCopy, isVerifiedPrefill } from "./deeplink";
import type { Deeplink } from "./api/types";

afterEach(() => jest.clearAllMocks());

const COPY = "FanDuel · Nimmo TB Over 1.5 @ +120";

test("verified prefill (isPrefill) opens the slip without copying", async () => {
  const dl: Deeplink = { tier: "prefill", url: "https://sportsbook.fanduel.com/addToBetslip?x=1", copy_text: COPY };
  const r = await openOrCopy(dl, true);
  expect(r).toBe("prefilled");
  expect(Linking.canOpenURL).not.toHaveBeenCalled();
  expect(Linking.openURL).toHaveBeenCalledWith(dl.url);
  expect(Clipboard.setStringAsync).not.toHaveBeenCalled(); // slip is filled — no noise copy
});

test("non-prefill https link opens the app AND copies the bet text", async () => {
  const dl: Deeplink = { tier: "home", url: "https://fanatics.onelink.me/5kut/x", copy_text: COPY };
  const r = await openOrCopy(dl, false);
  expect(r).toBe("opened_and_copied");
  expect(Linking.openURL).toHaveBeenCalledWith(dl.url);
  expect(Clipboard.setStringAsync).toHaveBeenCalledWith(COPY);
});

test("custom scheme checks canOpenURL then opens (prefill -> no copy)", async () => {
  (Linking.canOpenURL as jest.Mock).mockResolvedValue(true);
  const r = await openOrCopy({ tier: "prefill", url: "dksb://sb/addbet/S1", copy_text: COPY }, true);
  expect(r).toBe("prefilled");
  expect(Linking.canOpenURL).toHaveBeenCalled();
  expect(Clipboard.setStringAsync).not.toHaveBeenCalled();
});

test("custom scheme that throws canOpenURL falls back to clipboard", async () => {
  (Linking.canOpenURL as jest.Mock).mockRejectedValue(new Error("not whitelisted"));
  const r = await openOrCopy({ tier: "home", url: "dksb://sb/addbet/S1", copy_text: COPY });
  expect(r).toBe("copied");
  expect(Clipboard.setStringAsync).toHaveBeenCalledWith(COPY);
});

test("null url copies the text", async () => {
  const r = await openOrCopy({ tier: "home", url: null, copy_text: COPY });
  expect(r).toBe("copied");
  expect(Clipboard.setStringAsync).toHaveBeenCalledWith(COPY);
});

test("isVerifiedPrefill is true only for prefill tier on an allowlisted book", () => {
  const pre: Deeplink = { tier: "prefill", url: "x", copy_text: COPY };
  expect(isVerifiedPrefill("FanDuel", pre)).toBe(true);
  expect(isVerifiedPrefill("DraftKings", pre)).toBe(false); // not allowlisted
  expect(isVerifiedPrefill("FanDuel", { ...pre, tier: "event" })).toBe(false);
});

import { VERIFIED_PREFILL_BOOKS } from "./config";

const PREFILL_DL = { tier: "prefill" as const, url: "https://x", copy_text: "FanDuel · NYM ML @ +120" };
const EVENT_DL = { tier: "event" as const, url: "https://x", copy_text: "DraftKings · NYM ML @ +110" };

test("verified prefill label only shows for tier=prefill AND a verified book", () => {
  for (const book of VERIFIED_PREFILL_BOOKS) {
    expect(isVerifiedPrefill(book, PREFILL_DL)).toBe(true);
  }
  expect(isVerifiedPrefill(VERIFIED_PREFILL_BOOKS[0] ?? "FanDuel", EVENT_DL)).toBe(false);
  expect(isVerifiedPrefill("BetMGM", PREFILL_DL)).toBe(false);
});
