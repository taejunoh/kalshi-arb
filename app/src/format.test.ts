// app/src/format.test.ts
import { decimalOdds, evPct, pnl, countdown, liveProgressLabel } from "./format";

test("decimalOdds formats to 2 dp", () => {
  expect(decimalOdds(2.2)).toBe("2.20");
  expect(decimalOdds(null)).toBe("—");
});

test("evPct shows a signed percentage", () => {
  expect(evPct(0.074)).toBe("+7.4%");
  expect(evPct(-0.012)).toBe("-1.2%");
  expect(evPct(null)).toBe("—");
});

test("pnl shows a signed 2-dp dollar amount", () => {
  expect(pnl(2.4)).toBe("+2.40");
  expect(pnl(-1)).toBe("-1.00");
  expect(pnl(0)).toBe("+0.00");
});

test("countdown renders relative time from now", () => {
  const now = new Date("2026-06-13T18:00:00Z");
  expect(countdown("2026-06-13T20:10:00Z", now)).toBe("2h 10m");
  expect(countdown("2026-06-13T18:00:30Z", now)).toBe("<1m");
  expect(countdown("2026-06-13T17:00:00Z", now)).toBe("LIVE");
});

test("liveProgressLabel composes current/needed + game state", () => {
  expect(liveProgressLabel({ current: 1, needed: 1.5, remaining: 1, game_state: "Mid 2nd" })).toBe("1 / 1.5 · Mid 2nd");
  expect(liveProgressLabel({ current: null, needed: 1.5, game_state: null })).toBe("— / 1.5");
  expect(liveProgressLabel(null)).toBe("");
});
