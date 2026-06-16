// app/src/components/PickCard.tsx
import { Pressable, StyleSheet, Text, View } from "react-native";
import { Swipeable } from "react-native-gesture-handler";
import type { Pick } from "../api/types";
import { decimalOdds, evPct, countdown } from "../format";

export function PickCard({
  pick,
  onOpen,
  onBet,
  onDismiss,
}: {
  pick: Pick;
  onOpen: (p: Pick) => void;
  onBet: (p: Pick) => void;
  onDismiss: (p: Pick) => void;
}) {
  // Swipe-left reveals 관심없음; same onDismiss as the inline button (kept as fallback).
  const renderRightActions = () => (
    <Pressable testID="swipe-dismiss" onPress={() => onDismiss(pick)} style={s.swipeAction}>
      <Text style={s.swipeText}>관심없음</Text>
    </Pressable>
  );

  return (
    <Swipeable renderRightActions={renderRightActions}>
    <Pressable style={[s.card, pick.should_bet === false && s.cardMuted]} onPress={() => onOpen(pick)}>
      {pick.should_bet !== undefined && (
        <Text style={pick.should_bet ? s.feBadge : s.lcBadge}>
          {pick.should_bet ? "🟣 BET · first-entry game" : "⚪ SKIP · 권장 스킵"}
        </Text>
      )}
      <View style={s.row}>
        <Text style={s.label} numberOfLines={2}>{pick.label}</Text>
        <Text style={s.ev}>{evPct(pick.ev)}</Text>
      </View>
      <Text style={s.meta}>
        {pick.book ?? "—"} @ {decimalOdds(pick.odds)} · {pick.n_books ?? 0}북 · {countdown(pick.commence_time)}
      </Text>
      <View style={s.actions}>
        <Pressable style={[s.btn, s.bet]} onPress={() => onBet(pick)}><Text style={s.btnText}>베팅</Text></Pressable>
        <Pressable testID="inline-dismiss" style={[s.btn, s.dismiss]} onPress={() => onDismiss(pick)}><Text style={s.btnText}>관심없음</Text></Pressable>
      </View>
    </Pressable>
    </Swipeable>
  );
}

const s = StyleSheet.create({
  card: { backgroundColor: "#1A1A22", borderRadius: 12, padding: 14, marginVertical: 6, marginHorizontal: 12 },
  cardMuted: { opacity: 0.55 },
  feBadge: { alignSelf: "flex-start", backgroundColor: "#7C3AED", color: "#FFF", fontSize: 12, fontWeight: "700", paddingHorizontal: 8, paddingVertical: 3, borderRadius: 8, marginBottom: 8, overflow: "hidden" },
  lcBadge: { alignSelf: "flex-start", backgroundColor: "#374151", color: "#9CA3AF", fontSize: 12, fontWeight: "600", paddingHorizontal: 8, paddingVertical: 3, borderRadius: 8, marginBottom: 8, overflow: "hidden" },
  row: { flexDirection: "row", justifyContent: "space-between" },
  label: { color: "#FFF", fontSize: 16, fontWeight: "600", flex: 1, paddingRight: 8 },
  ev: { color: "#34D399", fontSize: 16, fontWeight: "700" },
  meta: { color: "#9CA3AF", marginTop: 6, fontSize: 13 },
  actions: { flexDirection: "row", marginTop: 10, gap: 8 },
  btn: { flex: 1, paddingVertical: 8, borderRadius: 8, alignItems: "center" },
  bet: { backgroundColor: "#7C3AED" },
  dismiss: { backgroundColor: "#374151" },
  btnText: { color: "#FFF", fontWeight: "600" },
  swipeAction: { backgroundColor: "#DC2626", justifyContent: "center", paddingHorizontal: 24, marginVertical: 6, marginRight: 12, borderRadius: 12 },
  swipeText: { color: "#FFF", fontWeight: "600" },
});
