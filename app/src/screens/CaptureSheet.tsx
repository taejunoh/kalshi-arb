// app/src/screens/CaptureSheet.tsx
import { useEffect, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { useNavigation } from "@react-navigation/native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import { api } from "../api/client";
import type { Pick } from "../api/types";
import type { PicksStackParamList } from "../navigation/types";
import { buildBetIn } from "../capture";
import { draftToBetIn } from "../scan";
import { decimalOdds } from "../format";
import { color, radius, space } from "../theme";

// Indexed access (not the `Pick<>` utility — `Pick` is imported above as the API type).
type Props = { route: NativeStackScreenProps<PicksStackParamList, "CaptureSheet">["route"] };

export function CaptureSheet({ route }: Props) {
  const nav = useNavigation<{ popToTop: () => void; goBack: () => void }>();
  const scanDraft = route.params?.scanDraft;
  const pickId = route.params?.pickId;
  const [pick, setPick] = useState<Pick | null>(null);
  const [stake, setStake] = useState(scanDraft?.stake != null ? String(scanDraft.stake) : "");
  const [fillOdds, setFillOdds] = useState(scanDraft?.fill_odds != null ? String(scanDraft.fill_odds) : "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (scanDraft || !pickId) return; // scan mode seeds itself; nothing to fetch
    void (async () => {
      try {
        const p = await api.getPick(pickId);
        setPick(p);
        setFillOdds(p.odds != null ? String(p.odds) : "");
      } catch (e) {
        setError((e as Error).message);
      }
    })();
  }, [pickId, scanDraft]);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      if (scanDraft) {
        await api.postBet(
          draftToBetIn(scanDraft, {
            stake: stake ? Number(stake) : undefined,
            fillOdds: fillOdds ? Number(fillOdds) : undefined,
            book: scanDraft.book,
          }),
        );
      } else if (pick) {
        await api.postBet(
          buildBetIn(pick, {
            stake: stake ? Number(stake) : undefined,
            fillOdds: fillOdds ? Number(fillOdds) : undefined,
          }),
        );
      } else {
        setSaving(false);
        return;
      }
      nav.popToTop();
    } catch (e) {
      setError((e as Error).message);
      setSaving(false);
    }
  };

  // ===== Scan mode: prefill from the AI draft, render read-only context. =====
  if (scanDraft) {
    const ctx = [scanDraft.side, scanDraft.point != null ? String(scanDraft.point) : null]
      .filter(Boolean)
      .join(" ");
    return (
      <View style={s.container}>
        <Text style={s.label}>{scanDraft.label ?? scanDraft.player ?? scanDraft.market ?? "스캔 베팅"}</Text>
        <Text style={s.meta}>
          {scanDraft.book ?? "—"}
          {ctx ? ` · ${ctx}` : ""}
          {scanDraft.fill_odds != null ? ` @ ${decimalOdds(scanDraft.fill_odds)}` : ""}
        </Text>
        {scanDraft.event_id == null && <Text style={s.offfeed}>오프피드 — 정산 수동</Text>}
        <Text style={s.field}>스테이크</Text>
        <TextInput style={s.input} placeholder="스테이크 ($)" placeholderTextColor={color.faint} keyboardType="decimal-pad" value={stake} onChangeText={setStake} />
        <Text style={s.field}>실제 체결 배당</Text>
        <TextInput style={s.input} placeholder="실배당" placeholderTextColor={color.faint} keyboardType="decimal-pad" value={fillOdds} onChangeText={setFillOdds} />
        {error && <Text style={s.err}>{error}</Text>}
        <View style={s.actions}>
          <Pressable style={[s.btn, s.cancel]} onPress={() => nav.goBack()}><Text style={s.btnText}>취소</Text></Pressable>
          <Pressable style={[s.btn, s.save]} disabled={saving} onPress={() => void save()}>
            <Text style={s.btnText}>{saving ? "저장 중…" : "기록"}</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  if (!pick) {
    return <View style={s.center}>{error ? <Text style={s.err}>{error}</Text> : <ActivityIndicator />}</View>;
  }

  return (
    <View style={s.container}>
      <Text style={s.label}>{pick.label}</Text>
      <Text style={s.meta}>추천: {pick.book ?? "—"} @ {decimalOdds(pick.odds)}</Text>
      <Text style={s.field}>스테이크</Text>
      <TextInput style={s.input} placeholder="스테이크 ($)" placeholderTextColor={color.faint} keyboardType="decimal-pad" value={stake} onChangeText={setStake} />
      <Text style={s.field}>실제 체결 배당</Text>
      <TextInput style={s.input} placeholder="실배당" placeholderTextColor={color.faint} keyboardType="decimal-pad" value={fillOdds} onChangeText={setFillOdds} />
      {error && <Text style={s.err}>{error}</Text>}
      <View style={s.actions}>
        <Pressable style={[s.btn, s.cancel]} onPress={() => nav.goBack()}><Text style={s.btnText}>취소</Text></Pressable>
        <Pressable style={[s.btn, s.save]} disabled={saving} onPress={() => void save()}>
          <Text style={s.btnText}>{saving ? "저장 중…" : "기록"}</Text>
        </Pressable>
      </View>
    </View>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: color.bg, padding: 18 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: color.bg },
  label: { color: color.text, fontSize: 18, fontWeight: "600" },
  meta: { color: color.muted, marginTop: 6, marginBottom: space.lg },
  offfeed: { color: color.urgent, fontSize: 12, marginBottom: 10 },
  field: { color: color.muted, fontSize: 13, marginTop: space.md, marginBottom: space.xs },
  input: { backgroundColor: color.surface, color: color.text, borderRadius: radius.sm, padding: space.md, fontSize: 16 },
  actions: { flexDirection: "row", gap: 10, marginTop: 24 },
  btn: { flex: 1, padding: space.lg, borderRadius: radius.button, alignItems: "center" },
  cancel: { backgroundColor: color.surfaceRaised },
  save: { backgroundColor: color.accent },
  btnText: { color: color.onAccent, fontWeight: "600", fontSize: 16 },
  err: { color: color.negative, marginTop: space.sm },
});
