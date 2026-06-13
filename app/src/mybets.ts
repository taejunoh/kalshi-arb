// app/src/mybets.ts
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
