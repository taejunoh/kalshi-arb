// app/src/components/CalendarHeatmap.tsx
import { StyleSheet, Text, View } from "react-native";
import { color, radius, space } from "../theme";
import { dayTint } from "../mybets";

// `calendar`: { "YYYY-MM-DD": pnl }. Renders the month of the most-recent key as a
// 7-col grid; each day cell is tinted by dayTint(pnl); the latest day gets an outline.
export function CalendarHeatmap({ calendar }: { calendar: Record<string, number> }) {
  const keys = Object.keys(calendar).sort();
  const latest = keys.length ? keys[keys.length - 1] : null;
  const ym = (latest ?? "2026-06-01").slice(0, 7); // "YYYY-MM"
  const [y, m] = ym.split("-").map(Number);
  const daysInMonth = new Date(y, m, 0).getDate();
  const days = Array.from({ length: daysInMonth }, (_, i) => i + 1);

  return (
    <View>
      <Text style={s.month}>{ym}</Text>
      <View style={s.grid}>
        {days.map((d) => {
          const key = `${ym}-${String(d).padStart(2, "0")}`;
          const isLatest = key === latest;
          return (
            <View
              key={key}
              testID={`heat-${key}`}
              style={[s.cell, { backgroundColor: isLatest ? color.accent : dayTint(calendar[key]) }, isLatest && s.today]}
            />
          );
        })}
      </View>
      <View style={s.legend}>
        <Legend c={color.positiveStrong} label="승" />
        <Legend c={color.negativeStrong} label="패" />
        <Legend c={color.accent} label="오늘" />
      </View>
    </View>
  );
}

function Legend({ c, label }: { c: string; label: string }) {
  return (
    <View style={s.legendItem}>
      <View style={[s.legendDot, { backgroundColor: c }]} />
      <Text style={s.legendText}>{label}</Text>
    </View>
  );
}

const s = StyleSheet.create({
  month: { color: color.muted, fontSize: 13, fontWeight: "500", marginBottom: 9 },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 5 },
  cell: { width: "12.5%", aspectRatio: 1, borderRadius: radius.sm, maxWidth: 34 },
  today: { borderWidth: 2, borderColor: color.accentText },
  legend: { flexDirection: "row", justifyContent: "center", gap: space.lg, marginTop: space.md },
  legendItem: { flexDirection: "row", alignItems: "center", gap: 5 },
  legendDot: { width: 9, height: 9, borderRadius: 2 },
  legendText: { color: color.faint, fontSize: 11 },
});
