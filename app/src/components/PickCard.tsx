// app/src/components/PickCard.tsx
import { Pressable, StyleSheet, Text, View } from "react-native";
import { Swipeable } from "react-native-gesture-handler";
import type { Pick } from "../api/types";
import { color, radius, space, font } from "../theme";
import { countdown, countdownUrgent, decimalOdds, evPct } from "../format";

export function PickCard({
  pick,
  onOpen,
  onBet,
  onDismiss,
  unitStake,
}: {
  pick: Pick;
  onOpen: (p: Pick) => void;
  onBet: (p: Pick) => void;
  onDismiss: (p: Pick) => void;
  unitStake?: number; // flat recommended stake ($); shown on BET picks only
}) {
  // "얼마 베팅" — the policy is a flat unit on the picks we actually bet (first-entry
  // games). SKIP picks intentionally show no amount (not recommended).
  const betLabel = pick.should_bet && unitStake ? `베팅 $${unitStake}` : "베팅";
  // Swipe-left reveals 관심없음; same onDismiss as the inline button (kept as fallback).
  const renderRightActions = () => (
    <Pressable testID="swipe-dismiss" onPress={() => onDismiss(pick)} style={s.swipeAction}>
      <Text style={s.swipeText}>관심없음</Text>
    </Pressable>
  );

  return (
    <Swipeable renderRightActions={renderRightActions}>
    <Pressable style={[s.card, pick.should_bet === false && s.cardMuted]} onPress={() => onOpen(pick)}>
      <View style={s.head}>
        {pick.should_bet !== undefined && (
          <Text style={pick.should_bet ? s.betBadge : s.skipBadge}>
            {pick.should_bet ? "🟣 BET · first-entry game" : "⚪ SKIP · 권장 스킵"}
          </Text>
        )}
        <Text style={s.evPill}>{evPct(pick.ev)}</Text>
      </View>
      <Text style={s.label} numberOfLines={2}>{pick.label}</Text>
      <View style={s.meta}>
        <Text style={s.metaBook}>{pick.book ?? "—"} {decimalOdds(pick.odds)}</Text>
        <Text style={s.metaDim}>{pick.n_books ?? 0}북</Text>
        <Text style={[s.metaDim, countdownUrgent(pick.commence_time) && s.metaUrgent]}>{countdown(pick.commence_time)}</Text>
      </View>
      <View style={s.actions}>
        <Pressable testID="bet-button" style={[s.btn, s.bet]} onPress={() => onBet(pick)}><Text style={s.betText}>{betLabel}</Text></Pressable>
        <Pressable testID="inline-dismiss" style={[s.btn, s.dismiss]} onPress={() => onDismiss(pick)}><Text style={s.dismissText}>관심없음</Text></Pressable>
      </View>
    </Pressable>
    </Swipeable>
  );
}

const s = StyleSheet.create({
  card: { backgroundColor: color.surface, borderRadius: radius.card, padding: space.lg, marginVertical: 6, marginHorizontal: space.md },
  cardMuted: { opacity: 0.5 },
  head: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  betBadge: { color: color.accentText, backgroundColor: color.accentDim, fontSize: 12, fontWeight: "500", paddingHorizontal: 8, paddingVertical: 3, borderRadius: radius.sm, overflow: "hidden" },
  skipBadge: { color: color.muted, backgroundColor: color.surfaceRaised, fontSize: 12, fontWeight: "500", paddingHorizontal: 8, paddingVertical: 3, borderRadius: radius.sm, overflow: "hidden" },
  evPill: { color: color.positive, backgroundColor: color.positiveDim, fontSize: 17, fontWeight: "600", paddingHorizontal: 11, paddingVertical: 3, borderRadius: radius.sm, overflow: "hidden" },
  label: { ...font.pickLabel, marginTop: space.md },
  meta: { flexDirection: "row", alignItems: "center", gap: space.md, marginTop: 9 },
  metaBook: { color: color.text, fontSize: 13, fontWeight: "500" },
  metaDim: { color: color.muted, fontSize: 13 },
  metaUrgent: { color: color.urgent },
  actions: { flexDirection: "row", marginTop: space.lg, gap: space.sm },
  btn: { paddingVertical: 11, borderRadius: radius.button, alignItems: "center" },
  bet: { flex: 3, backgroundColor: color.accent },
  dismiss: { flex: 1, backgroundColor: color.surfaceRaised },
  betText: { color: color.onAccent, fontWeight: "600", fontSize: 15 },
  dismissText: { color: color.muted, fontWeight: "500" },
  swipeAction: { backgroundColor: color.negative, justifyContent: "center", paddingHorizontal: 24, marginVertical: 6, marginRight: space.md, borderRadius: radius.card },
  swipeText: { color: color.onAccent, fontWeight: "600" },
});
