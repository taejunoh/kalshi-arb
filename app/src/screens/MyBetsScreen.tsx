// app/src/screens/MyBetsScreen.tsx
import { useCallback, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from "react-native";
import { useFocusEffect } from "@react-navigation/native";
import { api } from "../api/client";
import type { MyBets, OpenBet } from "../api/types";
import { calendarSummary } from "../mybets";
import { decimalOdds, liveProgressLabel, pnl } from "../format";
import { CalendarHeatmap } from "../components/CalendarHeatmap";
import { color, radius, space, font } from "../theme";

const STATUS_COLOR: Record<string, string> = {
  winning: color.positive,
  losing: color.negative,
  pending: color.muted,
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
    <ScrollView style={s.container} contentContainerStyle={{ padding: space.md }}>
      <Text style={s.title}>내 베팅</Text>
      <View style={s.metric}>
        <Text style={s.metricLabel}>누적 PnL</Text>
        <Text style={[s.metricVal, { color: summary.total >= 0 ? color.positive : color.negative }]}>{pnl(summary.total)}</Text>
      </View>
      <CalendarHeatmap calendar={data?.calendar ?? {}} />

      <Text style={s.section}>진행 중 ({data?.open.length ?? 0})</Text>
      {loading && <ActivityIndicator style={{ marginTop: space.lg }} />}
      {data?.open.map((b: OpenBet) => (
        <View key={b.id} style={s.betCard}>
          <View style={s.betRow}>
            <Text style={s.betLabel} numberOfLines={1}>{b.label ?? `${b.side} ${b.point ?? ""}`}</Text>
            <Text style={[s.betStatus, { color: STATUS_COLOR[b.live_status ?? "pending"] ?? color.muted }]}>
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
  container: { flex: 1, backgroundColor: color.bg },
  title: { ...font.title, marginBottom: space.md },
  metric: { backgroundColor: color.surface, borderRadius: radius.sm, padding: space.md, marginBottom: space.lg },
  metricLabel: { color: color.faint, fontSize: 11 },
  metricVal: { fontSize: 24, fontWeight: "600", marginTop: 3 },
  section: { color: color.text, fontSize: 16, fontWeight: "600", marginTop: space.xl, marginBottom: space.sm },
  betCard: { backgroundColor: color.surface, borderRadius: radius.sm, padding: space.lg, marginVertical: 5 },
  betRow: { flexDirection: "row", justifyContent: "space-between" },
  betLabel: { color: color.text, fontWeight: "600", flex: 1, paddingRight: space.sm },
  betStatus: { fontWeight: "600", fontSize: 13 },
  betMeta: { color: color.muted, marginTop: 6, fontSize: 13 },
  empty: { color: color.faint, textAlign: "center", marginTop: space.xl },
});
