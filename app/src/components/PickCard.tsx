// app/src/components/PickCard.tsx
import { Pressable, StyleSheet, Text, View } from "react-native";
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
  return (
    <Pressable style={s.card} onPress={() => onOpen(pick)}>
      <View style={s.row}>
        <Text style={s.label} numberOfLines={2}>{pick.label}</Text>
        <Text style={s.ev}>{evPct(pick.ev)}</Text>
      </View>
      <Text style={s.meta}>
        {pick.book ?? "—"} @ {decimalOdds(pick.odds)} · {pick.n_books ?? 0}북 · {countdown(pick.commence_time)}
      </Text>
      <View style={s.actions}>
        <Pressable style={[s.btn, s.bet]} onPress={() => onBet(pick)}><Text style={s.btnText}>베팅</Text></Pressable>
        <Pressable style={[s.btn, s.dismiss]} onPress={() => onDismiss(pick)}><Text style={s.btnText}>관심없음</Text></Pressable>
      </View>
    </Pressable>
  );
}

const s = StyleSheet.create({
  card: { backgroundColor: "#1A1A22", borderRadius: 12, padding: 14, marginVertical: 6, marginHorizontal: 12 },
  row: { flexDirection: "row", justifyContent: "space-between" },
  label: { color: "#FFF", fontSize: 16, fontWeight: "600", flex: 1, paddingRight: 8 },
  ev: { color: "#34D399", fontSize: 16, fontWeight: "700" },
  meta: { color: "#9CA3AF", marginTop: 6, fontSize: 13 },
  actions: { flexDirection: "row", marginTop: 10, gap: 8 },
  btn: { flex: 1, paddingVertical: 8, borderRadius: 8, alignItems: "center" },
  bet: { backgroundColor: "#7C3AED" },
  dismiss: { backgroundColor: "#374151" },
  btnText: { color: "#FFF", fontWeight: "600" },
});
