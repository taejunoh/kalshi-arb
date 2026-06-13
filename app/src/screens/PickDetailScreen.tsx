// app/src/screens/PickDetailScreen.tsx
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useNavigation } from "@react-navigation/native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import { api } from "../api/client";
import type { RefreshResponse } from "../api/types";
import type { PicksStackParamList } from "../navigation/types";
import { openOrCopy, isVerifiedPrefill } from "../deeplink";
import { decimalOdds, evPct } from "../format";

// Indexed access (not the `Pick<>` utility) so files that import the API `Pick`
// type don't shadow it.
type Props = { route: NativeStackScreenProps<PicksStackParamList, "PickDetail">["route"] };

export function PickDetailScreen({ route }: Props) {
  const nav = useNavigation<{ push: (s: string, p?: object) => void }>();
  const { pickId } = route.params;
  const [data, setData] = useState<RefreshResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.refreshPick(pickId));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [pickId]);

  useEffect(() => { void load(); }, [load]);

  return (
    <ScrollView style={s.container} contentContainerStyle={{ padding: 12 }}>
      <View style={s.headerRow}>
        <Text style={s.fair}>{data?.fair != null ? `공정확률 ${(data.fair * 100).toFixed(1)}%` : "—"}</Text>
        <Pressable style={s.refresh} onPress={() => void load()}><Text style={s.refreshText}>새로고침{data ? ` (${data.credits})` : ""}</Text></Pressable>
      </View>
      {loading && <ActivityIndicator style={{ marginTop: 24 }} />}
      {error && <Text style={s.err}>로드 실패: {error}</Text>}
      {data?.books.map((b) => {
        const verified = isVerifiedPrefill(b.book, b.deeplink);
        return (
          <Pressable key={b.book} style={s.bookRow} onPress={() => void openOrCopy(b.deeplink)}>
            <View style={{ flex: 1 }}>
              <Text style={s.bookName}>{b.book}</Text>
              <Text style={s.tier}>{verified ? "✓ 슬립 프리필" : "앱 열기 + 복사"}</Text>
            </View>
            <Text style={s.odds}>{decimalOdds(b.odds)}</Text>
            <Text style={s.bookEv}>{evPct(b.ev)}</Text>
          </Pressable>
        );
      })}
      <Pressable style={s.betBtn} onPress={() => nav.push("CaptureSheet", { pickId })}>
        <Text style={s.betText}>베팅함 기록</Text>
      </Pressable>
    </ScrollView>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#0E0E13" },
  headerRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 8 },
  fair: { color: "#9CA3AF", fontSize: 14 },
  refresh: { backgroundColor: "#1F2937", paddingHorizontal: 12, paddingVertical: 6, borderRadius: 8 },
  refreshText: { color: "#FFF", fontSize: 12 },
  err: { color: "#F87171", marginTop: 12 },
  bookRow: { flexDirection: "row", alignItems: "center", backgroundColor: "#1A1A22", borderRadius: 10, padding: 14, marginVertical: 5 },
  bookName: { color: "#FFF", fontSize: 16, fontWeight: "600" },
  tier: { color: "#60A5FA", fontSize: 12, marginTop: 2 },
  odds: { color: "#FFF", fontSize: 16, fontWeight: "700", marginHorizontal: 10 },
  bookEv: { color: "#34D399", fontSize: 14, width: 56, textAlign: "right" },
  betBtn: { backgroundColor: "#7C3AED", borderRadius: 10, padding: 14, alignItems: "center", marginTop: 16 },
  betText: { color: "#FFF", fontWeight: "700", fontSize: 16 },
});
