// app/src/mybets.ts
import { color } from "./theme";

export interface DayPnl {
  day: string;
  pnl: number;
}
export interface CalendarSummary {
  total: number;
  days: DayPnl[];
}

export function calendarSummary(calendar: Record<string, number>): CalendarSummary {
  const days = Object.entries(calendar)
    .map(([day, pnl]) => ({ day, pnl }))
    .sort((a, b) => (a.day < b.day ? 1 : -1));
  const total = days.reduce((sum, d) => sum + d.pnl, 0);
  return { total, days };
}

export function dayTint(pnl: number | undefined): string {
  if (pnl === undefined || pnl === 0) return color.heatmapEmpty;
  if (pnl > 0) return pnl >= 20 ? color.positiveStrong : color.positiveDim;
  return pnl <= -20 ? color.negativeStrong : color.negativeDim;
}
