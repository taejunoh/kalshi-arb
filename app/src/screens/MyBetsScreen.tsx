// app/src/screens/MyBetsScreen.tsx
import { useCallback, useState } from "react";
import { ActivityIndicator, Pressable, SectionList, StyleSheet, Text, View } from "react-native";
import { useFocusEffect, useNavigation } from "@react-navigation/native";
import { api } from "../api/client";
import type { AppBet, MyBetsResponse } from "../api/types";
import { americanOdds, matchup } from "../format";
import { color, radius, space } from "../theme";

type Section = { title: string; data: AppBet[] };

/** The month key the calendar uses, in the device's local date. */
function monthPrefix(now = new Date()): string {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

export function monthPnl(calendar: Record<string, number>, prefix = monthPrefix()): number {
  return Object.entries(calendar)
    .filter(([day]) => day.startsWith(prefix))
    .reduce((sum, [, pnl]) => sum + pnl, 0);
}

export function sections(d: MyBetsResponse | null): Section[] {
  if (!d) return [];
  // Empty sections are dropped rather than rendered with a 0 — a header that
  // says "정산됨 0" reads as a state, not as absence.
  return ([
    ["미정산", d.open],
    ["정산됨", d.settled],
    ["베팅 안 함", d.not_placed],
  ] as const)
    .filter(([, rows]) => rows.length > 0)
    .map(([title, rows]) => ({ title: `${title} ${rows.length}`, data: rows }));
}

function signed(n: number): string {
  return `${n >= 0 ? "+" : "-"}${Math.abs(n).toFixed(2)}`;
}

function BetRow({ bet, onPress }: { bet: AppBet; onPress: (b: AppBet) => void }) {
  const off = bet.not_placed === 1;
  const meta = [
    bet.book,
    bet.fill_odds != null ? americanOdds(bet.fill_odds) : null,
    bet.stake != null ? `$${bet.stake}` : null,
  ].filter(Boolean).join(" · ");
  return (
    <Pressable style={s.row} onPress={() => onPress(bet)}>
      <View style={s.rowMain}>
        <Text style={[s.label, off && s.offLabel]}>{bet.label ?? "—"}</Text>
        <Text style={s.matchup}>{matchup(bet.away, bet.home) ?? ""}</Text>
        <Text style={s.meta}>{meta}</Text>
      </View>
      {bet.settled === 1 && bet.pnl != null && (
        <Text style={[s.pnl, bet.pnl >= 0 ? s.pnlUp : s.pnlDown]}>{signed(bet.pnl)}</Text>
      )}
    </Pressable>
  );
}

export function MyBetsScreen() {
  const nav = useNavigation<{ push: (s: string, p?: object) => void }>();
  const [data, setData] = useState<MyBetsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.getMyBets());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { void load(); }, [load]));

  const openEdit = useCallback((bet: AppBet) => nav.push("BetEdit", { bet }), [nav]);

  if (error) {
    return (
      <View style={s.center}>
        <Text style={s.err}>로드 실패: {error}</Text>
        <Pressable style={s.retry} onPress={() => void load()}>
          <Text style={s.retryText}>다시 시도</Text>
        </Pressable>
      </View>
    );
  }

  const month = data ? monthPnl(data.calendar) : 0;

  return (
    <View style={s.container}>
      <View style={s.summary}>
        <Text style={s.summaryLabel}>이번 달</Text>
        <Text style={[s.summaryValue, month >= 0 ? s.pnlUp : s.pnlDown]}>{signed(month)}</Text>
      </View>
      <SectionList
        sections={sections(data)}
        keyExtractor={(b) => String(b.id)}
        refreshing={loading}
        onRefresh={() => void load()}
        renderSectionHeader={({ section }) => (
          <Text style={s.sectionHeader}>{section.title}</Text>
        )}
        renderItem={({ item }) => <BetRow bet={item} onPress={openEdit} />}
        ListEmptyComponent={
          loading ? <ActivityIndicator style={{ marginTop: 40 }} />
            : <Text style={s.empty}>기록된 베팅 없음</Text>
        }
      />
    </View>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: color.bg },
  summary: { flexDirection: "row", alignItems: "baseline", justifyContent: "space-between",
    paddingHorizontal: space.lg, paddingVertical: space.md },
  summaryLabel: { color: color.faint, fontSize: 11, letterSpacing: 0.4 },
  summaryValue: { fontSize: 19, fontWeight: "700" },
  sectionHeader: { color: color.muted, fontWeight: "600", fontSize: 12.5,
    paddingHorizontal: space.lg, paddingTop: space.lg, paddingBottom: space.xs,
    backgroundColor: color.bg },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    backgroundColor: color.surface, borderRadius: radius.card, padding: space.md,
    marginHorizontal: space.md, marginTop: space.sm },
  rowMain: { flexShrink: 1, gap: 2 },
  label: { color: color.text, fontSize: 15, fontWeight: "600" },
  offLabel: { color: color.faint, textDecorationLine: "line-through" },
  matchup: { color: color.muted, fontSize: 12.5 },
  meta: { color: color.faint, fontSize: 12 },
  pnl: { fontSize: 15, fontWeight: "700", marginLeft: space.md },
  pnlUp: { color: color.positive },
  pnlDown: { color: color.negative },
  center: { flex: 1, alignItems: "center", justifyContent: "center",
    backgroundColor: color.bg, padding: space.xl },
  err: { color: color.negative, textAlign: "center", marginBottom: space.md },
  retry: { backgroundColor: color.accent, paddingHorizontal: space.lg,
    paddingVertical: 10, borderRadius: radius.sm },
  retryText: { color: color.onAccent, fontWeight: "600" },
  empty: { color: color.faint, textAlign: "center", marginTop: 40 },
});
