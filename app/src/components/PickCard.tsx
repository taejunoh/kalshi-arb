// app/src/components/PickCard.tsx
import { Pressable, StyleSheet, Text, View } from "react-native";
import { Swipeable } from "react-native-gesture-handler";
import type { Pick } from "../api/types";
import { color, radius, space, font } from "../theme";
import { countdown, countdownUrgent, americanOdds, evPct, matchup } from "../format";

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
  // Under forward review (core.is_ev_watch): still a BET verdict, but we ask to hold.
  // Only meaningful on a BET — a SKIP is already not recommended.
  const watch = pick.should_bet === true && pick.ev_watch === true;
  // "얼마 베팅" — the policy is a flat unit on the picks we actually bet (first-entry
  // games). SKIP picks intentionally show no amount (not recommended), and neither do
  // watched picks: "$20" next to "베팅 보류" would contradict itself (same reason the
  // Telegram tag drops its 💵 segment).
  const betLabel = pick.should_bet && !watch && unitStake ? `베팅 $${unitStake}` : "베팅";
  // The bet label alone ("Over 167.5") doesn't say which game it is — show the
  // matchup so a totals/spread pick is identifiable at a glance.
  const game = matchup(pick.away, pick.home);
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
          <Text style={watch ? s.watchBadge : pick.should_bet ? s.betBadge : s.skipBadge}>
            {watch ? "🟡 관찰 · 베팅 보류"
              : pick.should_bet ? "🟣 BET · first-entry game" : "⚪ SKIP · 권장 스킵"}
          </Text>
        )}
        <Text style={s.evPill}>{evPct(pick.ev)}</Text>
      </View>
      {game && <Text style={s.matchup} numberOfLines={1}>{game}</Text>}
      <Text style={[s.label, game && s.labelTight]} numberOfLines={2}>{pick.label}</Text>
      {watch && <Text style={s.watchNote}>ML EV≥5% — 페이퍼 4승 21패 · 전진관찰 중</Text>}
      <View style={s.meta}>
        <Text style={s.metaBook}>{pick.book ?? "—"} {americanOdds(pick.odds)}</Text>
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
  // amber = "still a BET, but hold" — deliberately NOT the muted SKIP grey (it is not
  // a skip) and not the accent purple (it is not a plain bet).
  watchBadge: { color: color.urgent, backgroundColor: color.urgentDim, fontSize: 12, fontWeight: "500", paddingHorizontal: 8, paddingVertical: 3, borderRadius: radius.sm, overflow: "hidden" },
  watchNote: { color: color.urgent, fontSize: 12, marginTop: space.xs },
  evPill: { color: color.positive, backgroundColor: color.positiveDim, fontSize: 17, fontWeight: "600", paddingHorizontal: 11, paddingVertical: 3, borderRadius: radius.sm, overflow: "hidden" },
  matchup: { color: color.muted, fontSize: 13, fontWeight: "500", marginTop: space.md },
  label: { ...font.pickLabel, marginTop: space.md },
  labelTight: { marginTop: 1 },
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
