// app/src/api/client.ts
import { getCredentials } from "../storage";
import type {
  BetIn,
  BetPatch,
  MuteRule,
  MyBetsResponse,
  Pick,
  PicksResponse,
  RefreshResponse,
  ScanDraft,
  Settings,
  UnmarkResponse,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const c = await getCredentials();
  if (!c) throw new ApiError(0, "No API credentials configured");
  const res = await fetch(c.baseUrl + path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${c.token}`,
      "CF-Access-Client-Id": c.cfId,
      "CF-Access-Client-Secret": c.cfSecret,
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) throw new ApiError(res.status, `${init?.method ?? "GET"} ${path} -> ${res.status}`);
  return (await res.json()) as T;
}

const enc = encodeURIComponent;

export const api = {
  getPicks: () => request<PicksResponse>("/picks"),
  getPick: (id: string) => request<Pick>(`/picks/${enc(id)}`),
  refreshPick: (id: string) => request<RefreshResponse>(`/picks/${enc(id)}/refresh`),
  postBet: (bet: BetIn) =>
    request<{ id: number }>("/bets", { method: "POST", body: JSON.stringify(bet) }),
  getMyBets: () => request<MyBetsResponse>("/mybets"),
  patchBet: (id: number, body: BetPatch) =>
    request<{ ok: boolean }>(`/bets/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  unmarkBet: (id: number) =>
    request<UnmarkResponse>(`/bets/${id}/unmark`, { method: "POST" }),
  remarkBet: (id: number) =>
    request<{ ok: boolean }>(`/bets/${id}/unmark`, { method: "DELETE" }),
  dismissPick: (id: string) =>
    request<{ ok: boolean }>(`/picks/${enc(id)}/dismiss`, { method: "POST" }),
  undismissPick: (id: string) =>
    request<{ ok: boolean }>(`/picks/${enc(id)}/undismiss`, { method: "POST" }),
  getSettings: () => request<Settings>("/settings"),
  putSettings: (s: Settings) =>
    request<{ ok: boolean }>("/settings", { method: "PUT", body: JSON.stringify(s) }),
  registerPush: (token: string) =>
    request<{ ok: boolean }>("/push/register", {
      method: "POST",
      body: JSON.stringify({ token }),
    }),
  scanBet: (image_base64: string, media_type = "image/jpeg") =>
    request<ScanDraft>("/bets/scan", {
      method: "POST",
      body: JSON.stringify({ image_base64, media_type }),
    }),
  getRules: () => request<{ rules: MuteRule[] }>("/rules"),
  addRule: (field: MuteRule["field"], value: string) =>
    request<{ id: number }>("/rules", { method: "POST", body: JSON.stringify({ field, value }) }),
  deleteRule: (id: number) =>
    request<{ ok: boolean }>(`/rules/${id}`, { method: "DELETE" }),
};
