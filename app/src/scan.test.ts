jest.mock("expo-image-picker", () => ({
  MediaTypeOptions: { Images: "Images" },
  requestCameraPermissionsAsync: jest.fn(async () => ({ granted: true })),
  requestMediaLibraryPermissionsAsync: jest.fn(async () => ({ granted: true })),
  launchCameraAsync: jest.fn(),
  launchImageLibraryAsync: jest.fn(),
}));

import * as ImagePicker from "expo-image-picker";
import { pickImage, draftToBetIn } from "./scan";
import type { BetInDraft } from "./api/types";

const DRAFT: BetInDraft = {
  event_id: "EVT123", sport: "baseball_mlb", home: "COL", away: "CHC",
  commence_time: null, market: "batter_total_bases", side: "Over", point: 1.5,
  player: "Nico Hoerner", label: "Nico Hoerner TB Over 1.5", book: "FanDuel",
  rec_odds: null, fill_odds: 2.0, stake: 2.0, source: "scan",
};

test("pickImage returns base64 + mediaType on a successful library pick", async () => {
  (ImagePicker.launchImageLibraryAsync as jest.Mock).mockResolvedValueOnce({
    canceled: false, assets: [{ base64: "QkFTRTY0", mimeType: "image/png" }],
  });
  await expect(pickImage("library")).resolves.toEqual({ base64: "QkFTRTY0", mediaType: "image/png" });
});

test("pickImage returns null when permission denied", async () => {
  (ImagePicker.requestCameraPermissionsAsync as jest.Mock).mockResolvedValueOnce({ granted: false });
  await expect(pickImage("camera")).resolves.toBeNull();
});

test("pickImage returns null when the user cancels", async () => {
  (ImagePicker.launchImageLibraryAsync as jest.Mock).mockResolvedValueOnce({ canceled: true });
  await expect(pickImage("library")).resolves.toBeNull();
});

test("draftToBetIn maps draft fields and applies user inputs (source stays scan)", () => {
  const bet = draftToBetIn(DRAFT, { stake: 5, fillOdds: 2.05 });
  expect(bet).toMatchObject({
    event_id: "EVT123", market: "batter_total_bases", side: "Over",
    player: "Nico Hoerner", stake: 5, fill_odds: 2.05, source: "scan",
  });
});

test("draftToBetIn falls back to draft values and empty event_id when unmatched", () => {
  const bet = draftToBetIn({ ...DRAFT, event_id: null, stake: null }, {});
  expect(bet.event_id).toBe("");
  expect(bet.fill_odds).toBe(2.0);
  expect(bet.stake).toBeNull();
});
