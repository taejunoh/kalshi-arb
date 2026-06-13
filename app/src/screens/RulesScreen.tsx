// app/src/screens/RulesScreen.tsx
import React, { useCallback, useEffect, useState } from "react";
import { View, Text, TextInput, Pressable, FlatList, StyleSheet } from "react-native";
import { api } from "../api/client";
import type { MuteRule } from "../api/types";

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
          placeholder="값 (예: Aaron Judge / total / EVT123)" placeholderTextColor="#6B7280" autoCapitalize="none" />
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
  wrap: { gap: 8 },
  fieldRow: { flexDirection: "row", gap: 8 },
  chip: { paddingVertical: 6, paddingHorizontal: 12, borderRadius: 16, backgroundColor: "#1F2937" },
  chipOn: { backgroundColor: "#7C3AED" },
  chipText: { color: "#FFF", fontSize: 13 },
  chipTextOn: { color: "#FFF" },
  addRow: { flexDirection: "row", gap: 8, alignItems: "center" },
  input: { flex: 1, backgroundColor: "#1A1A22", color: "#FFF", borderRadius: 8, padding: 10, fontSize: 14 },
  addBtn: { backgroundColor: "#7C3AED", borderRadius: 8, paddingVertical: 10, paddingHorizontal: 14 },
  addBtnText: { color: "#FFF", fontWeight: "600" },
  ruleRow: { flexDirection: "row", justifyContent: "space-between", paddingVertical: 10 },
  ruleText: { color: "#E5E7EB" },
  del: { color: "#F87171" },
  empty: { color: "#888", paddingVertical: 12 },
});
