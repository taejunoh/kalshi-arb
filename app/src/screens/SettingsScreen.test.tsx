// app/src/screens/SettingsScreen.test.tsx
jest.mock("../api/client", () => ({
  api: {
    getSettings: jest.fn(async () => ({ min_ev: 0.02, sports: ["baseball_mlb"], muted_books: ["BallyBet"], sort: "ev" })),
    putSettings: jest.fn(async () => ({ ok: true })),
    registerPush: jest.fn(async () => ({ ok: true })),
  },
}));
jest.mock("../storage", () => ({ saveCredentials: jest.fn(async () => undefined), getCredentials: jest.fn(async () => null) }));
jest.mock("../push", () => ({ registerForPush: jest.fn(async () => "ExponentPushToken[x]") }));

import { render, screen, waitFor, fireEvent } from "@testing-library/react-native";
import { SettingsScreen } from "./SettingsScreen";
import { api } from "../api/client";
import { saveCredentials } from "../storage";

test("loads current settings and saves a changed sort", async () => {
  await render(<SettingsScreen />);
  await waitFor(() => expect(screen.getByText("MLB")).toBeOnTheScreen());
  await fireEvent.press(screen.getByText("시간순"));
  await waitFor(() => expect(api.putSettings).toHaveBeenCalled());
  const sent = (api.putSettings as jest.Mock).mock.calls.at(-1)?.[0];
  expect(sent.sort).toBe("time");
});

test("saves typed credentials to the keychain", async () => {
  await render(<SettingsScreen />);
  await waitFor(() => expect(screen.getByPlaceholderText("API base URL")).toBeOnTheScreen());
  await fireEvent.changeText(screen.getByPlaceholderText("API base URL"), "https://api.example.com");
  await fireEvent.changeText(screen.getByPlaceholderText("APP_API_TOKEN"), "tok");
  await fireEvent.changeText(screen.getByPlaceholderText("CF-Access-Client-Id"), "cfid");
  await fireEvent.changeText(screen.getByPlaceholderText("CF-Access-Client-Secret"), "cfsecret");
  await fireEvent.press(screen.getByText("자격 증명 저장"));
  await waitFor(() => expect(saveCredentials).toHaveBeenCalledWith({ baseUrl: "https://api.example.com", token: "tok", cfId: "cfid", cfSecret: "cfsecret" }));
});
