// app/src/format.test.ts
import { americanOdds, parseOddsToDecimal, evPct, countdown, countdownUrgent } from "./format";

test("americanOdds shows the US-book number (+underdog / -favorite)", () => {
  expect(americanOdds(3.9)).toBe("+290");
  expect(americanOdds(2.0)).toBe("+100");
  expect(americanOdds(1.91)).toBe("-110");
  expect(americanOdds(1.5)).toBe("-200");
  expect(americanOdds(null)).toBe("—");
  expect(americanOdds(1.0)).toBe("—");
});

test("parseOddsToDecimal accepts american or decimal (mirrors the Telegram parser)", () => {
  expect(parseOddsToDecimal("+105")).toBeCloseTo(2.05, 5);
  expect(parseOddsToDecimal("-120")).toBeCloseTo(1.8333, 3);
  expect(parseOddsToDecimal("150")).toBeCloseTo(2.5, 5);   // bare int >=100 = american
  expect(parseOddsToDecimal("1.98")).toBeCloseTo(1.98, 5); // decimal stays decimal
  expect(parseOddsToDecimal("+110")).toBeCloseTo(2.1, 5);  // round-trips americanOdds(2.1)
  expect(parseOddsToDecimal("")).toBeNull();
  expect(parseOddsToDecimal("+50")).toBeNull();            // |american| < 100 is invalid
  expect(parseOddsToDecimal("abc")).toBeNull();
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
