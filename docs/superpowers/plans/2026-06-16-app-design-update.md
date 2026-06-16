# Companion App Design Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refresh the +EV betting companion app's look — bold/action treatment for the pick-flow screens, calm/data-forward for review screens — on a single centralized design-token foundation, with zero functional change.

**Architecture:** Add `app/src/theme.ts` (color/radius/space/font tokens + a React Navigation dark theme). Every screen drops its inline hex literals and reads from the tokens. Two pure helpers (`countdownUrgent`, `dayTint`) and one new presentational component (`CalendarHeatmap`) are the only new logic; everything else is restyle. Behavior, navigation graph, API calls, deep-link/capture/push, and all test-asserted strings stay identical.

**Tech Stack:** Expo SDK56, React Native, TypeScript, React Navigation v7, Jest + @testing-library/react-native, `@expo/vector-icons` (added for tab icons), EAS Build.

**Spec:** `docs/superpowers/specs/2026-06-16-app-design-update-design.md`

---

## File Structure

- **Create** `app/src/theme.ts` — tokens (`color`, `radius`, `space`, `font`) + `navTheme`. Single source of truth.
- **Create** `app/src/components/CalendarHeatmap.tsx` — month PnL heatmap (presentational).
- **Create** `app/src/components/CalendarHeatmap.test.tsx`.
- **Modify** `app/src/format.ts` — add `countdownUrgent()`.
- **Modify** `app/src/format.test.ts` — add `countdownUrgent` tests. (If the file doesn't exist, create it.)
- **Modify** `app/src/mybets.ts` — add `dayTint()`.
- **Modify** `app/src/mybets.test.ts` — add `dayTint` tests. (If absent, create it.)
- **Modify** `app/App.tsx` — pass `theme={navTheme}` to `NavigationContainer`.
- **Modify** `app/src/navigation/index.tsx` — header + tab-bar `screenOptions` + tab icons.
- **Modify** `app/src/components/PickCard.tsx` — tokens + bold treatment.
- **Modify** `app/src/screens/PicksScreen.tsx` — tokens (filter bar, states).
- **Modify** `app/src/screens/PickDetailScreen.tsx` — tokens + summary card + best-book accent + prefill chips + toast.
- **Modify** `app/src/screens/MyBetsScreen.tsx` — metric card + `CalendarHeatmap` + restyle.
- **Modify** `app/src/screens/SettingsScreen.tsx`, `app/src/screens/RulesScreen.tsx`, `app/src/screens/CaptureSheet.tsx` — token swap.
- **Modify** `app/app.json` — `expo-notifications` accent `#7C3AED` → `#7C5CFF`.

### Canonical hex → token map (used in every restyle)

| current hex | token | current hex | token |
|---|---|---|---|
| `#0E0E13` | `color.bg` | `#9CA3AF` | `color.muted` |
| `#1A1A22` | `color.surface` | `#6B7280` / `#888` | `color.faint` |
| `#1F2937` (fill) | `color.surfaceRaised` | `#D1D5DB` / `#E5E7EB` | `color.text` |
| `#1F2937` (border) | `color.border` | `#FFF` (on accent) | `color.onAccent` |
| `#374151` | `color.surfaceRaised` | `#FFF` (on dark) | `color.text` |
| `#7C3AED` | `color.accent` | `#FBBF24` | `color.urgent` |
| `#34D399` | `color.positive` | `#B91C1C` / `#DC2626` | `color.negative` |
| `#F87171` | `color.negative` | `#2563EB` (push btn) | `color.surfaceRaised` bg + `color.accentText` text |

Run all commands from `app/` (the RN project root). Test runner: `npx jest`. Type check: `npx tsc --noEmit`.

---

## Task 1: Design tokens (`theme.ts`)

**Files:**
- Create: `app/src/theme.ts`
- Test: `app/src/theme.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// app/src/theme.test.ts
import { color, radius, space, font, navTheme } from "./theme";

test("tokens expose the load-bearing keys", () => {
  expect(color.bg).toBe("#0B0B0F");
  expect(color.accent).toBe("#7C5CFF");
  expect(color.positive).toBe("#2FD08A");
  expect(color.negative).toBe("#FB6B6B");
  expect(radius.card).toBe(14);
  expect(space.lg).toBe(16);
  expect(font.title.fontWeight).toBe("600");
});

test("navTheme is a dark React Navigation theme using the accent", () => {
  expect(navTheme.dark).toBe(true);
  expect(navTheme.colors.primary).toBe(color.accent);
  expect(navTheme.colors.background).toBe(color.bg);
});
```

- [ ] **Step 2: Run it, verify it fails**

Run: `npx jest src/theme.test.ts`
Expected: FAIL — `Cannot find module './theme'`.

- [ ] **Step 3: Create `theme.ts`**

```ts
// app/src/theme.ts
import type { TextStyle } from "react-native";
import { DarkTheme } from "@react-navigation/native";

export const color = {
  bg: "#0B0B0F",
  surface: "#16161C",
  surfaceRaised: "#1E1E26",
  border: "#26262F",
  accent: "#7C5CFF",
  accentDim: "#241C46",
  accentText: "#C9BCFF",
  onAccent: "#FFFFFF",
  positive: "#2FD08A",
  positiveDim: "#11321F",
  positiveStrong: "#0F4A2C",
  negative: "#FB6B6B",
  negativeDim: "#3A1A1A",
  negativeStrong: "#5A2222",
  urgent: "#FBBF24",
  heatmapEmpty: "#141319",
  text: "#F4F4F6",
  muted: "#9A9AA6",
  faint: "#62626E",
} as const;

export const radius = { sm: 8, card: 14, button: 11, pill: 20 } as const;
export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 20 } as const;

export const font: Record<string, TextStyle> = {
  title: { fontSize: 21, fontWeight: "600", letterSpacing: -0.4, color: color.text },
  pickLabel: { fontSize: 18, fontWeight: "600", letterSpacing: -0.2, color: color.text },
  rowName: { fontSize: 15, fontWeight: "600", color: color.text },
  body: { fontSize: 14, color: color.text },
  meta: { fontSize: 13, color: color.muted },
  micro: { fontSize: 11, fontWeight: "500", letterSpacing: 0.4 },
};

export const navTheme = {
  ...DarkTheme,
  colors: {
    ...DarkTheme.colors,
    primary: color.accent,
    background: color.bg,
    card: color.bg,
    text: color.text,
    border: color.border,
    notification: color.accent,
  },
};
```

- [ ] **Step 4: Run it, verify it passes**

Run: `npx jest src/theme.test.ts`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add app/src/theme.ts app/src/theme.test.ts
git commit -m "feat(app): add centralized design tokens + nav theme"
```

---

## Task 2: Pure helpers — `countdownUrgent` + `dayTint`

**Files:**
- Modify: `app/src/format.ts` (add `countdownUrgent`)
- Modify/Create: `app/src/format.test.ts`
- Modify: `app/src/mybets.ts` (add `dayTint`)
- Modify/Create: `app/src/mybets.test.ts`

- [ ] **Step 1: Write the failing tests**

Append to `app/src/format.test.ts` (create the file with this import header if it doesn't exist: `import { countdownUrgent } from "./format";`):

```ts
test("countdownUrgent: true only when < 1h to start and not started", () => {
  const now = new Date("2026-06-16T12:00:00Z");
  expect(countdownUrgent("2026-06-16T12:45:00Z", now)).toBe(true);   // 45m out
  expect(countdownUrgent("2026-06-16T13:30:00Z", now)).toBe(false);  // 90m out
  expect(countdownUrgent("2026-06-16T11:59:00Z", now)).toBe(false);  // already started
});
```

Append to `app/src/mybets.test.ts` (create with `import { dayTint } from "./mybets"; import { color } from "./theme";` if absent):

```ts
test("dayTint maps PnL to heatmap intensity", () => {
  expect(dayTint(undefined)).toBe(color.heatmapEmpty);
  expect(dayTint(0)).toBe(color.heatmapEmpty);
  expect(dayTint(5)).toBe(color.positiveDim);
  expect(dayTint(25)).toBe(color.positiveStrong);
  expect(dayTint(-5)).toBe(color.negativeDim);
  expect(dayTint(-25)).toBe(color.negativeStrong);
});
```

- [ ] **Step 2: Run, verify fail**

Run: `npx jest src/format.test.ts src/mybets.test.ts`
Expected: FAIL — `countdownUrgent`/`dayTint` not exported.

- [ ] **Step 3: Implement**

Append to `app/src/format.ts`:

```ts
export function countdownUrgent(commenceISO: string, now: Date = new Date()): boolean {
  const diffMs = new Date(commenceISO).getTime() - now.getTime();
  return diffMs > 0 && diffMs < 60 * 60000;
}
```

Append to `app/src/mybets.ts`:

```ts
import { color } from "./theme";

export function dayTint(pnl: number | undefined): string {
  if (pnl === undefined || pnl === 0) return color.heatmapEmpty;
  if (pnl > 0) return pnl >= 20 ? color.positiveStrong : color.positiveDim;
  return pnl <= -20 ? color.negativeStrong : color.negativeDim;
}
```

- [ ] **Step 4: Run, verify pass**

Run: `npx jest src/format.test.ts src/mybets.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/format.ts app/src/format.test.ts app/src/mybets.ts app/src/mybets.test.ts
git commit -m "feat(app): add countdownUrgent + dayTint helpers"
```

---

## Task 3: Navigation theming + tab icons

**Files:**
- Modify: `app/App.tsx:24`
- Modify: `app/src/navigation/index.tsx:37-45`
- Install: `@expo/vector-icons`

- [ ] **Step 1: Install the icon library**

Run: `npx expo install @expo/vector-icons`
Expected: adds `@expo/vector-icons` to `package.json` (managed-workflow safe; ships its own fonts).

- [ ] **Step 2: Apply the nav theme in `App.tsx`**

Change line 24 from `<NavigationContainer ref={navigationRef}>` to:

```tsx
<NavigationContainer ref={navigationRef} theme={navTheme}>
```

Add the import near the other imports at the top of `App.tsx`:

```tsx
import { navTheme } from "./src/theme";
```

- [ ] **Step 3: Style headers + tab bar + icons in `navigation/index.tsx`**

Add the import at the top:

```tsx
import { Feather } from "@expo/vector-icons";
import { color } from "../theme";
```

Replace `PicksNavigator`'s `<PicksStack.Navigator>` opening tag with header options:

```tsx
    <PicksStack.Navigator
      screenOptions={{
        headerStyle: { backgroundColor: color.bg },
        headerTintColor: color.accent,
        headerTitleStyle: { color: color.text },
        headerShadowVisible: false,
        contentStyle: { backgroundColor: color.bg },
      }}
    >
```

Replace the `Tabs.Navigator` block (lines 38-44) with:

```tsx
    <Tabs.Navigator
      screenOptions={{
        headerShown: false,
        tabBarStyle: { backgroundColor: color.bg, borderTopColor: color.border },
        tabBarActiveTintColor: color.accent,
        tabBarInactiveTintColor: color.faint,
      }}
    >
      <Tabs.Screen name="Picks" component={PicksNavigator} options={{ title: "픽", tabBarIcon: ({ color: c, size }) => <Feather name="zap" size={size} color={c} /> }} />
      <Tabs.Screen name="MyBets" component={MyBetsScreen} options={{ title: "내 베팅", tabBarIcon: ({ color: c, size }) => <Feather name="calendar" size={size} color={c} /> }} />
      <Tabs.Screen name="Settings" component={SettingsScreen} options={{ title: "설정", tabBarIcon: ({ color: c, size }) => <Feather name="settings" size={size} color={c} /> }} />
    </Tabs.Navigator>
```

- [ ] **Step 4: Verify**

Run: `npx tsc --noEmit && npx jest src/navigation`
Expected: tsc clean; existing `navigation/types.test.ts` passes.

- [ ] **Step 5: Commit**

```bash
git add app/App.tsx app/src/navigation/index.tsx app/package.json app/package-lock.json
git commit -m "feat(app): dark nav theme + tab icons"
```

---

## Task 4: `PickCard` — tokens + bold treatment

**Files:**
- Modify: `app/src/components/PickCard.tsx`

- [ ] **Step 1: Confirm the guard tests exist & pass first**

Run: `npx jest src/components/PickCard.test.tsx`
Expected: PASS (these guard behavior — onOpen/onBet/onDismiss/swipe — and must stay green after the restyle).

- [ ] **Step 2: Rewrite `PickCard.tsx` body + styles**

Replace the imports block and the returned JSX + `StyleSheet` with the token version. Keep the component signature, props, `Swipeable`, `renderRightActions`, `onOpen/onBet/onDismiss`, and the `first_entry`/`should_bet` logic identical.

Add imports:

```tsx
import { color, radius, space, font } from "../theme";
import { countdown, countdownUrgent, decimalOdds, evPct } from "../format";
```

Replace the returned JSX (lines 25-46) with:

```tsx
  return (
    <Swipeable renderRightActions={renderRightActions}>
    <Pressable style={[s.card, pick.first_entry === false && s.cardMuted]} onPress={() => onOpen(pick)}>
      <View style={s.head}>
        {pick.first_entry !== undefined && (
          <Text style={pick.first_entry ? s.feLabel : s.lcLabel}>
            <Text style={pick.first_entry ? s.feDot : s.lcDot}>● </Text>
            {pick.first_entry ? "first-entry" : "late-crosser · 스킵"}
          </Text>
        )}
        <Text style={s.evPill}>{evPct(pick.ev)}</Text>
      </View>
      <Text style={s.label} numberOfLines={2}>{pick.label}</Text>
      <View style={s.meta}>
        <Text style={s.metaBook}>{pick.book ?? "—"} {decimalOdds(pick.odds)}</Text>
        <Text style={s.metaDim}>{pick.n_books ?? 0}북</Text>
        <Text style={[s.metaDim, countdownUrgent(pick.commence_time) && s.metaUrgent]}>{countdown(pick.commence_time)}</Text>
      </View>
      <View style={s.actions}>
        <Pressable style={[s.btn, s.bet]} onPress={() => onBet(pick)}><Text style={s.betText}>베팅하기</Text></Pressable>
        <Pressable testID="inline-dismiss" style={[s.btn, s.dismiss]} onPress={() => onDismiss(pick)}><Text style={s.dismissText}>관심없음</Text></Pressable>
      </View>
    </Pressable>
    </Swipeable>
  );
```

Replace the `StyleSheet.create({...})` with:

```tsx
const s = StyleSheet.create({
  card: { backgroundColor: color.surface, borderRadius: radius.card, padding: space.lg, marginVertical: 6, marginHorizontal: space.md },
  cardMuted: { opacity: 0.5 },
  head: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  feLabel: { color: color.accentText, fontSize: 12, fontWeight: "500" },
  lcLabel: { color: color.muted, fontSize: 12, fontWeight: "500" },
  feDot: { color: color.accent },
  lcDot: { color: color.faint },
  evPill: { color: color.positive, backgroundColor: color.positiveDim, fontSize: 17, fontWeight: "600", paddingHorizontal: 11, paddingVertical: 3, borderRadius: radius.sm, overflow: "hidden" },
  label: { ...font.pickLabel, marginTop: space.md },
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
```

(The `swipeAction`/`swipeText` keys keep their existing names so `renderRightActions` needs no change.)

- [ ] **Step 3: Verify**

Run: `npx tsc --noEmit && npx jest src/components/PickCard.test.tsx`
Expected: tsc clean; PickCard tests still PASS.

- [ ] **Step 4: Commit**

```bash
git add app/src/components/PickCard.tsx
git commit -m "feat(app): restyle PickCard to tokens (bold treatment)"
```

---

## Task 5: `PicksScreen` — tokens (filter bar + states)

**Files:**
- Modify: `app/src/screens/PicksScreen.tsx`

Functional code (filters, BET-only, scan, cooled footer, FlatList) is unchanged. Only the header title weight and `StyleSheet` colors move to tokens.

- [ ] **Step 1: Add the import**

```tsx
import { color, radius, space } from "../theme";
```

- [ ] **Step 2: Replace the `StyleSheet.create({...})` (lines 151-168) with:**

```tsx
const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: color.bg },
  filterBar: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 6, padding: 10 },
  filterLabel: { color: color.muted, fontSize: 12, marginRight: space.xs },
  chip: { paddingHorizontal: 10, paddingVertical: 5, borderRadius: radius.pill, backgroundColor: color.surfaceRaised },
  chipOn: { backgroundColor: color.accent },
  scanChip: { paddingHorizontal: 10, paddingVertical: 5, borderRadius: radius.pill, backgroundColor: color.accentDim },
  feChip: { paddingHorizontal: 10, paddingVertical: 5, borderRadius: radius.pill, backgroundColor: color.surfaceRaised, borderWidth: 1, borderColor: color.accent },
  feChipOn: { backgroundColor: color.accent, borderColor: color.accent },
  chipText: { color: color.text, fontSize: 12 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: color.bg, padding: space.xl },
  err: { color: color.negative, textAlign: "center", marginBottom: space.md },
  retry: { backgroundColor: color.accent, paddingHorizontal: space.lg, paddingVertical: 10, borderRadius: radius.sm },
  retryText: { color: color.onAccent, fontWeight: "600" },
  empty: { color: color.faint, textAlign: "center", marginTop: 40 },
  cooledHeader: { padding: space.lg },
  cooledText: { color: color.muted, fontWeight: "600" },
});
```

(The `📷`/`🟣` emoji in chip labels stay as-is — they're test-neutral content, not styling.)

- [ ] **Step 3: Verify**

Run: `npx tsc --noEmit && npx jest src/screens/PicksScreen.test.tsx`
Expected: tsc clean; PicksScreen tests PASS.

- [ ] **Step 4: Commit**

```bash
git add app/src/screens/PicksScreen.tsx
git commit -m "feat(app): restyle PicksScreen filter bar to tokens"
```

---

## Task 6: `PickDetailScreen` — summary card + best-book accent + prefill chips

**Files:**
- Modify: `app/src/screens/PickDetailScreen.tsx`

Keep all behavior: `load`/refresh, `onTapBook`, toast logic, `isVerifiedPrefill`, row sorting (server already returns books EV-desc), `decimalOdds`/`evPct`. The first row in `data.books` is the best (highest EV) — give it the accent treatment.

- [ ] **Step 1: Add imports**

```tsx
import { color, radius, space, font } from "../theme";
```

- [ ] **Step 2: Replace the rendered book list + header (the `return (<View style={s.root}>...` block) with:**

```tsx
  return (
    <View style={s.root}>
      <ScrollView style={s.container} contentContainerStyle={{ padding: space.md }}>
        <View style={s.headerRow}>
          <Text style={s.fair}>{data?.fair != null ? `공정확률 ${(data.fair * 100).toFixed(1)}%` : "—"}</Text>
          <Pressable style={s.refresh} onPress={() => void load()}><Text style={s.refreshText}>새로고침{data ? ` (${data.credits})` : ""}</Text></Pressable>
        </View>
        {loading && <ActivityIndicator style={{ marginTop: 24 }} />}
        {error && <Text style={s.err}>로드 실패: {error}</Text>}
        {data?.books.map((b, i) => {
          const verified = isVerifiedPrefill(b.book, b.deeplink);
          return (
            <Pressable key={b.book} style={[s.bookRow, i === 0 && s.bookBest]} onPress={() => void onTapBook(b)}>
              <View style={{ flex: 1 }}>
                <Text style={s.bookName}>{b.book}{i === 0 ? <Text style={s.bestTag}> · 추천</Text> : null}</Text>
                <Text style={verified ? s.prefill : s.openCopy}>{verified ? "✓ 슬립 프리필" : "앱 열기 + 복사"}</Text>
              </View>
              <View style={{ alignItems: "flex-end" }}>
                <Text style={s.odds}>{decimalOdds(b.odds)}</Text>
                <Text style={s.bookEv}>{evPct(b.ev)}</Text>
              </View>
            </Pressable>
          );
        })}
        <Pressable style={s.betBtn} onPress={() => nav.push("CaptureSheet", { pickId })}>
          <Text style={s.betText}>베팅함 기록</Text>
        </Pressable>
      </ScrollView>
      {toast && (
        <View style={s.toast} pointerEvents="none"><Text style={s.toastText}>{toast}</Text></View>
      )}
    </View>
  );
```

(Note: the per-book tier strings `"✓ 슬립 프리필"` and `"앱 열기 + 복사"` and the toast strings are unchanged — invariants.)

- [ ] **Step 3: Replace the `StyleSheet.create({...})` with:**

```tsx
const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: color.bg },
  container: { flex: 1, backgroundColor: color.bg },
  headerRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: space.sm },
  fair: { color: color.muted, fontSize: 14 },
  refresh: { backgroundColor: color.surfaceRaised, paddingHorizontal: space.md, paddingVertical: 6, borderRadius: radius.sm },
  refreshText: { color: color.accentText, fontSize: 12 },
  err: { color: color.negative, marginTop: space.md },
  bookRow: { flexDirection: "row", alignItems: "center", backgroundColor: color.surface, borderRadius: radius.button, padding: space.lg, marginVertical: 5 },
  bookBest: { borderWidth: 1, borderColor: color.accent },
  bookName: { ...font.rowName },
  bestTag: { color: color.accentText, fontSize: 11, fontWeight: "500" },
  prefill: { color: color.positive, fontSize: 11, marginTop: 3 },
  openCopy: { color: color.faint, fontSize: 11, marginTop: 3 },
  odds: { color: color.text, fontSize: 15, fontWeight: "600" },
  bookEv: { color: color.positive, fontSize: 12, marginTop: 2 },
  betBtn: { backgroundColor: color.surfaceRaised, borderRadius: radius.button, padding: space.lg, alignItems: "center", marginTop: space.lg },
  betText: { color: color.accentText, fontWeight: "600", fontSize: 15 },
  toast: { position: "absolute", left: space.lg, right: space.lg, bottom: 28, backgroundColor: color.surfaceRaised, borderRadius: radius.sm, paddingVertical: space.md, paddingHorizontal: space.lg, alignItems: "center", borderWidth: 1, borderColor: color.border },
  toastText: { color: color.text, fontSize: 13, textAlign: "center" },
});
```

- [ ] **Step 4: Verify**

Run: `npx tsc --noEmit && npx jest src/screens/PickDetailScreen.test.tsx`
Expected: tsc clean; PickDetail tests (rows render, tap → toast) PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/screens/PickDetailScreen.tsx
git commit -m "feat(app): restyle PickDetail — summary, best-book accent, prefill chips"
```

