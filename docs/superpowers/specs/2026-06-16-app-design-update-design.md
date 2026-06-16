# Companion app design update — design spec (2026-06-16)

## Goal

Refresh the look of the +EV betting companion app (Expo SDK56 / React Native, TestFlight)
from a plain "developer dark theme" into a polished product, AND put it on a centralized
design-token foundation so the look stays consistent and easy to change.

Two threads, done together:
- **B — token foundation:** the palette is currently ~16 hex values duplicated inline across
  screens. Centralize into one `theme.ts`; every screen reads from it.
- **A — visual refresh:** apply a refined dark design language on top of the tokens.

Design direction (approved): **bold/punchy for action screens** (Picks feed, PickDetail —
EV is the hero, confident one-tap CTA) and **calm/data-forward for review screens** (MyBets,
Settings — restrained, metric-led). Same tokens underneath both.

## Out of scope (explicit)

- **No functional or flow changes.** Same screens, same navigation graph, same API calls,
  same deep-link / capture / push behavior. This is a visual + token refactor only.
- No new features. No copy/string changes except where a token-driven control needs one.
- The deep-link prefill logic, `openOrCopy`, capture sheet, mute rules, push routing — all
  unchanged. (The recent copy-toast in `PickDetailScreen` stays as-is, restyled to tokens.)

## Design language — `app/src/theme.ts`

Single source of truth. Plain exported objects (no theming lib). Dark-only (the app is
dark; no light mode).

```ts
export const color = {
  bg:           "#0B0B0F", // app canvas
  surface:      "#16161C", // cards
  surfaceRaised:"#1E1E26", // chips, secondary buttons
  border:       "#26262F", // hairlines
  accent:       "#7C5CFF", // brand violet (refreshed from #7C3AED)
  accentDim:    "#241C46", // violet fills
  accentText:   "#C9BCFF", // violet text/labels on dark
  positive:     "#2FD08A", // +EV, win
  positiveDim:  "#11321F", // +EV pill bg / low-PnL win heatmap cell
  positiveStrong:"#0F4A2C",// high-PnL win heatmap cell
  negative:     "#FB6B6B", // -EV, loss
  negativeDim:  "#3A1A1A", // low-loss heatmap cell
  negativeStrong:"#5A2222",// high-loss heatmap cell
  heatmapEmpty: "#141319", // no-bet day cell
  urgent:       "#FBBF24", // countdown < 1h
  text:         "#F4F4F6", // primary
  muted:        "#9A9AA6", // secondary
  faint:        "#62626E", // tertiary / inactive
} as const;

export const radius = { sm: 8, card: 14, button: 11, pill: 20 } as const;
export const space  = { xs: 4, sm: 8, md: 12, lg: 16, xl: 20 } as const;

// Text presets (RN style objects). Numbers use tabular alignment where shown (odds/EV).
export const font = {
  title:     { fontSize: 21, fontWeight: "600", letterSpacing: -0.4, color: color.text },
  pickLabel: { fontSize: 18, fontWeight: "600", letterSpacing: -0.2, color: color.text },
  evPill:    { fontSize: 17, fontWeight: "600" },
  rowName:   { fontSize: 15, fontWeight: "600", color: color.text },
  body:      { fontSize: 14, fontWeight: "400", color: color.text },
  meta:      { fontSize: 13, fontWeight: "400", color: color.muted },
  micro:     { fontSize: 11, fontWeight: "500", letterSpacing: 0.4 },
} as const;
```

Decision (sub): brand violet nudged `#7C3AED → #7C5CFF` for more pop on the deeper bg.
`accent` is the only place to change it back if desired. Also update `app.json`
`expo-notifications.color` and any splash/icon accent to match `#7C5CFF` for consistency.

## Component / screen specs

All screens import tokens from `theme.ts` and drop their inline hex literals.

### Navigation theming — `app/src/navigation/index.tsx`
The NavigationContainer currently uses the default (light) chrome while screens are dark =
mismatch. Apply a custom dark theme built from tokens:
- `NavigationContainer theme`: `{ dark: true, colors: { background: color.bg, card: color.bg,
  text: color.text, border: color.border, primary: color.accent, notification: color.accent } }`.
- Stack headers: bg `color.bg`, title `color.text`, back tint `color.accent`, no bottom border
  (or a 1px `color.border`).
- Tab bar: bg `color.bg`, top border `color.border`, active tint `color.accent`, inactive
  `color.faint`. Add tab icons via `@expo/vector-icons` (bundled with Expo): 픽 = bolt/zap,
  내 베팅 = calendar, 설정 = settings. (Icon set is an implementation choice; Feather is fine.)

