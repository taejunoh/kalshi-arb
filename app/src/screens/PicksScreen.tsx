// app/src/screens/PicksScreen.tsx
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, FlatList, Pressable, StyleSheet, Text, View } from "react-native";
import { useFocusEffect, useNavigation } from "@react-navigation/native";
import { api } from "../api/client";
import type { Pick, PicksResponse, Settings, SortKey } from "../api/types";
import { PickCard } from "../components/PickCard";
import { pickImage } from "../scan";
import { MIN_EV_STEPS } from "../config";
import { color, radius, space } from "../theme";

export function PicksScreen() {
  const nav = useNavigation<{ push: (s: string, p?: object) => void; navigate: (s: string, p?: object) => void }>();
  const [data, setData] = useState<PicksResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [prefs, setPrefs] = useState<Settings>({ min_ev: 0, sports: null, muted_books: null, sort: "ev" });
  const [showCooled, setShowCooled] = useState(false);
  const [betOnly, setBetOnly] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.getPicks());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        setPrefs(await api.getSettings());
      } catch {
        /* no creds yet — keep defaults */
      }
    })();
  }, []);

  useFocusEffect(useCallback(() => { void load(); }, [load]));

  const applyFilters = useCallback(
    async (next: { min_ev?: number; sort?: SortKey }) => {
      const updated: Settings = {
        ...prefs,
        min_ev: next.min_ev ?? prefs.min_ev,
        sort: next.sort ?? prefs.sort,
      };
      setPrefs(updated);
      try {
        await api.putSettings(updated);
      } catch {
        /* settings sync best-effort; UI already updated */
      }
      void load();
    },
    [prefs, load],
  );

  const onCapture = useCallback(async (source: "camera" | "library") => {
    const img = await pickImage(source);
    if (!img) return;
    try {
      const draft = await api.scanBet(img.base64, img.mediaType);
      nav.navigate("CaptureSheet", { scanDraft: draft.bet });
    } catch {
      Alert.alert("슬립을 읽지 못했어요. 다시 시도하세요.");
    }
  }, [nav]);

  const dismiss = useCallback(async (p: Pick) => {
    setData((d) => (d ? { ...d, picks: d.picks.filter((x) => x.id !== p.id) } : d)); // optimistic
    try {
      await api.dismissPick(p.id);
    } catch {
      void load(); // revert by reloading on failure
    }
  }, [load]);

  if (error) {
    return (
      <View style={s.center}>
        <Text style={s.err}>로드 실패: {error}</Text>
        <Pressable style={s.retry} onPress={() => void load()}><Text style={s.retryText}>다시 시도</Text></Pressable>
      </View>
    );
  }

  const allActive = data?.picks ?? [];
  const visiblePicks = betOnly ? allActive.filter((p) => p.should_bet) : allActive;
  const betCount = allActive.filter((p) => p.should_bet).length;

  return (
    <View style={s.container}>
      <View style={s.filterBar}>
        <Pressable testID="scan-capture-library" onPress={() => void onCapture("library")} style={s.scanChip}>
          <Text style={s.chipText}>📷 슬립 캡처</Text>
        </Pressable>
        <Pressable onPress={() => setBetOnly((v) => !v)} style={[s.feChip, betOnly && s.feChipOn]}>
          <Text style={s.chipText}>🟣 BET만{betCount ? ` (${betCount})` : ""}</Text>
        </Pressable>
        <Text style={s.filterLabel}>최소 EV</Text>
        {MIN_EV_STEPS.map((v) => (
          <Pressable key={v} onPress={() => void applyFilters({ min_ev: v })} style={[s.chip, prefs.min_ev === v && s.chipOn]}>
            <Text style={s.chipText}>{v === 0 ? "전체" : `${(v * 100).toFixed(0)}%`}</Text>
          </Pressable>
        ))}
        {(["ev", "time", "n_books"] as SortKey[]).map((k) => (
          <Pressable key={k} onPress={() => void applyFilters({ sort: k })} style={[s.chip, prefs.sort === k && s.chipOn]}>
            <Text style={s.chipText}>{k === "ev" ? "EV순" : k === "time" ? "시간순" : "북수순"}</Text>
          </Pressable>
        ))}
      </View>
      <FlatList
        data={visiblePicks}
        keyExtractor={(p) => p.id}
        refreshing={loading}
        onRefresh={() => void load()}
        renderItem={({ item }) => (
          // "베팅" + 카드 탭 = PickDetail (북별 호가 + 딥링크로 바로 베팅). 기록은
          // PickDetail의 "베팅함 기록"에서 (베팅 후) — 빠른 베팅 > 사후 기록.
          <PickCard
            pick={item}
            onOpen={(p) => nav.push("PickDetail", { pickId: p.id })}
            onBet={(p) => nav.push("PickDetail", { pickId: p.id })}
            onDismiss={(p) => void dismiss(p)}
          />
        )}
        ListEmptyComponent={
          loading ? <ActivityIndicator style={{ marginTop: 40 }} />
            : <Text style={s.empty}>{betOnly ? "BET 픽 없음 (게임 first-entry만 베팅)" : "활성 픽 없음"}</Text>
        }
        ListFooterComponent={
          data && data.cooled.length > 0 ? (
            <View>
              <Pressable style={s.cooledHeader} onPress={() => setShowCooled((v) => !v)}>
                <Text style={s.cooledText}>식은 픽 {data.cooled.length} {showCooled ? "▲" : "▼"}</Text>
              </Pressable>
              {showCooled &&
                data.cooled.map((p) => (
                  <PickCard key={p.id} pick={p} onOpen={(x) => nav.push("PickDetail", { pickId: x.id })} onBet={(x) => nav.push("PickDetail", { pickId: x.id })} onDismiss={(x) => void dismiss(x)} />
                ))}
            </View>
          ) : null
        }
      />
    </View>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: color.bg },
  filterBar: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 6, padding: 10 },
  filterLabel: { color: color.muted, fontSize: 12, marginRight: space.xs },
  chip: { paddingHorizontal: 10, paddingVertical: 5, borderRadius: radius.pill, backgroundColor: color.surfaceRaised },
  chipOn: { backgroundColor: color.accent },
  scanChip: { paddingHorizontal: 10, paddingVertical: 5, borderRadius: radius.pill, backgroundColor: color.accentDim },
  feChip: { paddingHorizontal: 10, paddingVertical: 5, borderRadius: radius.pill, backgroundColor: color.surfaceRaised, borderWidth: 1, borderColor: color.accent },
  feChipOn: { backgroundColor: color.accent, borderColor: color.accent },
  chipText: { color: color.text, fontSize: 12 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: color.bg, padding: space.xl },
  err: { color: color.negative, textAlign: "center", marginBottom: space.md },
  retry: { backgroundColor: color.accent, paddingHorizontal: space.lg, paddingVertical: 10, borderRadius: radius.sm },
  retryText: { color: color.onAccent, fontWeight: "600" },
  empty: { color: color.faint, textAlign: "center", marginTop: 40 },
  cooledHeader: { padding: space.lg },
  cooledText: { color: color.muted, fontWeight: "600" },
});
