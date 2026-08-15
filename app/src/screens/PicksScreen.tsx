// app/src/screens/PicksScreen.tsx
import { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, Alert, FlatList, Modal, Pressable, StyleSheet, Text, View } from "react-native";
import { useFocusEffect, useNavigation } from "@react-navigation/native";
import { api } from "../api/client";
import type { Pick, PicksResponse, Settings, SortKey } from "../api/types";
import { PickCard } from "../components/PickCard";
import { pickImage } from "../scan";
import { getUnitStake } from "../storage";
import { DEFAULT_UNIT_STAKE } from "../config";
import { matchup } from "../format";
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
  const [showExpired, setShowExpired] = useState(false);
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

  // 관심없음 sits right next to 베팅, so mis-taps happen. Undo beats a confirm
  // dialog: a real dismiss costs nothing extra, a mis-tap costs one tap back.
  const [undoPick, setUndoPick] = useState<Pick | null>(null);
  const undoTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const dismiss = useCallback(async (p: Pick) => {
    // Optimistic removal from EVERY list — the pick may live in cooled or
    // expired, not just the active feed (a cooled-pick dismiss used to sit
    // on screen until the next refetch, which read as the button not working).
    setData((d) => (d ? {
      ...d,
      picks: d.picks.filter((x) => x.id !== p.id),
      cooled: (d.cooled ?? []).filter((x) => x.id !== p.id),
      expired: (d.expired ?? []).filter((x) => x.id !== p.id),
    } : d));
    if (undoTimer.current) clearTimeout(undoTimer.current);
    setUndoPick(p);
    undoTimer.current = setTimeout(() => setUndoPick(null), 5000);
    try {
      await api.dismissPick(p.id);
    } catch {
      setUndoPick(null);
      void load(); // revert by reloading on failure
    }
  }, [load]);

  const undo = useCallback(async () => {
    if (undoTimer.current) clearTimeout(undoTimer.current);
    const p = undoPick;
    setUndoPick(null);
    if (!p) return;
    try {
      await api.undismissPick(p.id);
    } catch {
      // 404 = dismiss never landed server-side; either way the reload decides
    }
    void load();
  }, [undoPick, load]);

  if (error) {
    return (
      <View style={s.center}>
        <Text style={s.err}>로드 실패: {error}</Text>
        <Pressable style={s.retry} onPress={() => void load()}><Text style={s.retryText}>다시 시도</Text></Pressable>
      </View>
    );
  }

  // The API no longer serves SKIP picks, so there is nothing left to filter to —
  // the "BET만" chip this replaced would have been a no-op.
  const visiblePicks = data?.picks ?? [];

  // An empty feed used to say only "활성 픽 없음", which reads as "nothing was
  // found" when the usual cause is that the picks are accounted for. Twice on
  // 2026-08-05 a Telegram alert arrived and the pick was not in the app — both
  // times it was one the user had already marked bet, and the app gave no way to
  // see that. counts already came down with every response; it just was not shown.
  //
  // bet/dismissed only: cooled and expired have their own sections right below,
  // and naming them twice reads as two different things.
  const c = data?.counts ?? {};
  // conflict_hidden is server-side: picks dropped because the opposite side of
  // that game is already staked. Without a line here they just look missing.
  const whereTheyWent = ([["bet", "표시함"], ["dismissed", "무시함"],
                          ["conflict_hidden", "반대방향 제외"]] as const)
    .filter(([k]) => (c[k] ?? 0) > 0)
    .map(([k, label]) => `${label} ${c[k]}`)
    .join(" · ");

  // Collapsed sections under the active feed: 식은 픽 (cooled — gone from the
  // latest scan) and 지나간 픽 (expired — game started). Same card + handlers.
  // Open the book/deeplink detail, passing the matchup so the detail header shows
  // which game it is (the bet label alone doesn't say).
  const openDetail = (p: Pick) => nav.push("PickDetail", { pickId: p.id, matchup: matchup(p.away, p.home) });

  const collapsibleSection = (key: string, title: string, items: Pick[], open: boolean, toggle: () => void) =>
    items.length > 0 ? (
      <View key={key}>
        <Pressable style={s.cooledHeader} onPress={toggle}>
          <Text style={s.cooledText}>{title} {items.length} {open ? "▲" : "▼"}</Text>
        </Pressable>
        {open &&
          items.map((p) => (
            <PickCard key={p.id} pick={p} unitStake={unitStake} onOpen={openDetail} onBet={openDetail} onDismiss={(x) => void dismiss(x)} />
          ))}
      </View>
    ) : null;

  return (
    <View style={s.container}>
      <View style={s.filterBar}>
        <View style={s.actionRow}>
          <Pressable testID="scan-capture-library" onPress={() => void onCapture("library")} style={s.scanChip}>
            <Feather name="camera" size={17} color={color.accentText} />
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
            onOpen={openDetail}
            onBet={openDetail}
            onDismiss={(p) => void dismiss(p)}
          />
        )}
        ListEmptyComponent={
          loading ? <ActivityIndicator style={{ marginTop: 40 }} />
            : <View>
                <Text style={s.empty}>활성 픽 없음</Text>
                {whereTheyWent ? <Text style={s.emptyWhy}>{whereTheyWent}</Text> : null}
              </View>
        }
        ListFooterComponent={
          <>
            {collapsibleSection("cooled", "식은 픽", data?.cooled ?? [], showCooled, () => setShowCooled((v) => !v))}
            {collapsibleSection("expired", "지나간 픽", data?.expired ?? [], showExpired, () => setShowExpired((v) => !v))}
          </>
        }
      />
      {undoPick && (
        <View style={s.undoBar}>
          <Text style={s.undoText} numberOfLines={1}>무시함: {undoPick.label}</Text>
          <Pressable onPress={() => void undo()} hitSlop={10}>
            <Text style={s.undoAction}>실행취소</Text>
          </Pressable>
        </View>
      )}
    </View>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: color.bg },
  filterBar: { padding: 10, gap: 9 },
  actionRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  filterRow: { flexDirection: "row", alignItems: "center", flexWrap: "wrap", gap: 8 },
  scanChip: { backgroundColor: color.surfaceRaised, width: 34, height: 30, borderRadius: radius.sm, alignItems: "center", justifyContent: "center" },
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
  emptyWhy: { color: color.muted, fontSize: 12.5, textAlign: "center", marginTop: space.xs },
  undoBar: { position: "absolute", left: space.lg, right: space.lg, bottom: space.lg,
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    backgroundColor: color.surface, borderRadius: radius.card, paddingHorizontal: space.lg,
    paddingVertical: 12, borderWidth: 1, borderColor: color.border,
    shadowColor: "#000", shadowOpacity: 0.4, shadowRadius: 8, elevation: 6 },
  undoText: { color: color.text, fontSize: 13, flexShrink: 1, marginRight: space.md },
  undoAction: { color: color.accentText, fontSize: 13, fontWeight: "700" },
  empty: { color: color.faint, textAlign: "center", marginTop: 40 },
  cooledHeader: { padding: space.lg },
  cooledText: { color: color.muted, fontWeight: "600" },
});
