// app/src/storage.test.ts
jest.mock("expo-secure-store", () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: "whenUnlockedThisDeviceOnly",
  setItemAsync: jest.fn(async () => undefined),
  getItemAsync: jest.fn(async () => null),
  deleteItemAsync: jest.fn(async () => undefined),
}));

import * as SecureStore from "expo-secure-store";
import { saveCredentials, getCredentials, clearCredentials, getUnitStake, setUnitStake } from "./storage";
import { DEFAULT_UNIT_STAKE } from "./config";

afterEach(() => jest.clearAllMocks());

const CREDS = {
  baseUrl: "https://api.example.com/",
  token: "tok",
  cfId: "cfid",
  cfSecret: "cfsecret",
};

test("saveCredentials writes all four keys and strips a trailing slash on baseUrl", async () => {
  await saveCredentials(CREDS);
  const calls = (SecureStore.setItemAsync as jest.Mock).mock.calls;
  expect(calls).toHaveLength(4);
  const base = calls.find((c) => c[0] === "api_base_url");
  expect(base?.[1]).toBe("https://api.example.com"); // no trailing slash
});

test("getCredentials returns null when any key is missing", async () => {
  (SecureStore.getItemAsync as jest.Mock).mockResolvedValue(null);
  expect(await getCredentials()).toBeNull();
});

test("getCredentials returns the bundle when all keys present", async () => {
  (SecureStore.getItemAsync as jest.Mock).mockImplementation(async (k: string) =>
    ({ api_base_url: "https://api.example.com", app_api_token: "tok", cf_access_client_id: "cfid", cf_access_client_secret: "cfsecret" } as Record<string, string>)[k] ?? null,
  );
  expect(await getCredentials()).toEqual(CREDS && { ...CREDS, baseUrl: "https://api.example.com" });
});

test("clearCredentials deletes all four keys (NOT the unit-stake pref)", async () => {
  await clearCredentials();
  const deleted = (SecureStore.deleteItemAsync as jest.Mock).mock.calls.map((c) => c[0]).sort();
  expect(deleted).toEqual(["api_base_url", "app_api_token", "cf_access_client_id", "cf_access_client_secret"]);
  expect(deleted).not.toContain("unit_stake");
});

test("getUnitStake falls back to the default for unset / invalid / non-positive", async () => {
  (SecureStore.getItemAsync as jest.Mock).mockResolvedValueOnce(null);
  expect(await getUnitStake()).toBe(DEFAULT_UNIT_STAKE);
  (SecureStore.getItemAsync as jest.Mock).mockResolvedValueOnce("abc");
  expect(await getUnitStake()).toBe(DEFAULT_UNIT_STAKE);
  (SecureStore.getItemAsync as jest.Mock).mockResolvedValueOnce("0");
  expect(await getUnitStake()).toBe(DEFAULT_UNIT_STAKE);
});

test("getUnitStake returns a stored positive value; setUnitStake persists it", async () => {
  (SecureStore.getItemAsync as jest.Mock).mockResolvedValueOnce("50");
  expect(await getUnitStake()).toBe(50);
  await setUnitStake(35);
  const call = (SecureStore.setItemAsync as jest.Mock).mock.calls.find((c) => c[0] === "unit_stake");
  expect(call?.[1]).toBe("35");
});
