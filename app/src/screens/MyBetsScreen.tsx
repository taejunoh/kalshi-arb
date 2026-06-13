// app/src/screens/MyBetsScreen.tsx
import { useCallback, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from "react-native";
import { useFocusEffect } from "@react-navigation/native";
import { api } from "../api/client";
import type { MyBets, OpenBet } from "../api/types";
import { calendarSummary } from "../mybets";
import { decimalOdds, liveProgressLabel, pnl } from "../format";

const STATUS_COLOR: Record<string, string> = {
  winning: "#34D399",
  losing: "#F87171",
  pending: "#9CA3AF",
};

export function MyBetsScreen() {
  const [data, setData] = useState<MyBets | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await api.getMyBets());
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { void load(); }, [load]));

  const summary = calendarSummary(data?.calendar ?? {});

  return (
    <ScrollView style={s.container} contentContainerStyle={{ padding: 12 }}>
      <View style={s.totalCard}>
        <Text style={s.totalLabel}>누적 PnL</Text>
        <Text style={[s.total, { color: summary.total >= 0 ? "#34D399" : "#F87171" }]}>{pnl(summary.total)}</Text>
      </View>
      {summary.days.map((d) => (
        <View key={d.day} style={s.dayRow}>
          <Text style={s.day}>{d.day}</Text>
          <Text style={[s.dayPnl, { color: d.pnl >= 0 ? "#34D399" : "#F87171" }]}>{pnl(d.pnl)}</Text>
        </View>
      ))}

      <Text style={s.section}>진행 중 ({data?.open.length ?? 0})</Text>
      {loading && <ActivityIndicator style={{ marginTop: 16 }} />}
      {data?.open.map((b: OpenBet) => (
        <View key={b.id} style={s.betCard}>
          <View style={s.betRow}>
            <Text style={s.betLabel} numberOfLines={1}>{b.label ?? `${b.side} ${b.point ?? ""}`}</Text>
            <Text style={[s.betStatus, { color: STATUS_COLOR[b.live_status ?? "pending"] ?? "#9CA3AF" }]}>
              {b.live_status ?? "pending"}
            </Text>
          </View>
          <Text style={s.betMeta}>
            {b.book ?? "—"} @ {decimalOdds(b.fill_odds)} · ${b.stake ?? 0}
            {b.live_progress ? ` · ${liveProgressLabel(b.live_progress)}` : ""}
          </Text>
        </View>
      ))}
      {!loading && (data?.open.length ?? 0) === 0 && <Text style={s.empty}>진행 중인 베팅 없음</Text>}
    </ScrollView>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#0E0E13" },
  totalCard: { backgroundColor: "#1A1A22", borderRadius: 12, padding: 16, alignItems: "center", marginBottom: 10 },
  totalLabel: { color: "#9CA3AF", fontSize: 13 },
  total: { fontSize: 28, fontWeight: "800", marginTop: 4 },
  dayRow: { flexDirection: "row", justifyContent: "space-between", paddingVertical: 8, paddingHorizontal: 4, borderBottomColor: "#1F2937", borderBottomWidth: 1 },
  day: { color: "#D1D5DB" },
  dayPnl: { fontWeight: "600" },
  section: { color: "#FFF", fontSize: 16, fontWeight: "700", marginTop: 20, marginBottom: 8 },
  betCard: { backgroundColor: "#1A1A22", borderRadius: 10, padding: 14, marginVertical: 5 },
  betRow: { flexDirection: "row", justifyContent: "space-between" },
  betLabel: { color: "#FFF", fontWeight: "600", flex: 1, paddingRight: 8 },
  betStatus: { fontWeight: "700", fontSize: 13 },
  betMeta: { color: "#9CA3AF", marginTop: 6, fontSize: 13 },
  empty: { color: "#6B7280", textAlign: "center", marginTop: 20 },
});
