// app/src/screens/RulesScreen.tsx
import React, { useCallback, useEffect, useState } from "react";
import { View, Text, TextInput, Pressable, FlatList, StyleSheet } from "react-native";
import { api } from "../api/client";
import type { MuteRule } from "../api/types";
import { color, radius, space } from "../theme";

const FIELDS: MuteRule["field"][] = ["player", "market", "sport", "event"];
const FIELD_LABEL: Record<MuteRule["field"], string> = {
  player: "선수", market: "마켓", sport: "종목", event: "경기",
};

export function RulesEditor() {
  const [rules, setRules] = useState<MuteRule[]>([]);
  const [field, setField] = useState<MuteRule["field"]>("player");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try { setRules((await api.getRules()).rules); } catch { /* keep current */ }
  }, []);
  useEffect(() => { load(); }, [load]);

  const add = async () => {
    if (!value.trim() || busy) return;
    setBusy(true);
    try { await api.addRule(field, value.trim()); setValue(""); await load(); }
    finally { setBusy(false); }
  };
  const remove = async (id: number) => {
    setBusy(true);
    try { await api.deleteRule(id); await load(); }
    finally { setBusy(false); }
  };

  return (
    <View style={styles.wrap}>
      <View style={styles.fieldRow}>
        {FIELDS.map((f) => (
          <Pressable key={f} onPress={() => setField(f)}
            style={[styles.chip, field === f && styles.chipOn]}>
            <Text style={[styles.chipText, field === f && styles.chipTextOn]}>{FIELD_LABEL[f]}</Text>
          </Pressable>
        ))}
      </View>
      <View style={styles.addRow}>
        <TextInput style={styles.input} value={value} onChangeText={setValue}
          placeholder="값 (예: Aaron Judge / total / EVT123)" placeholderTextColor={color.faint} autoCapitalize="none" />
        <Pressable onPress={add} style={styles.addBtn}><Text style={styles.addBtnText}>규칙 추가</Text></Pressable>
      </View>
      <FlatList
        data={rules}
        scrollEnabled={false}
        keyExtractor={(r) => String(r.id)}
        ListEmptyComponent={<Text style={styles.empty}>저장된 규칙이 없습니다.</Text>}
        renderItem={({ item }) => (
          <View style={styles.ruleRow}>
            <Text style={styles.ruleText}>{FIELD_LABEL[item.field]} · {item.value}</Text>
            <Pressable testID={`delete-rule-${item.id}`} onPress={() => remove(item.id)}>
              <Text style={styles.del}>삭제</Text>
            </Pressable>
          </View>
        )}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: space.sm },
  fieldRow: { flexDirection: "row", gap: space.sm },
  chip: { paddingVertical: 6, paddingHorizontal: space.md, borderRadius: radius.pill, backgroundColor: color.surfaceRaised },
  chipOn: { backgroundColor: color.accent },
  chipText: { color: color.text, fontSize: 13 },
  chipTextOn: { color: color.onAccent },
  addRow: { flexDirection: "row", gap: space.sm, alignItems: "center" },
  input: { flex: 1, backgroundColor: color.surface, color: color.text, borderRadius: radius.sm, padding: 10, fontSize: 14 },
  addBtn: { backgroundColor: color.accent, borderRadius: radius.sm, paddingVertical: 10, paddingHorizontal: space.lg },
  addBtnText: { color: color.onAccent, fontWeight: "600" },
  ruleRow: { flexDirection: "row", justifyContent: "space-between", paddingVertical: 10 },
  ruleText: { color: color.text },
  del: { color: color.negative },
  empty: { color: color.faint, paddingVertical: space.md },
});
