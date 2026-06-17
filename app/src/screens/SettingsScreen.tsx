// app/src/screens/SettingsScreen.tsx
import { useEffect, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { api } from "../api/client";
import type { Settings, SortKey } from "../api/types";
import { saveCredentials } from "../storage";
import { registerForPush } from "../push";
import { ALL_BOOKS, DEFAULT_SETTINGS, MIN_EV_STEPS, SPORT_LABELS } from "../config";
import { RulesEditor } from "./RulesScreen";
import { color, radius, space } from "../theme";

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
      <TextInput style={s.input} placeholder="API base URL" placeholderTextColor={color.faint} autoCapitalize="none" value={baseUrl} onChangeText={setBaseUrl} />
      <TextInput style={s.input} placeholder="APP_API_TOKEN" placeholderTextColor={color.faint} autoCapitalize="none" value={token} onChangeText={setToken} />
      <TextInput style={s.input} placeholder="CF-Access-Client-Id" placeholderTextColor={color.faint} autoCapitalize="none" value={cfId} onChangeText={setCfId} />
      <TextInput style={s.input} placeholder="CF-Access-Client-Secret" placeholderTextColor={color.faint} autoCapitalize="none" secureTextEntry value={cfSecret} onChangeText={setCfSecret} />
      <Pressable style={s.save} onPress={() => void saveCreds()}><Text style={s.saveText}>자격 증명 저장</Text></Pressable>
      <Pressable style={[s.save, s.push]} onPress={() => void enablePush()}><Text style={s.saveText}>푸시 등록</Text></Pressable>
      {msg && <Text style={s.msg}>{msg}</Text>}

      <Text style={s.h}>트리아지 규칙 (Mute rules)</Text>
      <RulesEditor />
    </ScrollView>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: color.bg },
  h: { color: color.text, fontSize: 18, fontWeight: "600", marginTop: space.xl, marginBottom: space.sm },
  field: { color: color.muted, fontSize: 13, marginTop: space.md, marginBottom: 6 },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  chip: { paddingHorizontal: space.md, paddingVertical: 6, borderRadius: radius.pill, backgroundColor: color.surfaceRaised },
  chipOn: { backgroundColor: color.accent },
  chipMuted: { backgroundColor: color.negative },
  chipText: { color: color.text, fontSize: 13 },
  input: { backgroundColor: color.surface, color: color.text, borderRadius: radius.sm, padding: space.md, fontSize: 14, marginTop: space.sm },
  save: { backgroundColor: color.accent, borderRadius: radius.button, padding: space.lg, alignItems: "center", marginTop: space.lg },
  push: { backgroundColor: color.surfaceRaised },
  saveText: { color: color.onAccent, fontWeight: "600" },
  msg: { color: color.positive, textAlign: "center", marginTop: space.md },
});