### `theme.ts` (new) — the foundation
Build first; everything below consumes it. Pure data module, trivially testable (a snapshot /
shape test is enough, or none — it's constants).

### `PickCard.tsx` (refined — bold/action)
- Card: `surface` bg, `radius.card`, 1px `border` optional, padding `space.lg`.
- Header row: left = signal chip — `first-entry` = violet dot + `accentText` label;
  `late-crosser` = gray dot + `muted` label "late-crosser · 스킵", whole card `opacity 0.5`.
  Right = **EV pill** (the hero): `positive` text on `positiveDim` bg, `radius.sm`, `font.evPill`.
- Pick label: `font.pickLabel`, white.
- Meta row: `book + odds` bolded (`color.text`), then `N북`, then countdown with a clock icon;
  countdown text turns `color.urgent` when < 1h to start (compute from `commence_time`).
- Actions: full-width `accent` "베팅하기" (`flex: 3`) + compact `surfaceRaised` X dismiss
  (`flex: 1`). Swipe-to-dismiss kept (restyle the swipe action to `negative` bg).
- Tests: existing `PickCard` behavior tests must still pass (onOpen/onBet/onDismiss, swipe).

### `PicksScreen.tsx` (feed)
- Header: `font.title` "+EV 픽" + a filter chip on the right (`accentDim` bg / `accentText`,
  pill) showing the active filter (e.g. "first-entry"). Keep current filter behavior.
- List background `color.bg`. Empty/loading/error states use `muted` / `negative` from tokens.

### `PickDetailScreen.tsx`
- Top summary card (`surface`): signal chip + EV pill + pick label + meta line
  (`MLB · market · countdown · 공정확률 X%`).
- "북별 호가" section label (`micro`, `faint`) + 새로고침 with refresh icon (shows credits).
- Book rows (one per quote, sorted by EV desc — unchanged):
  - **Best book** (top / highest EV): `surface` card with 1px `accent` border + "· 추천"
    (`accentText`) after the name.
  - Tier indicator under the name: verified prefill (`isVerifiedPrefill` true) = `positive`
    "✓ 슬립 프리필"; otherwise `faint` "앱 열기 + 복사". (Strings unchanged so behavior/tests hold.)
  - Right: odds (`rowName`) over EV (`positive`), tabular.
  - Rows below the best are hairline-separated (`border`), not full cards.
  - Tap = `onTapBook` (unchanged) → open/copy → toast (restyled to `surfaceRaised`/`text`).
- "베팅함 기록" = secondary button (`surfaceRaised` bg, `accentText`).
- Tests: `PickDetailScreen.test.tsx` (rows render, tap → toast) must still pass.

### `MyBetsScreen.tsx` (calm — Direction 1)
Current state (verified): renders a "누적 PnL" total card + a flat per-day PnL **row list**
(`calendarSummary(data.calendar).days`) + a "진행 중 (N)" open-bets list. It is NOT a calendar
today, and the only summary metric available is `누적 PnL` (`summary.total`). The redesign:
- Header `font.title` "내 베팅".
- **Metric cards:** restyle the total card → a `surface` metric card (`faint` 11px label over a
  value; `positive`/`negative` by sign) for `누적 PnL`. ROI and W-L cards are shown ONLY if
  `api.getMyBets()` already returns the needed fields (stakes for ROI, win/loss counts). It does
  not today → **ship just the 누적 PnL card; ROI / W-L are a follow-up that needs backend data**
  (don't fabricate). The 3-card row in the mockup is the target once that data exists.
- **Calendar PnL heatmap** (new presentation of the SAME `data.calendar` `{day: pnl}` map —
  replaces the flat day-row list): month grid, 7 cols. Each day cell `aspect-ratio 1`,
  `radius.sm`, color via a new `dayTint(pnl)` helper → no-bet `heatmapEmpty`, win
  `positiveDim`→`positiveStrong` by |PnL|, loss `negativeDim`→`negativeStrong` by |PnL|,
  today `accent` fill + `accentText` outline. Legend row (승 / 패 / 오늘). This is the one
  genuinely new bit of UI (still same data) — call it out in the plan.
- **진행 중 open bets:** keep the list, restyle to tokens (`surface` cards, `STATUS_COLOR` →
  `positive`/`negative`/`muted`).
- Tests: `MyBetsScreen.test.tsx` (total + open-bet grouping) must still pass; add a unit test for
  `dayTint(pnl)` (PnL → tint mapping incl. zero/positive/negative/large boundaries).

### `SettingsScreen.tsx`, `RulesScreen.tsx`, `CaptureSheet.tsx` (token application)
Standard list/form screens — apply tokens (surface rows, `border` dividers, `accent` toggles/
switches, `muted` labels, `bg` canvas). No layout redesign. CaptureSheet keeps its capture/
prefill behavior; only colors + spacing move to tokens. Existing tests must pass.

## Behavior invariants

- Navigation graph, screen names, route params: unchanged.
- All API calls, deep-link build/open, capture, push, mute rules: unchanged.
- All user-visible strings that tests assert on (e.g. "✓ 슬립 프리필", "앱 열기 + 복사",
  "복사됨", book names): unchanged.
- The full existing jest suite (currently 70 tests) must stay green; tsc clean.

## Testing

- `theme.ts`: constants — no test needed (or a trivial shape test).
- Per screen: rely on existing behavior tests (they assert text/interaction, not styles), which
  must continue to pass after the restyle. Add a test only where a NEW token-driven behavior is
  introduced (e.g. countdown-urgency color → test the helper that decides urgent vs normal, if
  extracted; the calendar day-tint helper → unit-test the PnL→tint mapping).
- No automated visual regression; on-device check via TestFlight after the rebuild.

## Implementation order

1. `theme.ts` (tokens) + (optional) extract small helpers: `countdownUrgent(commence)`,
   `dayTint(pnl)`.
2. Navigation theming (container dark theme, headers, tab bar, icons).
3. `PickCard` → tokens + bold treatment.
4. `PicksScreen` (feed header, filter chip, states).
5. `PickDetailScreen` (summary card, best-book accent, prefill chips, rows, toast).
6. `MyBetsScreen` (metric cards + calendar heatmap).
7. `SettingsScreen` / `RulesScreen` / `CaptureSheet` (token application).
8. Sweep: grep `src/` for remaining inline hex literals → all should now come from `theme.ts`.
9. `tsc` + full jest green → EAS rebuild (build#8) → on-device check.

This ships as one app-only change set (no server/EC2 deploy) + one EAS rebuild.
