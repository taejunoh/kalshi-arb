// app/src/screens/PickDetailScreen.tsx
import { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useNavigation } from "@react-navigation/native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import { api } from "../api/client";
import type { RefreshResponse } from "../api/types";
import type { PicksStackParamList } from "../navigation/types";
import { openOrCopy, isVerifiedPrefill } from "../deeplink";
import { decimalOdds, evPct } from "../format";

type Book = RefreshResponse["books"][number];

// Indexed access (not the `Pick<>` utility) so files that import the API `Pick`
// type don't shadow it.
type Props = { route: NativeStackScreenProps<PicksStackParamList, "PickDetail">["route"] };

export function PickDetailScreen({ route }: Props) {
  const nav = useNavigation<{ push: (s: string, p?: object) => void }>();
  const { pickId } = route.params;
  const [data, setData] = useState<RefreshResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const showToast = useCallback((msg: string) => {
    setToast(msg);
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 2600);
  }, []);
  useEffect(() => () => { if (toastTimer.current) clearTimeout(toastTimer.current); }, []);

  // Tap a book row: open its slip/app, and (unless it's a verified prefill) copy the bet text
  // so the user can paste it into the book. Always surface what happened so a copy isn't a
  // silent no-op (the old behavior felt like "nothing happened").
  const onTapBook = useCallback(async (b: Book) => {
    const result = await openOrCopy(b.deeplink, isVerifiedPrefill(b.book, b.deeplink));
    if (result === "opened_and_copied") showToast(`${b.book} 앱 열림 · 베팅 정보 복사됨 (검색창에 붙여넣기)`);
    else if (result === "copied") showToast("베팅 정보 복사됨 (앱을 열지 못했어요)");
    // "prefilled": opened straight to a pre-filled slip — no toast needed.
  }, [showToast]);

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
    <View style={s.root}>
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
            <Pressable key={b.book} style={s.bookRow} onPress={() => void onTapBook(b)}>
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
      {toast && (
        <View style={s.toast} pointerEvents="none"><Text style={s.toastText}>{toast}</Text></View>
      )}
    </View>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#0E0E13" },
  container: { flex: 1, backgroundColor: "#0E0E13" },
  toast: { position: "absolute", left: 16, right: 16, bottom: 28, backgroundColor: "#1F2937", borderRadius: 10, paddingVertical: 12, paddingHorizontal: 16, alignItems: "center", borderWidth: 1, borderColor: "#374151" },
  toastText: { color: "#FFF", fontSize: 13, textAlign: "center" },
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
