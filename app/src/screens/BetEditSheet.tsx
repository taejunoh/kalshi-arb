// app/src/screens/BetEditSheet.tsx
import { useState } from "react";
import { Alert, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { useNavigation } from "@react-navigation/native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import { api } from "../api/client";
import type { AppBet, BetPatch } from "../api/types";
import type { MyBetsStackParamList } from "../navigation/types";
import { americanOdds, matchup, parseOddsToDecimal } from "../format";
import { color, radius, space } from "../theme";

type Props = { route: NativeStackScreenProps<MyBetsStackParamList, "BetEdit">["route"] };

const MONTH_DAY = (iso: string): string => {
  const [, m, d] = iso.slice(0, 10).split("-");
  return `${Number(m)}월 ${Number(d)}일`;
};

/** The confirm text. A settled bet's PnL moves, so the dialog names the day and
 *  the amount — a past number must never shift in silence. */
export function unmarkMessage(bet: AppBet): string {
  const tail = "기록은 남고 집계에서만 빠집니다. 이 픽은 다시 피드에 나타납니다.";
  if (!bet.settled || bet.pnl == null || !bet.settled_at) return tail;
  const delta = -bet.pnl;
  const signed = `${delta >= 0 ? "+" : "-"}${Math.abs(delta).toFixed(2)}`;
  return `${MONTH_DAY(bet.settled_at)} PnL이 ${signed} 변경됩니다. ${tail}`;
}

export function BetEditSheet({ route }: Props) {
  const nav = useNavigation<{ popToTop: () => void; goBack: () => void }>();
  const bet = route.params.bet;
  const [book, setBook] = useState(bet.book ?? "");
  const [stake, setStake] = useState(bet.stake != null ? String(bet.stake) : "");
  const [odds, setOdds] = useState(bet.fill_odds != null ? americanOdds(bet.fill_odds) : "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const editable = bet.settled === 0 && bet.not_placed === 0;

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      // stake / fill_odds cannot be nulled server-side (422): a placed bet
      // without them is not a record of anything, and the settle worker
      // multiplies by both. So an emptied field is OMITTED, not sent as null.
      // book may be cleared — it is descriptive metadata.
      const patch: BetPatch = { book: book.trim() || null };
      // Number("abc") is NaN, and JSON.stringify serializes NaN as null — the
      // one payload this must never send. Parse first, include only if finite.
      const parsedStake = Number(stake);
      if (stake && Number.isFinite(parsedStake)) patch.stake = parsedStake;
      const parsedOdds = parseOddsToDecimal(odds);
      if (parsedOdds != null) patch.fill_odds = parsedOdds;
      await api.patchBet(bet.id, patch);
      nav.popToTop();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  // No optimistic update and no 5-second undo bar here: this action is already
  // gated by a confirm dialog, so waiting for the server is the honest thing.
  const unmark = () => {
    Alert.alert("베팅 안 했음으로 처리", unmarkMessage(bet), [
      { text: "취소", style: "cancel" },
      {
        text: "처리", style: "destructive",
        onPress: () => void (async () => {
          setBusy(true);
          try {
            await api.unmarkBet(bet.id);
            nav.popToTop();
          } catch (e) {
            setError((e as Error).message);
            setBusy(false);
          }
        })(),
      },
    ]);
  };

  const remark = async () => {
    setBusy(true);
    try {
      await api.remarkBet(bet.id);
      nav.popToTop();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <View style={s.container}>
      <Text style={s.label}>{bet.label ?? "—"}</Text>
      <Text style={s.matchup}>{matchup(bet.away, bet.home) ?? ""}</Text>
      <Text style={s.immutable}>경기·마켓은 수정할 수 없습니다</Text>

      {editable ? (
        <>
          <Text style={s.field}>북</Text>
          <TextInput testID="edit-book" style={s.input} value={book} onChangeText={setBook}
            placeholder="북" placeholderTextColor={color.faint} autoCapitalize="none" />
          <Text style={s.field}>스테이크</Text>
          <TextInput testID="edit-stake" style={s.input} value={stake} onChangeText={setStake}
            placeholder="스테이크 ($)" placeholderTextColor={color.faint} keyboardType="decimal-pad" />
          <Text style={s.field}>실제 체결 배당</Text>
          <TextInput testID="edit-odds" style={s.input} value={odds} onChangeText={setOdds}
            placeholder="실배당 (예: -110)" placeholderTextColor={color.faint}
            keyboardType="numbers-and-punctuation" />
        </>
      ) : (
        <Text style={s.locked}>
          {bet.not_placed === 1 ? "베팅 안 함으로 처리된 기록입니다"
            : "정산된 베팅은 수정할 수 없습니다"}
        </Text>
      )}

      {error && <Text style={s.err}>{error}</Text>}

      <View style={s.actions}>
        <Pressable style={[s.btn, s.cancel]} onPress={() => nav.goBack()}>
          <Text style={s.btnText}>닫기</Text>
        </Pressable>
        {editable && (
          <Pressable style={[s.btn, s.save]} disabled={busy} onPress={() => void save()}>
            <Text style={s.btnText}>저장</Text>
          </Pressable>
        )}
      </View>

      {bet.not_placed === 1 ? (
        <Pressable style={s.destructive} disabled={busy} onPress={() => void remark()}>
          <Text style={s.destructiveText}>되돌리기</Text>
        </Pressable>
      ) : (
        <Pressable style={s.destructive} disabled={busy} onPress={unmark}>
          <Text style={s.destructiveText}>베팅 안 했음</Text>
        </Pressable>
      )}
    </View>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: color.bg, padding: space.xl },
  label: { color: color.text, fontSize: 18, fontWeight: "600" },
  matchup: { color: color.muted, fontSize: 13, marginTop: 2 },
  immutable: { color: color.faint, fontSize: 11.5, marginTop: space.xs },
  locked: { color: color.urgent, fontSize: 13, marginTop: space.lg },
  field: { color: color.faint, fontSize: 11, letterSpacing: 0.4, marginTop: space.lg },
  input: { backgroundColor: color.surfaceRaised, color: color.text, borderRadius: radius.sm,
    paddingHorizontal: space.md, paddingVertical: 10, marginTop: space.xs, fontSize: 15 },
  err: { color: color.negative, marginTop: space.md },
  actions: { flexDirection: "row", gap: space.md, marginTop: space.xl },
  btn: { flex: 1, paddingVertical: 12, borderRadius: radius.button, alignItems: "center" },
  cancel: { backgroundColor: color.surfaceRaised },
  save: { backgroundColor: color.accent },
  btnText: { color: color.text, fontWeight: "600" },
  destructive: { marginTop: space.xl, paddingVertical: 12, alignItems: "center",
    borderRadius: radius.button, borderWidth: 1, borderColor: color.negativeStrong },
  destructiveText: { color: color.negative, fontWeight: "600" },
});
