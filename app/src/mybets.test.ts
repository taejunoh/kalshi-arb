// app/src/mybets.test.ts
import { calendarSummary } from "./mybets";

test("calendarSummary totals and sorts days descending", () => {
  const out = calendarSummary({ "2026-06-11": 2.4, "2026-06-12": -1.0, "2026-06-10": 5.0 });
  expect(out.total).toBeCloseTo(6.4);
  expect(out.days.map((d) => d.day)).toEqual(["2026-06-12", "2026-06-11", "2026-06-10"]);
});

test("calendarSummary handles an empty calendar", () => {
  expect(calendarSummary({})).toEqual({ total: 0, days: [] });
});
