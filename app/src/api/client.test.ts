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
