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

/** "8월 12일" from a YYYY-MM-DD day key.
 *
 *  Takes the DAY, never a raw settled_at. settled_at is a UTC instant, so
 *  slicing it names the following day for anything settling after 8pm ET —
 *  which is most evening games, and 39% of settled bets in production. The day
 *  is computed once on the server (see AppBet.settled_day) and only formatted
 *  here. */
const MONTH_DAY = (day: string): string => {
  const [, m, d] = day.split("-");
  return `${Number(m)}월 ${Number(d)}일`;
};

/** Does toggling this bet move a number that is already on the books? Only a
 *  settled row with a recorded PnL has one. Both the dialog TEXT and the
 *  decision to show a dialog at all read this single predicate, so a confirm
 *  prompt can never appear with no amount in it to disclose.
 *
 *  Gated on settled_day, not settled_at: the dialog's whole job is to NAME the
 *  day that moves, so a row we cannot name a day for has nothing to disclose
 *  either. That also makes the deploy-skew case (a row predating settled_day)
 *  degrade to the no-amount dialog instead of naming a UTC day.
 *
 *  A type predicate so toggleMessage still gets pnl/settled_day narrowed to
 *  non-null after its early return. */
function movesSettledPnl(bet: AppBet): bet is AppBet & { pnl: number; settled_day: string } {
  return !!bet.settled && bet.pnl != null && !!bet.settled_day;
}

/** "{month}월 {day}일 PnL이 {+/-N.NN}" — the day+amount fragment named in every
 *  surface that discloses a PnL change, so the wording used before an action
 *  ("about to change") and after one (unmarkedElsewhereMessage, below,
 *  "just changed") cannot drift apart. */
function pnlChangeFragment(day: string, delta: number): string {
  const signed = `${delta >= 0 ? "+" : "-"}${Math.abs(delta).toFixed(2)}`;
  return `${MONTH_DAY(day)} PnL이 ${signed}`;
}

/** The confirm text for BOTH directions of the 베팅 안 했음 toggle. A settled
 *  bet's PnL moves either way — out of that day on unmark, back into it on
 *  restore — so the dialog names the day and the amount: a past number must
 *  never shift in silence. One builder on purpose, so the two dialogs cannot
 *  drift apart; only the sign of the delta and the trailing sentence differ. */
function toggleMessage(bet: AppBet, sign: 1 | -1, tail: string): string {
  if (!movesSettledPnl(bet)) return tail;
  return `${pnlChangeFragment(bet.settled_day, sign * bet.pnl)} 변경됩니다. ${tail}`;
}

/** 베팅 안 했음: the bet leaves the totals, the pick returns to the feed. */
export const unmarkMessage = (bet: AppBet): string => toggleMessage(bet, -1,
  "기록은 남고 집계에서만 빠집니다. 이 픽은 다시 피드에 나타납니다.");

/** 되돌리기: the exact inverse, and just as much of a change to a past day. */
export const remarkMessage = (bet: AppBet): string => toggleMessage(bet, 1,
  "이 베팅이 다시 집계에 포함됩니다. 이 픽은 피드에서 다시 숨겨집니다.");

/** Post-hoc disclosure for unmark ONLY. unmarkMessage(bet) above is built from
 *  the navigation-param snapshot taken when the list loaded; if the
 *  settlement worker settles this bet while the sheet is open,
 *  movesSettledPnl(snapshot) still reads false and that confirm dialog names
 *  no amount. The server's response is the only place the truth is available
 *  at that point (POST /bets/{id}/unmark's pnl_delta/settled_day), so the
 *  caller shows this after the fact rather than dropping a real past-day PnL
 *  change in silence. Same pnlChangeFragment as the confirm dialogs, just past
 *  tense — the two paths cannot say different things about the same change.
 *
 *  Scoped to unmark only: 되돌리기 (remark) can never hit this gap, because the
 *  settlement worker only ever touches rows with not_placed=0 — a disowned
 *  row's settled/pnl cannot go stale out from under a 되돌리기 confirm. */
export function unmarkedElsewhereMessage(day: string, delta: number): string {
  return `${pnlChangeFragment(day, delta)} 변경되었습니다.`;
}

export function BetEditSheet({ route }: Props) {
  const nav = useNavigation<{ popToTop: () => void; goBack: () => void }>();
  const bet = route.params.bet;
  const [book, setBook] = useState(bet.book ?? "");
  const [stake, setStake] = useState(bet.stake != null ? String(bet.stake) : "");
  // Kept so save() can tell "the user retyped the odds" from "the odds field was
  // merely rendered". See the omit rule in save().
  const initialOdds = bet.fill_odds != null ? americanOdds(bet.fill_odds) : "";
  const [odds, setOdds] = useState(initialOdds);
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
      // fill_odds goes in ONLY when the text changed. decimal -> American -> decimal
      // is lossy for every price under 2.0 — i.e. every favorite: americanOdds(1.91)
      // is "-110" and parsing that back gives 1.9090909090909092 (2.375 -> "+138" ->
      // 2.38). Re-parsing an untouched field would reprice a real-money ledger row
      // to a number the book never showed, on a save the user made to fix the book
      // name. app_bet rows are never repriced (LEDGER RULE in softbook_app_db).
      // stake needs no such guard: String(20) -> Number("20") is exact.
      if (odds !== initialOdds) {
        const parsedOdds = parseOddsToDecimal(odds);
        if (parsedOdds != null) patch.fill_odds = parsedOdds;
      }
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
            const res = await api.unmarkBet(bet.id);
            // The confirm dialog just shown named no amount (movesSettledPnl
            // read false on the snapshot). If the server reports a real
            // pnl_delta anyway, the snapshot was stale — disclose it now.
            if (res.pnl_delta != null && res.settled_day != null && !movesSettledPnl(bet)) {
              Alert.alert(unmarkedElsewhereMessage(res.settled_day, res.pnl_delta));
            }
            nav.popToTop();
          } catch (e) {
            setError((e as Error).message);
            setBusy(false);
          }
        })(),
      },
    ]);
  };

  const doRemark = async () => {
    setBusy(true);
    try {
      await api.remarkBet(bet.id);
      nav.popToTop();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  // Confirmed ONLY when a past number actually moves. Restoring a settled bet
  // puts that day's PnL back, which must not be rewritten under the user in
  // silence — but an unsettled restore shifts nothing already on the books, and
  // it is the UNDO of the destructive direction, so a prompt there is friction
  // with nothing behind it. Same call the pick feed's 관심없음 makes with its
  // 5-second undo instead of a confirm. Unmark stays unconditional: it is the
  // destructive direction (drops the bet from PnL, re-exposes the pick).
  const remark = () => {
    if (!movesSettledPnl(bet)) {
      void doRemark();
      return;
    }
    Alert.alert("베팅 기록 되돌리기", remarkMessage(bet), [
      { text: "취소", style: "cancel" },
      { text: "되돌리기", onPress: () => void doRemark() },
    ]);
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
        <Pressable style={s.destructive} disabled={busy} onPress={remark}>
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
