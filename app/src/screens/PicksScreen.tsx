// app/src/screens/PicksScreen.tsx
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, FlatList, Modal, Pressable, StyleSheet, Text, View } from "react-native";
import { useFocusEffect, useNavigation } from "@react-navigation/native";
import { api } from "../api/client";
import type { Pick, PicksResponse, Settings, SortKey } from "../api/types";
import { PickCard } from "../components/PickCard";
import { pickImage } from "../scan";
import { getUnitStake } from "../storage";
import { DEFAULT_UNIT_STAKE } from "../config";
import { Feather } from "@expo/vector-icons";
import { color, radius, space } from "../theme";

const FEED_EV_STEPS = [0, 0.02, 0.04];
const SORT_ORDER: SortKey[] = ["ev", "time", "n_books"];
const SORT_LABEL: Record<SortKey, string> = { ev: "EV순", time: "시간순", n_books: "북수순" };

export function PicksScreen() {
  const nav = useNavigation<{ push: (s: string, p?: object) => void; navigate: (s: string, p?: object) => void }>();
  const [data, setData] = useState<PicksResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [prefs, setPrefs] = useState<Settings>({ min_ev: 0, sports: null, muted_books: null, sort: "ev" });
  const [showCooled, setShowCooled] = useState(false);
  const [betOnly, setBetOnly] = useState(false);
  const [unitStake, setUnitStake] = useState<number>(DEFAULT_UNIT_STAKE);
  const [sortOpen, setSortOpen] = useState(false);

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
      setUnitStake(await getUnitStake()); // app-local; safe without creds
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

  const selectSort = useCallback((k: SortKey) => {
    setSortOpen(false);
    void applyFilters({ sort: k });
  }, [applyFilters]);

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
        <View style={s.actionRow}>
          <Pressable testID="scan-capture-library" onPress={() => void onCapture("library")} style={s.scanChip}>
            <Feather name="camera" size={17} color={color.accentText} />
          </Pressable>
          <Pressable onPress={() => setBetOnly((v) => !v)} style={[s.feChip, betOnly && s.feChipOn]}>
            <Feather name="zap" size={13} color={betOnly ? color.onAccent : color.accentText} />
            <Text style={[s.feText, betOnly && s.feTextOn]}>BET만{betCount ? ` · ${betCount}` : ""}</Text>
          </Pressable>
        </View>
        <View style={s.filterRow}>
          <Text style={s.filterLabel}>EV</Text>
          {FEED_EV_STEPS.map((v) => (
            <Pressable key={v} onPress={() => void applyFilters({ min_ev: v })} style={[s.chip, prefs.min_ev === v && s.chipOn]}>
              <Text style={[s.chipText, prefs.min_ev === v && s.chipTextOn]}>{v === 0 ? "전체" : `${(v * 100).toFixed(0)}%+`}</Text>
            </Pressable>
          ))}
          <View style={s.divider} />
          <Pressable testID="sort-dropdown" onPress={() => setSortOpen(true)} style={s.sortChip}>
            <Text style={s.sortText}>{SORT_LABEL[prefs.sort]}</Text>
            <Feather name="chevron-down" size={14} color={color.muted} />
          </Pressable>
        </View>
      </View>
      <Modal visible={sortOpen} transparent animationType="fade" onRequestClose={() => setSortOpen(false)}>
        <Pressable style={s.modalBackdrop} onPress={() => setSortOpen(false)}>
          <View style={s.sortMenu}>
            <Text style={s.sortMenuTitle}>정렬</Text>
            {SORT_ORDER.map((k) => (
              <Pressable key={k} testID={`sort-opt-${k}`} onPress={() => selectSort(k)} style={[s.sortOpt, prefs.sort === k && s.sortOptOn]}>
                <Text style={[s.sortOptText, prefs.sort === k && s.sortOptTextOn]}>{SORT_LABEL[k]}</Text>
                {prefs.sort === k && <Feather name="check" size={16} color={color.accentText} />}
              </Pressable>
            ))}
          </View>
        </Pressable>
      </Modal>
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
            unitStake={unitStake}
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
                  <PickCard key={p.id} pick={p} unitStake={unitStake} onOpen={(x) => nav.push("PickDetail", { pickId: x.id })} onBet={(x) => nav.push("PickDetail", { pickId: x.id })} onDismiss={(x) => void dismiss(x)} />
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
  filterBar: { padding: 10, gap: 9 },
  actionRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  filterRow: { flexDirection: "row", alignItems: "center", flexWrap: "wrap", gap: 8 },
  scanChip: { backgroundColor: color.surfaceRaised, width: 34, height: 30, borderRadius: radius.sm, alignItems: "center", justifyContent: "center" },
  feChip: { flexDirection: "row", alignItems: "center", gap: 5, paddingHorizontal: 12, paddingVertical: 6, borderRadius: radius.pill, backgroundColor: color.accentDim },
  feChipOn: { backgroundColor: color.accent },
  feText: { color: color.accentText, fontSize: 13, fontWeight: "500" },
  feTextOn: { color: color.onAccent },
  filterLabel: { color: color.faint, fontSize: 11, letterSpacing: 0.4 },
  chip: { paddingHorizontal: 11, paddingVertical: 4, borderRadius: radius.pill, backgroundColor: color.surfaceRaised },
  chipOn: { backgroundColor: color.accent },
  chipText: { color: color.muted, fontSize: 12 },
  chipTextOn: { color: color.onAccent },
  divider: { width: 1, height: 16, backgroundColor: color.border, marginHorizontal: 2 },
  sortChip: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 10, paddingVertical: 4, borderRadius: radius.pill, backgroundColor: color.surfaceRaised },
  sortText: { color: color.muted, fontSize: 12 },
  modalBackdrop: { flex: 1, backgroundColor: "#0009", alignItems: "center", justifyContent: "center", padding: space.xl },
  sortMenu: { backgroundColor: color.surfaceRaised, borderRadius: radius.card, paddingVertical: space.sm, minWidth: 200, borderWidth: 1, borderColor: color.border },
  sortMenuTitle: { color: color.faint, fontSize: 11, letterSpacing: 0.4, paddingHorizontal: space.lg, paddingTop: space.sm, paddingBottom: space.xs },
  sortOpt: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: space.lg, paddingVertical: 12 },
  sortOptOn: { backgroundColor: color.accentDim },
  sortOptText: { color: color.text, fontSize: 15 },
  sortOptTextOn: { color: color.accentText, fontWeight: "600" },
  center: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: color.bg, padding: space.xl },
  err: { color: color.negative, textAlign: "center", marginBottom: space.md },
  retry: { backgroundColor: color.accent, paddingHorizontal: space.lg, paddingVertical: 10, borderRadius: radius.sm },
  retryText: { color: color.onAccent, fontWeight: "600" },
  empty: { color: color.faint, textAlign: "center", marginTop: 40 },
  cooledHeader: { padding: space.lg },
  cooledText: { color: color.muted, fontWeight: "600" },
});