---

## Task 7: `MyBetsScreen` — metric card + `CalendarHeatmap`

**Files:**
- Create: `app/src/components/CalendarHeatmap.tsx`
- Create: `app/src/components/CalendarHeatmap.test.tsx`
- Modify: `app/src/screens/MyBetsScreen.tsx`

The heatmap renders the current month from the `calendar` `{ "YYYY-MM-DD": pnl }` map. Month is derived from the most-recent key (deterministic, no `new Date()` in the layout) so it's testable; "today" highlight uses the max key.

- [ ] **Step 1: Write the failing component test**

```tsx
// app/src/components/CalendarHeatmap.test.tsx
import { render, screen } from "@testing-library/react-native";
import { CalendarHeatmap } from "./CalendarHeatmap";

test("renders a cell per day of the month and marks the latest day", async () => {
  await render(<CalendarHeatmap calendar={{ "2026-06-03": 12, "2026-06-10": -8, "2026-06-15": 30 }} />);
  // 30 day cells for June + the title
  expect(screen.getByText("2026-06")).toBeOnTheScreen();
  expect(screen.getByTestId("heat-2026-06-15")).toBeOnTheScreen();
  expect(screen.getByTestId("heat-2026-06-30")).toBeOnTheScreen();
});
```

- [ ] **Step 2: Run, verify fail**

