// app/src/screens/SettingsScreen.tsx
import { useEffect, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { api } from "../api/client";
import type { Settings, SortKey } from "../api/types";
import { saveCredentials } from "../storage";
import { registerForPush } from "../push";
import { ALL_BOOKS, DEFAULT_SETTINGS, MIN_EV_STEPS, SPORT_LABELS } from "../config";

export function SettingsScreen() {
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
  const [baseUrl, setBaseUrl] = useState("");
  const [token, setToken] = useState("");
  const [cfId, setCfId] = useState("");
  const [cfSecret, setCfSecret] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        setSettings(await api.getSettings());
      } catch {
        /* no creds yet — keep defaults */
      }
    })();
  }, []);

  const persist = async (next: Settings) => {
    setSettings(next);
    try {
      await api.putSettings(next);
    } catch (e) {
      setMsg(`설정 저장 실패: ${(e as Error).message}`);
    }
  };

  const toggle = (list: string[] | null, value: string): string[] | null => {
    const set = new Set(list ?? []);
    set.has(value) ? set.delete(value) : set.add(value);
    return set.size ? [...set] : null;
  };

  const saveCreds = async () => {
    await saveCredentials({ baseUrl, token, cfId, cfSecret });
    setMsg("자격 증명 저장됨");
  };

  const enablePush = async () => {
    const t = await registerForPush();
    if (!t) return setMsg("푸시 권한/토큰 실패 (실기기 필요)");
    await api.registerPush(t);
    setMsg("푸시 등록됨");
  };

  return (
    <ScrollView style={s.container} contentContainerStyle={{ padding: 16 }}>
      <Text style={s.h}>필터</Text>
      <Text style={s.field}>최소 EV</Text>
      <View style={s.chips}>
        {MIN_EV_STEPS.map((v) => (
          <Pressable key={v} style={[s.chip, settings.min_ev === v && s.chipOn]} onPress={() => void persist({ ...settings, min_ev: v })}>
            <Text style={s.chipText}>{v === 0 ? "전체" : `${(v * 100).toFixed(0)}%`}</Text>
          </Pressable>
        ))}
      </View>
      <Text style={s.field}>종목</Text>
      <View style={s.chips}>
        {Object.entries(SPORT_LABELS).map(([key, label]) => (
          <Pressable key={key} style={[s.chip, settings.sports?.includes(key) && s.chipOn]} onPress={() => void persist({ ...settings, sports: toggle(settings.sports, key) })}>
            <Text style={s.chipText}>{label}</Text>
          </Pressable>
        ))}
      </View>
      <Text style={s.field}>북 음소거</Text>
      <View style={s.chips}>
        {ALL_BOOKS.map((b) => (
          <Pressable key={b} style={[s.chip, settings.muted_books?.includes(b) && s.chipMuted]} onPress={() => void persist({ ...settings, muted_books: toggle(settings.muted_books, b) })}>
            <Text style={s.chipText}>{b}</Text>
          </Pressable>
        ))}
      </View>
      <Text style={s.field}>정렬</Text>
      <View style={s.chips}>
        {(["ev", "time", "n_books"] as SortKey[]).map((k) => (
          <Pressable key={k} style={[s.chip, settings.sort === k && s.chipOn]} onPress={() => void persist({ ...settings, sort: k })}>
            <Text style={s.chipText}>{k === "ev" ? "EV순" : k === "time" ? "시간순" : "북수순"}</Text>
          </Pressable>
        ))}
      </View>

      <Text style={s.h}>연결</Text>
      <TextInput style={s.input} placeholder="API base URL" placeholderTextColor="#6B7280" autoCapitalize="none" value={baseUrl} onChangeText={setBaseUrl} />
      <TextInput style={s.input} placeholder="APP_API_TOKEN" placeholderTextColor="#6B7280" autoCapitalize="none" value={token} onChangeText={setToken} />
      <TextInput style={s.input} placeholder="CF-Access-Client-Id" placeholderTextColor="#6B7280" autoCapitalize="none" value={cfId} onChangeText={setCfId} />
      <TextInput style={s.input} placeholder="CF-Access-Client-Secret" placeholderTextColor="#6B7280" autoCapitalize="none" secureTextEntry value={cfSecret} onChangeText={setCfSecret} />
      <Pressable style={s.save} onPress={() => void saveCreds()}><Text style={s.saveText}>자격 증명 저장</Text></Pressable>
      <Pressable style={[s.save, s.push]} onPress={() => void enablePush()}><Text style={s.saveText}>푸시 등록</Text></Pressable>
      {msg && <Text style={s.msg}>{msg}</Text>}
    </ScrollView>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#0E0E13" },
  h: { color: "#FFF", fontSize: 18, fontWeight: "800", marginTop: 20, marginBottom: 8 },
  field: { color: "#9CA3AF", fontSize: 13, marginTop: 12, marginBottom: 6 },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  chip: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: 14, backgroundColor: "#1F2937" },
  chipOn: { backgroundColor: "#7C3AED" },
  chipMuted: { backgroundColor: "#B91C1C" },
  chipText: { color: "#FFF", fontSize: 13 },
  input: { backgroundColor: "#1A1A22", color: "#FFF", borderRadius: 8, padding: 12, fontSize: 14, marginTop: 8 },
  save: { backgroundColor: "#7C3AED", borderRadius: 10, padding: 14, alignItems: "center", marginTop: 14 },
  push: { backgroundColor: "#2563EB" },
  saveText: { color: "#FFF", fontWeight: "700" },
  msg: { color: "#34D399", textAlign: "center", marginTop: 12 },
});
