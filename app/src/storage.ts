// app/src/storage.ts
import * as SecureStore from "expo-secure-store";
import { DEFAULT_UNIT_STAKE } from "./config";

export type Credentials = { baseUrl: string; token: string; cfId: string; cfSecret: string };

// Keys must be [A-Za-z0-9._-] only (SecureStore constraint).
const KEYS = {
  baseUrl: "api_base_url",
  token: "app_api_token",
  cfId: "cf_access_client_id",
  cfSecret: "cf_access_client_secret",
} as const;

// A user PREF, not a credential — kept out of KEYS so clearCredentials never wipes it.
const UNIT_KEY = "unit_stake";

const OPTS = { keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY };

export async function saveCredentials(c: Credentials): Promise<void> {
  const baseUrl = c.baseUrl.replace(/\/+$/, "");
  await Promise.all([
    SecureStore.setItemAsync(KEYS.baseUrl, baseUrl, OPTS),
    SecureStore.setItemAsync(KEYS.token, c.token, OPTS),
    SecureStore.setItemAsync(KEYS.cfId, c.cfId, OPTS),
    SecureStore.setItemAsync(KEYS.cfSecret, c.cfSecret, OPTS),
  ]);
}

export async function getCredentials(): Promise<Credentials | null> {
  const [baseUrl, token, cfId, cfSecret] = await Promise.all([
    SecureStore.getItemAsync(KEYS.baseUrl),
    SecureStore.getItemAsync(KEYS.token),
    SecureStore.getItemAsync(KEYS.cfId),
    SecureStore.getItemAsync(KEYS.cfSecret),
  ]);
  if (!baseUrl || !token || !cfId || !cfSecret) return null;
  return { baseUrl, token, cfId, cfSecret };
}

export async function clearCredentials(): Promise<void> {
  await Promise.all(Object.values(KEYS).map((k) => SecureStore.deleteItemAsync(k)));
}

// Flat per-bet stake ($) — app-local pref (not a server Settings field; it only
// affects display + record prefill, never the feed/filtering). Falls back to the
// default for unset/invalid/non-positive values.
export async function getUnitStake(): Promise<number> {
  const v = await SecureStore.getItemAsync(UNIT_KEY);
  const n = v == null ? NaN : Number(v);
  return Number.isFinite(n) && n > 0 ? n : DEFAULT_UNIT_STAKE;
}

export async function setUnitStake(n: number): Promise<void> {
  await SecureStore.setItemAsync(UNIT_KEY, String(n), OPTS);
}
