// app/src/format.test.ts
import { decimalOdds, evPct, countdown, countdownUrgent } from "./format";

test("decimalOdds formats to 2 dp", () => {
  expect(decimalOdds(2.2)).toBe("2.20");
  expect(decimalOdds(null)).toBe("—");
});

test("evPct shows a signed percentage", () => {
  expect(evPct(0.074)).toBe("+7.4%");
  expect(evPct(-0.012)).toBe("-1.2%");
  expect(evPct(null)).toBe("—");
});

test("countdown renders relative time from now", () => {
  const now = new Date("2026-06-13T18:00:00Z");
  expect(countdown("2026-06-13T20:10:00Z", now)).toBe("2h 10m");
  expect(countdown("2026-06-13T18:00:30Z", now)).toBe("<1m");
  expect(countdown("2026-06-13T17:00:00Z", now)).toBe("LIVE");
});

test("countdownUrgent: true only when < 1h to start and not started", () => {
  const now = new Date("2026-06-16T12:00:00Z");
  expect(countdownUrgent("2026-06-16T12:45:00Z", now)).toBe(true);
  expect(countdownUrgent("2026-06-16T13:30:00Z", now)).toBe(false);
  expect(countdownUrgent("2026-06-16T11:59:00Z", now)).toBe(false);
});
