// app/src/screens/PickDetailScreen.tsx
import { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useNavigation } from "@react-navigation/native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import { api } from "../api/client";
import type { RefreshResponse } from "../api/types";
import type { PicksStackParamList } from "../navigation/types";
import { openOrCopy, isVerifiedPrefill } from "../deeplink";
import { americanOdds, evPct } from "../format";
import { color, radius, space, font } from "../theme";

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

  // Tap a book row: open its slip/app, and (unless it's a verified prefill) copy the SEARCH
  // entity (player/team/matchup name) so the user can paste it into the book's search box.
  // Show the copied term in the toast so a copy isn't a silent no-op and the user knows exactly
  // what to search.
  const onTapBook = useCallback(async (b: Book) => {
    const result = await openOrCopy(b.deeplink, isVerifiedPrefill(b.book, b.deeplink));
    const term = b.deeplink.copy_text;
    if (result === "opened_and_copied") showToast(`${b.book} 앱 열림 · 검색어 "${term}" 복사됨 (검색창에 붙여넣기)`);
    else if (result === "copied") {
      // url === null ⇒ a copy-only book (no AASA + no scheme, e.g. BetRivers/BallyBet): opening
      // it would only launch Safari, so we intentionally open nothing. url set but not opened ⇒
      // the app genuinely failed to launch.
      showToast(b.deeplink.url == null
        ? `검색어 "${term}" 복사됨 · ${b.book} 앱을 직접 열어 검색창에 붙여넣기`
        : `검색어 "${term}" 복사됨 (앱을 열지 못했어요)`);
    }
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
      <ScrollView style={s.container} contentContainerStyle={{ padding: space.md }}>
        <View style={s.headerRow}>
          <Text style={s.fair}>{data?.fair != null ? `공정확률 ${(data.fair * 100).toFixed(1)}%` : "—"}</Text>
          <Pressable style={s.refresh} onPress={() => void load()}><Text style={s.refreshText}>새로고침{data ? ` (${data.credits})` : ""}</Text></Pressable>
        </View>
        {loading && <ActivityIndicator style={{ marginTop: 24 }} />}
        {error && <Text style={s.err}>로드 실패: {error}</Text>}
        {data?.books.map((b, i) => {
          const verified = isVerifiedPrefill(b.book, b.deeplink);
          // A copy-only book (no AASA + no scheme, e.g. BetRivers/BallyBet) has no url — opening
          // it would only launch Safari, so we copy the search term and the user opens the app.
          const copyOnly = !verified && b.deeplink.url == null;
          return (
            <Pressable key={b.book} style={[s.bookRow, i === 0 && s.bookBest]} onPress={() => void onTapBook(b)}>
              <View style={{ flex: 1 }}>
                <Text style={s.bookName}>{b.book}{i === 0 ? <Text style={s.bestTag}> · 추천</Text> : null}</Text>
                <Text style={verified ? s.prefill : s.openCopy}>
                  {verified ? "✓ 슬립 프리필" : (copyOnly ? "복사 · 앱에서 검색" : "앱 열기 + 복사")}
                </Text>
              </View>
              <View style={{ alignItems: "flex-end" }}>
                <Text style={s.odds}>{americanOdds(b.odds)}</Text>
                <Text style={s.bookEv}>{evPct(b.ev)}</Text>
              </View>
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
  root: { flex: 1, backgroundColor: color.bg },
  container: { flex: 1, backgroundColor: color.bg },
  headerRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: space.sm },
  fair: { color: color.muted, fontSize: 14 },
  refresh: { backgroundColor: color.surfaceRaised, paddingHorizontal: space.md, paddingVertical: 6, borderRadius: radius.sm },
  refreshText: { color: color.accentText, fontSize: 12 },
  err: { color: color.negative, marginTop: space.md },
  bookRow: { flexDirection: "row", alignItems: "center", backgroundColor: color.surface, borderRadius: radius.button, padding: space.lg, marginVertical: 5 },
  bookBest: { borderWidth: 1, borderColor: color.accent },
  bookName: { ...font.rowName },
  bestTag: { color: color.accentText, fontSize: 11, fontWeight: "500" },
  prefill: { color: color.positive, fontSize: 11, marginTop: 3 },
  openCopy: { color: color.faint, fontSize: 11, marginTop: 3 },
  odds: { color: color.text, fontSize: 15, fontWeight: "600" },
  bookEv: { color: color.positive, fontSize: 12, marginTop: 2 },
  betBtn: { backgroundColor: color.surfaceRaised, borderRadius: radius.button, padding: space.lg, alignItems: "center", marginTop: space.lg },
  betText: { color: color.accentText, fontWeight: "600", fontSize: 15 },
  toast: { position: "absolute", left: space.lg, right: space.lg, bottom: 28, backgroundColor: color.surfaceRaised, borderRadius: radius.sm, paddingVertical: space.md, paddingHorizontal: space.lg, alignItems: "center", borderWidth: 1, borderColor: color.border },
  toastText: { color: color.text, fontSize: 13, textAlign: "center" },
});