Run: `npx jest src/components/CalendarHeatmap.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Create `CalendarHeatmap.tsx`**

```tsx
// app/src/components/CalendarHeatmap.tsx
import { StyleSheet, Text, View } from "react-native";
import { color, radius, space } from "../theme";
import { dayTint } from "../mybets";

// `calendar`: { "YYYY-MM-DD": pnl }. Renders the month of the most-recent key as a
// 7-col grid; each day cell is tinted by dayTint(pnl); the latest day gets an outline.
export function CalendarHeatmap({ calendar }: { calendar: Record<string, number> }) {
  const keys = Object.keys(calendar).sort();
  const latest = keys.length ? keys[keys.length - 1] : null;
  const ym = (latest ?? "2026-06-01").slice(0, 7); // "YYYY-MM"
  const [y, m] = ym.split("-").map(Number);
  const daysInMonth = new Date(y, m, 0).getDate();
  const days = Array.from({ length: daysInMonth }, (_, i) => i + 1);

  return (
    <View>
      <Text style={s.month}>{ym}</Text>
      <View style={s.grid}>
        {days.map((d) => {
          const key = `${ym}-${String(d).padStart(2, "0")}`;
          const isLatest = key === latest;
          return (
            <View
              key={key}
              testID={`heat-${key}`}
              style={[s.cell, { backgroundColor: isLatest ? color.accent : dayTint(calendar[key]) }, isLatest && s.today]}
            />
          );
        })}
      </View>
      <View style={s.legend}>
        <Legend c={color.positiveStrong} label="승" />
        <Legend c={color.negativeStrong} label="패" />
        <Legend c={color.accent} label="오늘" />
      </View>
    </View>
  );
}

