// app/src/api/client.test.ts
jest.mock("../storage", () => ({
  getCredentials: jest.fn(async () => ({
    baseUrl: "https://api.example.com",
    token: "tok",
    cfId: "cfid",
    cfSecret: "cfsecret",
  })),
}));

import { api, ApiError } from "./client";
import { getCredentials } from "../storage";
import type { BetPatch } from "./types";

function mockFetch(body: unknown, ok = true, status = 200) {
  return jest.fn(async () => ({ ok, status, json: async () => body })) as unknown as typeof fetch;
}

afterEach(() => jest.clearAllMocks());

test("getPicks hits /picks with the three auth headers", async () => {
  const f = mockFetch({ picks: [], counts: {}, cooled: [] });
  global.fetch = f;
  await api.getPicks();
  const [url, init] = (f as jest.Mock).mock.calls[0];
  expect(url).toBe("https://api.example.com/picks");
  expect(init.headers.Authorization).toBe("Bearer tok");
  expect(init.headers["CF-Access-Client-Id"]).toBe("cfid");
  expect(init.headers["CF-Access-Client-Secret"]).toBe("cfsecret");
});

test("refreshPick URL-encodes the pipe-and-space pick id", async () => {
  const f = mockFetch({ fair: null, books: [], credits: 0 });
  global.fetch = f;
  await api.refreshPick("E1|batter_total_bases|Over|1.5|Brandon Nimmo");
  const [url] = (f as jest.Mock).mock.calls[0];
  expect(url).toBe(
    "https://api.example.com/picks/E1%7Cbatter_total_bases%7COver%7C1.5%7CBrandon%20Nimmo/refresh",
  );
});

test("postBet sends a JSON body and POST method", async () => {
  const f = mockFetch({ id: 7 });
  global.fetch = f;
  const out = await api.postBet({ event_id: "E1", market: "h2h", side: "NYM", source: "one_tap" });
  expect(out.id).toBe(7);
  const [, init] = (f as jest.Mock).mock.calls[0];
  expect(init.method).toBe("POST");
  expect(JSON.parse(init.body).event_id).toBe("E1");
});

test("a non-ok response throws ApiError with the status", async () => {
  global.fetch = mockFetch({}, false, 401);
  await expect(api.getPicks()).rejects.toBeInstanceOf(ApiError);
  await expect(api.getPicks()).rejects.toMatchObject({ status: 401 });
});

test("missing credentials throws before fetch", async () => {
  (getCredentials as jest.Mock).mockResolvedValueOnce(null);
  const f = mockFetch({});
  global.fetch = f;
  await expect(api.getPicks()).rejects.toBeInstanceOf(ApiError);
  expect((f as jest.Mock).mock.calls).toHaveLength(0);
});

test("scanBet posts the image and parses a ScanDraft", async () => {
  const draft = {
    bet: {
      event_id: "EVT123", sport: "baseball_mlb", home: "COL", away: "CHC",
      commence_time: null, market: "batter_total_bases", side: "Over", point: 1.5,
      player: "Nico Hoerner", label: "Nico Hoerner TB Over 1.5", book: "FanDuel",
      rec_odds: null, fill_odds: 2.0, stake: 2.0, source: "scan",
    },
    matched: true, notes: "single leg",
  };
  const fetchMock = jest.fn(
    async (..._args: Parameters<typeof fetch>) => ({ ok: true, status: 200, json: async () => draft }),
  );
  (global as unknown as { fetch: typeof fetch }).fetch = fetchMock as never;

  const { api } = require("./client");
  const out = await api.scanBet("QkFTRTY0", "image/png");
  expect(out.matched).toBe(true);
  expect(out.bet.event_id).toBe("EVT123");
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(String(url)).toContain("/bets/scan");
  expect(init.method).toBe("POST");
  expect(JSON.parse(init.body as string)).toEqual({
    image_base64: "QkFTRTY0",
    media_type: "image/png",
  });
});

test("rules CRUD client methods hit the right paths", async () => {
  const fetchMock = jest.fn(async (url: string, init?: { method?: string }) => {
    if (String(url).endsWith("/rules") && (!init || init.method === undefined))
      return { ok: true, status: 200, json: async () => ({ rules: [{ id: 1, field: "player", value: "Nico Hoerner", created_at: "t" }] }) };
    if (String(url).endsWith("/rules") && init?.method === "POST")
      return { ok: true, status: 200, json: async () => ({ id: 2 }) };
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  });
  (global as unknown as { fetch: typeof fetch }).fetch = fetchMock as never;
  const { api } = require("./client");
  expect((await api.getRules()).rules[0].field).toBe("player");
  expect((await api.addRule("market", "total")).id).toBe(2);
  expect((await api.deleteRule(2)).ok).toBe(true);
  expect(fetchMock.mock.calls.some(([u, i]) => String(u).endsWith("/rules/2") && i?.method === "DELETE")).toBe(true);
});

test("getMyBets reads /mybets", async () => {
  const f = mockFetch({ calendar: {}, open: [], settled: [], not_placed: [] });
  global.fetch = f;
  const r = await api.getMyBets();
  expect((f as jest.Mock).mock.calls[0][0]).toBe("https://api.example.com/mybets");
  expect(r.not_placed).toEqual([]);
});

test("patchBet sends PATCH with only the supplied fields", async () => {
  const f = mockFetch({ ok: true });
  global.fetch = f;
  await api.patchBet(7, { stake: 25 });
  const [url, init] = (f as jest.Mock).mock.calls[0];
  expect(url).toBe("https://api.example.com/bets/7");
  expect(init.method).toBe("PATCH");
  expect(JSON.parse(init.body)).toEqual({ stake: 25 });
});

// Type-level guard: BetPatch must reject an explicitly-null stake/fill_odds at
// compile time, since the server 422s them (a placed bet without a stake/fill_odds
// isn't a record of anything, and the settle worker multiplies by both) — this is
// exactly the mistake a "clear this field" edit-screen interaction could make. A
// null `book` must still type-check, since it's descriptive metadata the server
// already accepts as null on creation. If either @ts-expect-error below stops
// erroring (i.e. the type got widened back), this file fails `tsc --noEmit`.
test("BetPatch rejects null stake/fill_odds at the type level but still allows null book", () => {
  // @ts-expect-error stake must not be null — server 422s a placed bet without a stake
  const badStake: BetPatch = { stake: null };
  // @ts-expect-error fill_odds must not be null — server 422s a placed bet without fill_odds
  const badFillOdds: BetPatch = { fill_odds: null };
  const okBook: BetPatch = { book: null };
  expect(badStake).toBeDefined();
  expect(badFillOdds).toBeDefined();
  expect(okBook).toBeDefined();
});

test("unmarkBet POSTs and remarkBet DELETEs the same path", async () => {
  const f = mockFetch({ ok: true, pnl_delta: null, settled_day: null });
  global.fetch = f;
  await api.unmarkBet(7);
  await api.remarkBet(7);
  const calls = (f as jest.Mock).mock.calls;
  expect(calls[0][0]).toBe("https://api.example.com/bets/7/unmark");
  expect(calls[0][1].method).toBe("POST");
  expect(calls[1][0]).toBe("https://api.example.com/bets/7/unmark");
  expect(calls[1][1].method).toBe("DELETE");
});
