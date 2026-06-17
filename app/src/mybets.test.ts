// app/src/mybets.test.ts
import { calendarSummary, dayTint } from "./mybets";
import { color } from "./theme";

test("calendarSummary totals and sorts days descending", () => {
  const out = calendarSummary({ "2026-06-11": 2.4, "2026-06-12": -1.0, "2026-06-10": 5.0 });
  expect(out.total).toBeCloseTo(6.4);
  expect(out.days.map((d) => d.day)).toEqual(["2026-06-12", "2026-06-11", "2026-06-10"]);
});

test("calendarSummary handles an empty calendar", () => {
  expect(calendarSummary({})).toEqual({ total: 0, days: [] });
});

test("dayTint maps PnL to heatmap intensity", () => {
  expect(dayTint(undefined)).toBe(color.heatmapEmpty);
  expect(dayTint(0)).toBe(color.heatmapEmpty);
  expect(dayTint(5)).toBe(color.positiveDim);
  expect(dayTint(25)).toBe(color.positiveStrong);
  expect(dayTint(-5)).toBe(color.negativeDim);
  expect(dayTint(-25)).toBe(color.negativeStrong);
});
