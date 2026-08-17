// app/src/screens/SettingsScreen.tsx
import { useEffect, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { Feather } from "@expo/vector-icons";
import { api } from "../api/client";
import type { Settings, SortKey } from "../api/types";
import { getCredentials, getUnitStake, saveCredentials, setUnitStake } from "../storage";
import { registerForPush } from "../push";
import { ALL_BOOKS, DEFAULT_SETTINGS, DEFAULT_UNIT_STAKE, MIN_EV_STEPS, SPORT_LABELS } from "../config";
import { RulesEditor } from "./RulesScreen";
import { color, radius, space } from "../theme";
import { useSafeAreaInsets } from "react-native-safe-area-context";

export function SettingsScreen() {
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
  const [baseUrl, setBaseUrl] = useState("");
  const [token, setToken] = useState("");
  const [cfId, setCfId] = useState("");
  const [cfSecret, setCfSecret] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [unit, setUnit] = useState(String(DEFAULT_UNIT_STAKE));
  const [connOpen, setConnOpen] = useState(false);
  const insets = useSafeAreaInsets();

  useEffect(() => {
    void (async () => {
      try {
        const srv = await api.getSettings();
        setSettings(srv);
        if (srv.unit_stake) {
          setUnit(String(srv.unit_stake));
          await setUnitStake(srv.unit_stake);   // refresh the offline copy
        }
      } catch {
        /* no creds yet — keep defaults */
      }
      // Server wins when reachable — it is what Telegram reads. The device
      // copy is the offline fallback and is kept in step on every save.
      setUnit(String(await getUnitStake()));
      setConnOpen(!(await getCredentials())); // first run (no creds) → expanded so it's findable
    })();
  }, []);

  const persistUnit = async () => {
    const n = Number(unit);
    const v = Number.isFinite(n) && n > 0 ? n : DEFAULT_UNIT_STAKE;
    setUnit(String(v));
    await setUnitStake(v);                       // offline copy
    try {
      await api.putSettings({ ...settings, unit_stake: v });  // what Telegram reads
    } catch {
      /* offline: the device copy still applies, server syncs on next save */
    }
  };

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
    <ScrollView style={s.container} contentContainerStyle={{ padding: 16, paddingTop: insets.top + 16 }}>
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

      <Text style={s.field}>유닛 (1회 베팅액)</Text>
      <View style={s.unitRow}>
        <Text style={s.dollar}>$</Text>
        <TextInput
          style={s.unitInput}
          placeholder={String(DEFAULT_UNIT_STAKE)}
          placeholderTextColor={color.faint}
          keyboardType="number-pad"
          value={unit}
          onChangeText={setUnit}
          onEndEditing={() => void persistUnit()}
        />
      </View>
      <Text style={s.hint}>모든 BET 픽에 동일 적용 (bet small · no size-up). 픽 카드 베팅 버튼에 표시.</Text>

      <Text style={s.h}>트리아지 규칙 (Mute rules)</Text>
      <RulesEditor />

      <Pressable style={s.connHeader} onPress={() => setConnOpen((v) => !v)}>
        <Text style={s.h}>연결 (자격 증명)</Text>
        <Feather name={connOpen ? "chevron-up" : "chevron-down"} size={20} color={color.muted} />
      </Pressable>
      {connOpen && (
        <View>
          <TextInput style={s.input} placeholder="API base URL" placeholderTextColor={color.faint} autoCapitalize="none" value={baseUrl} onChangeText={setBaseUrl} />
          <TextInput style={s.input} placeholder="APP_API_TOKEN" placeholderTextColor={color.faint} autoCapitalize="none" value={token} onChangeText={setToken} />
          <TextInput style={s.input} placeholder="CF-Access-Client-Id" placeholderTextColor={color.faint} autoCapitalize="none" value={cfId} onChangeText={setCfId} />
          <TextInput style={s.input} placeholder="CF-Access-Client-Secret" placeholderTextColor={color.faint} autoCapitalize="none" secureTextEntry value={cfSecret} onChangeText={setCfSecret} />
          <Pressable style={s.save} onPress={() => void saveCreds()}><Text style={s.saveText}>자격 증명 저장</Text></Pressable>
          <Pressable style={[s.save, s.push]} onPress={() => void enablePush()}><Text style={s.saveText}>푸시 등록</Text></Pressable>
          {msg && <Text style={s.msg}>{msg}</Text>}
        </View>
      )}
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
  unitRow: { flexDirection: "row", alignItems: "center", backgroundColor: color.surface, borderRadius: radius.sm, paddingHorizontal: space.md },
  dollar: { color: color.muted, fontSize: 16, fontWeight: "600" },
  unitInput: { flex: 1, color: color.text, padding: space.md, fontSize: 16 },
  hint: { color: color.faint, fontSize: 12, marginTop: 6 },
  connHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  input: { backgroundColor: color.surface, color: color.text, borderRadius: radius.sm, padding: space.md, fontSize: 14, marginTop: space.sm },
  save: { backgroundColor: color.accent, borderRadius: radius.button, padding: space.lg, alignItems: "center", marginTop: space.lg },
  push: { backgroundColor: color.surfaceRaised },
  saveText: { color: color.onAccent, fontWeight: "600" },
  msg: { color: color.positive, textAlign: "center", marginTop: space.md },
});