function Legend({ c, label }: { c: string; label: string }) {
  return (
    <View style={s.legendItem}>
      <View style={[s.legendDot, { backgroundColor: c }]} />
      <Text style={s.legendText}>{label}</Text>
    </View>
  );
}

const s = StyleSheet.create({
  month: { color: color.muted, fontSize: 13, fontWeight: "500", marginBottom: 9 },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 5 },
  cell: { width: "12.5%", aspectRatio: 1, borderRadius: radius.sm, maxWidth: 34 },
  today: { borderWidth: 2, borderColor: color.accentText },
  legend: { flexDirection: "row", justifyContent: "center", gap: space.lg, marginTop: space.md },
  legendItem: { flexDirection: "row", alignItems: "center", gap: 5 },
  legendDot: { width: 9, height: 9, borderRadius: 2 },
  legendText: { color: color.faint, fontSize: 11 },
});
```

Note: `new Date(y, m, 0).getDate()` (days-in-month) uses fixed numeric args — deterministic, no clock dependency.

- [ ] **Step 4: Run, verify pass**

Run: `npx jest src/components/CalendarHeatmap.test.tsx`
Expected: PASS.

- [ ] **Step 5: Rewrite `MyBetsScreen.tsx` presentation (keep data flow)**

Replace imports + the returned JSX + `StyleSheet`. Keep `load`, `useFocusEffect`, `calendarSummary`, `data.open` rendering.

Imports:

```tsx
import { calendarSummary } from "../mybets";
import { decimalOdds, liveProgressLabel, pnl } from "../format";
import { CalendarHeatmap } from "../components/CalendarHeatmap";
import { color, radius, space, font } from "../theme";
```

Replace `STATUS_COLOR` (lines 10-14) with:

```tsx
const STATUS_COLOR: Record<string, string> = {
  winning: color.positive,
  losing: color.negative,
  pending: color.muted,
};
```

Replace the returned JSX (the `<ScrollView>` block) with:

```tsx
  return (
    <ScrollView style={s.container} contentContainerStyle={{ padding: space.md }}>
      <Text style={s.title}>내 베팅</Text>
      <View style={s.metric}>
        <Text style={s.metricLabel}>누적 PnL</Text>
        <Text style={[s.metricVal, { color: summary.total >= 0 ? color.positive : color.negative }]}>{pnl(summary.total)}</Text>
      </View>
      <CalendarHeatmap calendar={data?.calendar ?? {}} />

      <Text style={s.section}>진행 중 ({data?.open.length ?? 0})</Text>
      {loading && <ActivityIndicator style={{ marginTop: space.lg }} />}
      {data?.open.map((b: OpenBet) => (
        <View key={b.id} style={s.betCard}>
          <View style={s.betRow}>
            <Text style={s.betLabel} numberOfLines={1}>{b.label ?? `${b.side} ${b.point ?? ""}`}</Text>
            <Text style={[s.betStatus, { color: STATUS_COLOR[b.live_status ?? "pending"] ?? color.muted }]}>
              {b.live_status ?? "pending"}
            </Text>
          </View>
          <Text style={s.betMeta}>
            {b.book ?? "—"} @ {decimalOdds(b.fill_odds)} · ${b.stake ?? 0}
            {b.live_progress ? ` · ${liveProgressLabel(b.live_progress)}` : ""}
          </Text>
        </View>
      ))}
      {!loading && (data?.open.length ?? 0) === 0 && <Text style={s.empty}>진행 중인 베팅 없음</Text>}
    </ScrollView>
  );
