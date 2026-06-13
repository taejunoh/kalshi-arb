// app/src/storage.ts
import * as SecureStore from "expo-secure-store";

export type Credentials = { baseUrl: string; token: string; cfId: string; cfSecret: string };

// Keys must be [A-Za-z0-9._-] only (SecureStore constraint).
const KEYS = {
  baseUrl: "api_base_url",
  token: "app_api_token",
  cfId: "cf_access_client_id",
  cfSecret: "cf_access_client_secret",
} as const;

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
