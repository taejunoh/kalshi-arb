// app/src/format.ts
import type { LiveProgress } from "./api/types";

export function decimalOdds(px: number | null | undefined): string {
  return px == null ? "—" : px.toFixed(2);
}

export function evPct(ev: number | null | undefined): string {
  if (ev == null) return "—";
  return `${ev >= 0 ? "+" : "-"}${Math.abs(ev * 100).toFixed(1)}%`;
}

export function pnl(n: number | null | undefined): string {
  const v = n ?? 0;
  return `${v >= 0 ? "+" : "-"}${Math.abs(v).toFixed(2)}`;
}

export function countdown(commenceISO: string, now: Date = new Date()): string {
  const start = new Date(commenceISO).getTime();
  const diffMs = start - now.getTime();
  if (diffMs <= 0) return "LIVE";
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "<1m";
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

export function liveProgressLabel(lp: LiveProgress | null | undefined): string {
  if (!lp) return "";
  const cur = lp.current == null ? "—" : String(lp.current);
  const head = `${cur} / ${lp.needed}`;
  return lp.game_state ? `${head} · ${lp.game_state}` : head;
}