```

Replace the `StyleSheet.create({...})` with:

```tsx
const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: color.bg },
  title: { ...font.title, marginBottom: space.md },
  metric: { backgroundColor: color.surface, borderRadius: radius.sm, padding: space.md, marginBottom: space.lg },
  metricLabel: { color: color.faint, fontSize: 11 },
  metricVal: { fontSize: 24, fontWeight: "600", marginTop: 3 },
  section: { color: color.text, fontSize: 16, fontWeight: "600", marginTop: space.xl, marginBottom: space.sm },
  betCard: { backgroundColor: color.surface, borderRadius: radius.sm, padding: space.lg, marginVertical: 5 },
  betRow: { flexDirection: "row", justifyContent: "space-between" },
  betLabel: { color: color.text, fontWeight: "600", flex: 1, paddingRight: space.sm },
  betStatus: { fontWeight: "600", fontSize: 13 },
  betMeta: { color: color.muted, marginTop: 6, fontSize: 13 },
  empty: { color: color.faint, textAlign: "center", marginTop: space.xl },
});
```

- [ ] **Step 6: Verify**

Run: `npx tsc --noEmit && npx jest src/screens/MyBetsScreen.test.tsx src/components/CalendarHeatmap.test.tsx`
Expected: tsc clean; PASS. `MyBetsScreen.test.tsx` needs NO change — it asserts the open bet (`Nimmo TB Over 1.5`), its progress (`2 / 1.5`), and the calendar total (`+2.40`). The redesign keeps the open-bets list and renders the total in the new `누적 PnL` metric card (still `pnl(summary.total)`), so all three still match; the heatmap replaces only the flat day-row list, which the test never asserted.

- [ ] **Step 7: Commit**

```bash
git add app/src/components/CalendarHeatmap.tsx app/src/components/CalendarHeatmap.test.tsx app/src/screens/MyBetsScreen.tsx app/src/screens/MyBetsScreen.test.tsx
git commit -m "feat(app): MyBets metric card + calendar heatmap"
```

---

## Task 8: Settings + Rules + CaptureSheet — token swap

**Files:**
- Modify: `app/src/screens/SettingsScreen.tsx`
- Modify: `app/src/screens/RulesScreen.tsx`
- Modify: `app/src/screens/CaptureSheet.tsx`

Pure color swaps via the canonical map. JSX unchanged. Add `import { color, radius, space } from "../theme";` to each.

- [ ] **Step 1: `SettingsScreen.tsx` — replace `StyleSheet.create({...})` (lines 107-121) with:**

```tsx
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
```

Also change the four `placeholderTextColor="#6B7280"` to `placeholderTextColor={color.faint}`. The push button keeps `style={[s.save, s.push]}` with the existing white `s.saveText` label — white on `surfaceRaised` is readable, so no JSX change is needed.

- [ ] **Step 2: `RulesScreen.tsx` — replace `StyleSheet.create({...})` (lines 68-83) with:**

```tsx
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
```

Change `placeholderTextColor="#6B7280"` to `placeholderTextColor={color.faint}`.

- [ ] **Step 3: `CaptureSheet.tsx` — replace `StyleSheet.create({...})` (lines 121-135) with:**

```tsx
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
```

Change both `placeholderTextColor="#6B7280"` to `placeholderTextColor={color.faint}`. Note `cancel` button text uses `s.btnText` (white on `surfaceRaised` — acceptable; if too bright, swap that `<Text>` to `color.text`).

- [ ] **Step 4: Verify**

Run: `npx tsc --noEmit && npx jest src/screens/SettingsScreen.test.tsx src/screens/RulesScreen.test.tsx src/screens/CaptureSheet.test.tsx`
Expected: tsc clean; all three suites PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/screens/SettingsScreen.tsx app/src/screens/RulesScreen.tsx app/src/screens/CaptureSheet.tsx
git commit -m "feat(app): token swap for Settings/Rules/Capture"
```

---

## Task 9: Accent in app.json + hex sweep + full verify

**Files:**
- Modify: `app/app.json`
- Sweep: `app/src/**`

- [ ] **Step 1: Update the notification accent in `app.json`**

In the `expo-notifications` plugin config, change `"color": "#7C3AED"` to `"color": "#7C5CFF"`.

- [ ] **Step 2: Sweep for stray inline hexes**

Run: `grep -rnE "#[0-9A-Fa-f]{6}" app/src --include=*.tsx --include=*.ts | grep -v "theme.ts" | grep -v ".test."`
Expected: NO matches outside `theme.ts` (every color now comes from tokens). If any remain, replace via the canonical map and re-run.

- [ ] **Step 3: Full type + test gate**

Run: `npx tsc --noEmit && npx jest`
Expected: tsc clean; full suite green (the 70 prior tests + the new theme/helper/heatmap tests).

- [ ] **Step 4: Commit**

```bash
git add app/app.json
git commit -m "chore(app): notification accent to #7C5CFF + confirm no stray hexes"
```

---

## Task 10: Ship — EAS build#8 + on-device check

**Files:** none (release)

- [ ] **Step 1: Push**

```bash
git push origin master
```

- [ ] **Step 2: Build + submit**

Run (from `app/`): `eas build -p ios --profile production --auto-submit --non-interactive`
Expected: buildNumber auto-increments to 8; build finishes; submitted to TestFlight.

- [ ] **Step 3: On-device check after Apple processing**

Update TestFlight to build#8 and confirm:
- Picks feed: bold cards, EV pill, first-entry dot, amber countdown < 1h, violet CTA, themed tab bar + icons.
- PickDetail: summary card, best book accented "· 추천", "✓ 슬립 프리필" green on FD/DK/Caesars/BetRivers, copy-toast still fires on Fanatics.
- MyBets: 누적 PnL metric card + calendar heatmap.
- Headers + tab bar are dark (no light flash).
- Deep-link / capture / push behavior unchanged.

---

## Notes for the executor

- This is one app-only change set — **no server / EC2 deploy**.
- A parallel session is also committing to `master`; before Task 10's push, `git pull --rebase origin master` and re-run `npx jest` to confirm green on top of any new commits.
- Keep every test-asserted string identical (`✓ 슬립 프리필`, `앱 열기 + 복사`, `복사됨`, book/tab titles). Restyle changes colors/layout, never copy.
