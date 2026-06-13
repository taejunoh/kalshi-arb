# +EV Companion App — iOS Expo App (Plan 2 of 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the native iOS (Expo / React Native) app that consumes the finished Plan 1 backend API — a 3-tab +EV betting companion that surfaces the High-pick feed, deep-links into sportsbooks, captures placed bets one-tap, tracks them live in-game, shows calendar PnL, and receives actionable push notifications — fully replacing Telegram.

**Architecture:** A new Expo SDK 56 TypeScript app in `app/` (a subtree of this repo, mirroring how Plan 1 kept the backend in `scripts/`). It talks **only** to the FastAPI service (`scripts/softbook_app_api.py`, already deployed) over HTTPS through the Cloudflare Tunnel, attaching the bearer + CF Access service-token headers it holds in the Keychain. All business logic (feed composition, deep-link tiering, reverse-middle, live grading, settlement) lives server-side; the app is a thin, well-typed client + UI. Pure logic (storage, api client, deep-link open, push routing, formatting, capture-state) is unit-tested with jest-expo; screens get smoke tests.

**Tech Stack:** Expo SDK 56 (React Native 0.85, React 19.2, TypeScript 6), React Navigation 7 (bottom-tabs + native-stack), expo-secure-store, expo-linking, expo-clipboard, expo-notifications, expo-constants, expo-device. Tests: jest-expo 56 + @testing-library/react-native 14. Build/distribution: EAS Build + TestFlight.

---

## Scope & Decomposition

This is the **front-end half** of the two-plan split defined in the spec (`docs/superpowers/specs/2026-06-12-ev-betting-companion-app-design.md`). Plan 1 (backend — `docs/superpowers/plans/2026-06-12-ev-companion-app-backend.md`) is **done, merged, and deployed** (master `326b73c`, 51/51 tests). This plan consumes its 9-endpoint API contract and adds nothing to the Python side. The two subsystems share a clean HTTP boundary, so this plan is self-contained: it produces a working, installable iOS app whose every screen is exercised against a mocked client in tests and against the live API on device.

The app surfaces Phase-1 features only (per spec): picks feed + filters + lifecycle, on-demand odds refresh + tiered deep-link, one-tap capture, live tracking, calendar PnL, push with actionable buttons. Screenshot-AI capture, price-move/cashout push, and saved mute-rules are **Phase 2** (out of scope here).

## The API contract this app consumes (ground truth)

From `scripts/softbook_app_api.py` (do not re-derive — these are the exact shapes):

| Method + path | Request | Response |
|---|---|---|
| `GET /picks` | — | `{ picks: Pick[], counts: Record<status,number>, cooled: Pick[] }` |
| `GET /picks/{id}` | — | `Pick` |
| `GET /picks/{id}/refresh` | — | `{ fair: number\|null, books: BookQuote[], credits: number }` |
| `POST /bets` | `BetIn` | `{ id: number }` |
| `POST /picks/{id}/dismiss` | — | `{ ok: true }` (server auto-dismisses the reverse-middle opposite) |
| `GET /mybets` | — | `{ calendar: Record<"YYYY-MM-DD",number>, open: OpenBet[] }` |
| `GET /settings` | — | `Settings` |
| `PUT /settings` | `Settings` | `{ ok: true }` |
| `POST /push/register` | `{ token: string }` | `{ ok: true }` |

- **Auth (every request):** `Authorization: Bearer <APP_API_TOKEN>`, `CF-Access-Client-Id: <id>`, `CF-Access-Client-Secret: <secret>`.
- **`Pick`** (a feed row): `id` (= `"event_id|market|side|point|player"`), `status` (`active|cooled|expired|bet|dismissed`), `event_id`, `sport`, `home`, `away`, `commence_time`, `market`, `side`, `point`, `player`, `label`, `ev`, `book`, `odds`, `conf`, `n_books`. The path `id` must be `encodeURIComponent`'d (it contains `|` and spaces).
- **`BookQuote`** (from `/refresh`): `{ book: string, odds: number, ev: number|null, deeplink: { tier: "prefill"|"event"|"home", url: string|null, copy_text: string } }`.
- **`BetIn`** (to `POST /bets`): `{ event_id, sport?, home?, away?, commence_time?, market, side, point?, player?, label?, book?, rec_odds?, fill_odds?, stake?, source }` (`source` defaults `"one_tap"`).
- **`OpenBet`** (from `/mybets` `open[]`): `{ id, event_id, sport, home, away, commence_time, market, side, point, player, label, book, fill_odds, stake, live_status, live_progress }` where `live_progress` is a parsed object `{ current: number|null, needed: number, remaining?: number, game_state: string|null }` or `null`.
- **`Settings`**: `{ min_ev: number, sports: string[]|null, muted_books: string[]|null, sort: "ev"|"time"|"n_books" }`.
- **Push payloads the backend already sends** (`scripts/softbook_push_worker.py`): category **`high_pick`** with `data={pick_id}` (+ a `BET_PLACED`/`NOT_INTERESTED` button pair this app must register), and category **`settle`** with `data={app_bet_id}`.

## Manual Prerequisites (the human does these — NOT code tasks)

1. **Backend live.** Plan 1's `docs/setup-cloudflare-tunnel.md` runbook must be completed: domain bought, Cloudflare Tunnel + Access service token created, EC2 `.env` has `APP_API_TOKEN`/`CF_ACCESS_TEAM_DOMAIN`/`CF_ACCESS_AUD`, and `kalshi-softbook-api.service` is up. Confirm with the runbook's curl smoke test before starting Task 8 (first on-device run).
2. **Apple Developer Program ($99/yr)** + an **Expo/EAS account** (`npx eas login`). Needed for Task 1's `eas init` (writes the real `projectId`), for push delivery (Task 6/13), and for the TestFlight build (Task 13).
3. **Local toolchain:** Node 22.13+ (the user has v26 — fine), `npx` available. A physical NY iPhone for push + deep-link verification (the Simulator cannot receive push or open sportsbook apps).
4. **The four runtime credentials** the user types into the app's Settings screen on first launch (Task 12): the API base URL (`https://api.<domain>`), `APP_API_TOKEN`, and the CF Access service token's `Client-Id` + `Client-Secret`. These live in the Keychain, never in source.

## File Structure

New tree under `app/` (created by Task 1; one responsibility per file):

```
app/
  app.json                      # Expo config: scheme, ios bundleId/infoPlist (LSApplicationQueriesSchemes), plugins, eas.projectId
  eas.json                      # EAS build profiles (preview = internal device, production = TestFlight)
  tsconfig.json                 # extends expo/tsconfig.base, strict
  package.json                  # deps + jest config (preset jest-expo)
  App.tsx                       # root: NavigationContainer + navigationRef + setNotificationHandler + <PushBridge/>
  src/
    config.ts                   # constants: category/action ids, sport labels, book list, verified-prefill allowlist, default settings
    storage.ts                  # expo-secure-store credential vault (baseUrl + token + CF id/secret)
    format.ts                   # pure formatters: odds, ev%, pnl, commence countdown, live-progress label
    api/
      types.ts                  # TS types for every API shape above
      client.ts                 # fetch wrapper injecting auth headers + one method per endpoint
    deeplink.ts                 # openOrCopy(deeplink) (canOpenURL→openURL→clipboard) + isVerifiedPrefill gate
    push.ts                     # registerForPush + registerCategories + notifTarget(response) pure router
    navigation/
      types.ts                  # RootParamList / TabParamList / PicksStackParamList
      index.tsx                 # tab navigator + Picks stack + navigationRef
    screens/
      PicksScreen.tsx           # feed + filter bar + per-card 베팅/관심없음 + cooled section
      PickDetailScreen.tsx      # GET /refresh on open + per-book deep-link rows
      CaptureSheet.tsx          # one-tap confirm (prefilled; stake + fill_odds) → POST /bets
      MyBetsScreen.tsx          # calendar PnL (top) + live open bets (bottom)
      SettingsScreen.tsx        # filter prefs + credential entry + push re-register
    components/
      PickCard.tsx              # one feed row (label, book@odds, ev, conf/n_books)
    PushBridge.tsx              # launch: register push token + categories; route notification taps via navigationRef
  DEEPLINK_VERIFICATION.md      # on-device checklist gating the "✓ 슬립 프리필" label (Task 13)
```

Modified files outside `app/`: none. (The Python backend is frozen.)

## Conventions (match these exactly)

- **Versions via `npx expo install`**, not raw `npm install`, for every Expo/native package (expo-*, react-native-screens, react-native-safe-area-context, jest-expo) so the SDK pins the compatible version. Only the React Navigation core packages and pure dev-deps are plain `npm install`. The version numbers in this plan are the current latest (June 2026); if `expo install` resolves a slightly different SDK-pinned patch, that wins.
- **All commands run from `app/`** unless stated: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && <cmd>`.
- **Run a test:** `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest <path> --watchAll=false`.
- **Tests are colocated** as `src/**/<name>.test.ts(x)`. jest-expo discovers them.
- **Commit after every task** with explicit paths (`git add app/<paths>` — the user keeps parallel uncommitted edits, never `git add .`) and message `feat(softbook-app): <what>`.
- **Imports:** relative within `src/` (`import { api } from '../api/client'`). Types-only imports use `import type`.
- **No URL hand-building:** the app never constructs sportsbook URLs — it opens the server-built `deeplink.url` and falls back to `deeplink.copy_text`. (The backend owns link tiering; the app owns the open + the honest label gate.)

---

### Task 1: Scaffold the Expo SDK 56 TypeScript app + test harness

**Files:**
- Create: `app/` (Expo project), `app/app.json`, `app/eas.json`, `app/tsconfig.json`, `app/package.json` (jest block), `app/src/smoke.test.ts`

This task produces a buildable, test-running skeleton. Not TDD (there's no behavior yet) — it ends with one trivial passing test proving the jest-expo harness works.

- [ ] **Step 1: Scaffold the project (SDK 56 must be pinned)**

From the repo root (`/Users/taejunoh/Desktop/LFG/kalshi-arb`):

```bash
npx create-expo-app@latest app --template blank-typescript@sdk-56
cd app
```

> Gotcha: omitting `@sdk-56` currently scaffolds SDK 54 (Expo Go compatibility). The `@sdk-56` pin is required for the latest stable. `blank-typescript` gives a minimal TS app with **no** navigation preconfigured (correct — we add React Navigation ourselves; the `default` template ships Expo Router, which we do not use).

- [ ] **Step 2: Install dependencies**

```bash
# Native / Expo modules — let the SDK pin versions:
npx expo install react-native-screens react-native-safe-area-context \
  expo-secure-store expo-linking expo-clipboard expo-notifications expo-constants expo-device
# React Navigation core (plain npm):
npm install @react-navigation/native @react-navigation/native-stack @react-navigation/bottom-tabs
# Test + dev deps:
npx expo install jest-expo --dev
npm install --save-dev jest@^30.4.2 @testing-library/react-native@^14.0.0 @types/jest@^30.0.0
```

Expected resulting `dependencies` (approx, June 2026 — `expo install` is authoritative): `expo ~56.0.0`, `react 19.2.3`, `react-native 0.85.2`, `@react-navigation/native ^7.3.1`, `@react-navigation/native-stack ^7.17.3`, `@react-navigation/bottom-tabs ^7.18.0`, `react-native-screens ~4.x`, `react-native-safe-area-context ~5.x`, `expo-secure-store 56.x`, `expo-linking 56.x`, `expo-clipboard 56.x`, `expo-notifications ~0.32.x`, `expo-constants ~18.x`, `expo-device ~8.x`.

- [ ] **Step 3: Write `app/app.json`** (replace the generated file)

```json
{
  "expo": {
    "name": "Softbook +EV",
    "slug": "softbook-ev",
    "version": "1.0.0",
    "scheme": "softbook",
    "orientation": "portrait",
    "platforms": ["ios"],
    "userInterfaceStyle": "dark",
    "ios": {
      "bundleIdentifier": "com.taejun.softbookev",
      "buildNumber": "1",
      "supportsTablet": false,
      "infoPlist": {
        "ITSAppUsesNonExemptEncryption": false,
        "LSApplicationQueriesSchemes": ["dksb", "fanduel", "draftkings"]
      }
    },
    "plugins": [
      ["expo-notifications", { "color": "#7C3AED" }],
      "expo-secure-store"
    ],
    "extra": {
      "eas": { "projectId": "FILLED_BY_EAS_INIT" }
    }
  }
}
```

> `LSApplicationQueriesSchemes` lists scheme **names only** (no `://`); it is required for `canOpenURL('dksb://…')` not to throw on iOS. Do **not** add `newArchEnabled` (removed in SDK 55 — New Architecture is always on). `aps-environment` is auto-injected by the `expo-notifications` plugin at build time.

- [ ] **Step 4: Write `app/eas.json`**

```json
{
  "cli": { "version": ">= 16.0.0", "appVersionSource": "remote" },
  "build": {
    "development": { "developmentClient": true, "distribution": "internal", "ios": { "simulator": false } },
    "preview": { "distribution": "internal", "ios": { "resourceClass": "m-medium" } },
    "production": { "distribution": "store", "autoIncrement": true, "ios": { "resourceClass": "m-medium" } }
  },
  "submit": {
    "production": { "ios": { "appleId": "REPLACE_APPLE_ID", "ascAppId": "REPLACE_ASC_APP_ID", "appleTeamId": "REPLACE_TEAM_ID" } }
  }
}
```

> `production` (`distribution:"store"`) is the TestFlight path via `eas submit`; `preview` (`distribution:"internal"`) is ad-hoc UDID device installs; `development` is the dev client used to test push on device. The `REPLACE_*` submit fields are filled by the human in Task 13 from App Store Connect.

- [ ] **Step 5: Write `app/tsconfig.json`**

```json
{
  "extends": "expo/tsconfig.base",
  "compilerOptions": { "strict": true },
  "include": ["**/*.ts", "**/*.tsx", ".expo/types/**/*.ts", "expo-env.d.ts"]
}
```

- [ ] **Step 6: Add the jest config + test script to `app/package.json`**

Add (merge with the generated `package.json`):

```json
{
  "scripts": {
    "start": "expo start",
    "test": "jest"
  },
  "jest": {
    "preset": "jest-expo",
    "transformIgnorePatterns": [
      "node_modules/(?!((jest-)?react-native|@react-native(-community)?)|expo(nent)?|@expo(nent)?/.*|@expo-google-fonts/.*|react-navigation|@react-navigation/.*|@sentry/react-native|native-base|react-native-svg)"
    ]
  }
}
```

> Do **not** install `react-native-test-renderer`/`react-test-renderer` — RNTL 14 bundles its own React 19-compatible renderer, and `react-test-renderer` breaks on React 19.

- [ ] **Step 7: Write the harness smoke test**

```typescript
// app/src/smoke.test.ts
// Proves the jest-expo harness runs TS in this repo before any real modules exist.
describe("test harness", () => {
  it("runs", () => {
    expect(1 + 1).toBe(2);
  });
});
```

- [ ] **Step 8: Run it to verify the harness works**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/smoke.test.ts --watchAll=false`
Expected: PASS (1 test). If you see "Cannot use import statement outside a module", the `transformIgnorePatterns` is wrong for your package manager (this one is for npm).

- [ ] **Step 9: Initialize the EAS project (human-assisted)**

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx eas init
```

This writes the real UUID into `app.json` → `extra.eas.projectId` (replacing `FILLED_BY_EAS_INIT`). Requires `npx eas login` (Prerequisite 2). If EAS isn't set up yet, leave the placeholder and revisit before Task 13 — the app builds and tests pass without it, but push-token registration (Task 6/13) needs it on device.

- [ ] **Step 10: Commit**

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
git add app/app.json app/eas.json app/tsconfig.json app/package.json app/package-lock.json app/src/smoke.test.ts app/.gitignore app/App.tsx app/index.ts app/assets
git commit -m "feat(softbook-app): scaffold Expo SDK 56 iOS app + jest-expo harness"
```

> Keep `app/node_modules` ignored (the generated `app/.gitignore` already does). Add `app/.expo` to `.gitignore` if not present.

---

### Task 2: Secure-store credential vault

**Files:**
- Create: `app/src/storage.ts`
- Test: `app/src/storage.test.ts`

Holds the four runtime credentials in the iOS Keychain. The api client (Task 4) reads `getCredentials()`; the Settings screen (Task 12) writes them. Device-only accessibility (not synced to iCloud backups).

- [ ] **Step 1: Write the failing test**

```typescript
// app/src/storage.test.ts
jest.mock("expo-secure-store", () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: "whenUnlockedThisDeviceOnly",
  setItemAsync: jest.fn(async () => undefined),
  getItemAsync: jest.fn(async () => null),
  deleteItemAsync: jest.fn(async () => undefined),
}));

import * as SecureStore from "expo-secure-store";
import { saveCredentials, getCredentials, clearCredentials } from "./storage";

afterEach(() => jest.clearAllMocks());

const CREDS = {
  baseUrl: "https://api.example.com/",
  token: "tok",
  cfId: "cfid",
  cfSecret: "cfsecret",
};

test("saveCredentials writes all four keys and strips a trailing slash on baseUrl", async () => {
  await saveCredentials(CREDS);
  const calls = (SecureStore.setItemAsync as jest.Mock).mock.calls;
  expect(calls).toHaveLength(4);
  const base = calls.find((c) => c[0] === "api_base_url");
  expect(base?.[1]).toBe("https://api.example.com"); // no trailing slash
});

test("getCredentials returns null when any key is missing", async () => {
  (SecureStore.getItemAsync as jest.Mock).mockResolvedValue(null);
  expect(await getCredentials()).toBeNull();
});

test("getCredentials returns the bundle when all keys present", async () => {
  (SecureStore.getItemAsync as jest.Mock).mockImplementation(async (k: string) =>
    ({ api_base_url: "https://api.example.com", app_api_token: "tok", cf_access_client_id: "cfid", cf_access_client_secret: "cfsecret" } as Record<string, string>)[k] ?? null,
  );
  expect(await getCredentials()).toEqual(CREDS && { ...CREDS, baseUrl: "https://api.example.com" });
});

test("clearCredentials deletes all four keys", async () => {
  await clearCredentials();
  expect((SecureStore.deleteItemAsync as jest.Mock).mock.calls.map((c) => c[0]).sort()).toEqual(
    ["api_base_url", "app_api_token", "cf_access_client_id", "cf_access_client_secret"],
  );
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/storage.test.ts --watchAll=false`
Expected: FAIL — `Cannot find module './storage'`.

- [ ] **Step 3: Write minimal implementation**

```typescript
// app/src/storage.ts
import * as SecureStore from "expo-secure-store";

export type Credentials = { baseUrl: string; token: string; cfId: string; cfSecret: string };

// Keys must be [A-Za-z0-9._-] only (SecureStore constraint).
const KEYS = {
  baseUrl: "api_base_url",
  token: "app_api_token",
  cfId: "cf_access_client_id",
  cfSecret: "cf_access_client_secret",
} as const;

const OPTS = { keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY };

export async function saveCredentials(c: Credentials): Promise<void> {
  const baseUrl = c.baseUrl.replace(/\/+$/, "");
  await Promise.all([
    SecureStore.setItemAsync(KEYS.baseUrl, baseUrl, OPTS),
    SecureStore.setItemAsync(KEYS.token, c.token, OPTS),
    SecureStore.setItemAsync(KEYS.cfId, c.cfId, OPTS),
    SecureStore.setItemAsync(KEYS.cfSecret, c.cfSecret, OPTS),
  ]);
}

export async function getCredentials(): Promise<Credentials | null> {
  const [baseUrl, token, cfId, cfSecret] = await Promise.all([
    SecureStore.getItemAsync(KEYS.baseUrl),
    SecureStore.getItemAsync(KEYS.token),
    SecureStore.getItemAsync(KEYS.cfId),
    SecureStore.getItemAsync(KEYS.cfSecret),
  ]);
  if (!baseUrl || !token || !cfId || !cfSecret) return null;
  return { baseUrl, token, cfId, cfSecret };
}

export async function clearCredentials(): Promise<void> {
  await Promise.all(Object.values(KEYS).map((k) => SecureStore.deleteItemAsync(k)));
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/storage.test.ts --watchAll=false`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
git add app/src/storage.ts app/src/storage.test.ts
git commit -m "feat(softbook-app): secure-store credential vault"
```

---

### Task 3: Pure formatters

**Files:**
- Create: `app/src/format.ts`
- Test: `app/src/format.test.ts`

Display helpers used by every screen. Pure functions — fully TDD. The backend already supplies human labels (`pick.label`, `deeplink.copy_text`), so `format` only handles numbers, time, and the live-progress string.

- [ ] **Step 1: Write the failing test**

```typescript
// app/src/format.test.ts
import { decimalOdds, evPct, pnl, countdown, liveProgressLabel } from "./format";

test("decimalOdds formats to 2 dp", () => {
  expect(decimalOdds(2.2)).toBe("2.20");
  expect(decimalOdds(null)).toBe("—");
});

test("evPct shows a signed percentage", () => {
  expect(evPct(0.074)).toBe("+7.4%");
  expect(evPct(-0.012)).toBe("-1.2%");
  expect(evPct(null)).toBe("—");
});

test("pnl shows a signed 2-dp dollar amount", () => {
  expect(pnl(2.4)).toBe("+2.40");
  expect(pnl(-1)).toBe("-1.00");
  expect(pnl(0)).toBe("+0.00");
});

test("countdown renders relative time from now", () => {
  const now = new Date("2026-06-13T18:00:00Z");
  expect(countdown("2026-06-13T20:10:00Z", now)).toBe("2h 10m");
  expect(countdown("2026-06-13T18:00:30Z", now)).toBe("<1m");
  expect(countdown("2026-06-13T17:00:00Z", now)).toBe("LIVE");
});

test("liveProgressLabel composes current/needed + game state", () => {
  expect(liveProgressLabel({ current: 1, needed: 1.5, remaining: 1, game_state: "Mid 2nd" })).toBe("1 / 1.5 · Mid 2nd");
  expect(liveProgressLabel({ current: null, needed: 1.5, game_state: null })).toBe("— / 1.5");
  expect(liveProgressLabel(null)).toBe("");
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/format.test.ts --watchAll=false`
Expected: FAIL — `Cannot find module './format'`.

- [ ] **Step 3: Write minimal implementation**

```typescript
// app/src/format.ts
import type { LiveProgress } from "./api/types";

export function decimalOdds(px: number | null | undefined): string {
  return px == null ? "—" : px.toFixed(2);
}

export function evPct(ev: number | null | undefined): string {
  if (ev == null) return "—";
  return `${ev >= 0 ? "+" : "-"}${Math.abs(ev * 100).toFixed(1)}%`;
}

export function pnl(n: number | null | undefined): string {
  const v = n ?? 0;
  return `${v >= 0 ? "+" : "-"}${Math.abs(v).toFixed(2)}`;
}

export function countdown(commenceISO: string, now: Date = new Date()): string {
  const start = new Date(commenceISO).getTime();
  const diffMs = start - now.getTime();
  if (diffMs <= 0) return "LIVE";
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "<1m";
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

export function liveProgressLabel(lp: LiveProgress | null | undefined): string {
  if (!lp) return "";
  const cur = lp.current == null ? "—" : String(lp.current);
  const head = `${cur} / ${lp.needed}`;
  return lp.game_state ? `${head} · ${lp.game_state}` : head;
}
```

> `format.ts` imports `LiveProgress` from `./api/types`, created in Task 4. Until then this test fails on the missing type import — that is the expected RED in Step 2. If you run Task 3 strictly before Task 4, temporarily inline the type; the plan orders Task 4's `types.ts` to land right after, and the import resolves. (Cleaner: do Step 1–2 of Task 4 first to create `types.ts`, then this task. Either order converges.)

- [ ] **Step 4: Run tests to verify they pass** (after Task 4's `types.ts` exists)

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/format.test.ts --watchAll=false`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
git add app/src/format.ts app/src/format.test.ts
git commit -m "feat(softbook-app): pure formatters (odds, ev%, pnl, countdown, live progress)"
```

---

### Task 4: API types + authenticated client

**Files:**
- Create: `app/src/api/types.ts`, `app/src/api/client.ts`
- Test: `app/src/api/client.test.ts`

`types.ts` mirrors the API contract exactly (see "The API contract this app consumes"). `client.ts` is a `fetch` wrapper that pulls credentials from `storage` and injects the three auth headers on every call, with one method per endpoint. (Do Step 1's `types.ts` first — Task 3's `format.ts` imports `LiveProgress` from it.)

- [ ] **Step 1: Write `app/src/api/types.ts`** (no test — pure type declarations)

```typescript
// app/src/api/types.ts
export type PickStatus = "active" | "cooled" | "expired" | "bet" | "dismissed";

export interface Pick {
  id: string; // "event_id|market|side|point|player"
  status: PickStatus;
  event_id: string;
  sport: string;
  home: string | null;
  away: string | null;
  commence_time: string;
  market: string;
  side: string;
  point: number | null;
  player: string | null;
  label: string;
  ev: number | null;
  book: string | null;
  odds: number | null;
  conf: number | null;
  n_books: number | null;
}

export interface PicksResponse {
  picks: Pick[];
  counts: Record<string, number>;
  cooled: Pick[];
}

export type DeeplinkTier = "prefill" | "event" | "home";
export interface Deeplink {
  tier: DeeplinkTier;
  url: string | null;
  copy_text: string;
}

export interface BookQuote {
  book: string;
  odds: number;
  ev: number | null;
  deeplink: Deeplink;
}

export interface RefreshResponse {
  fair: number | null;
  books: BookQuote[];
  credits: number;
}

export interface BetIn {
  event_id: string;
  sport?: string | null;
  home?: string | null;
  away?: string | null;
  commence_time?: string | null;
  market: string;
  side: string;
  point?: number | null;
  player?: string | null;
  label?: string | null;
  book?: string | null;
  rec_odds?: number | null;
  fill_odds?: number | null;
  stake?: number | null;
  source: string;
}

export interface LiveProgress {
  current: number | null;
  needed: number;
  remaining?: number;
  game_state: string | null;
}

export interface OpenBet {
  id: number;
  event_id: string;
  sport: string | null;
  home: string | null;
  away: string | null;
  commence_time: string | null;
  market: string;
  side: string;
  point: number | null;
  player: string | null;
  label: string | null;
  book: string | null;
  fill_odds: number | null;
  stake: number | null;
  live_status: string | null;
  live_progress: LiveProgress | null;
}

export interface MyBets {
  calendar: Record<string, number>;
  open: OpenBet[];
}

export type SortKey = "ev" | "time" | "n_books";
export interface Settings {
  min_ev: number;
  sports: string[] | null;
  muted_books: string[] | null;
  sort: SortKey;
}
```

- [ ] **Step 2: Write the failing client test**

```typescript
// app/src/api/client.test.ts
jest.mock("../storage", () => ({
  getCredentials: jest.fn(async () => ({
    baseUrl: "https://api.example.com",
    token: "tok",
    cfId: "cfid",
    cfSecret: "cfsecret",
  })),
}));

import { api, ApiError } from "./client";
import { getCredentials } from "../storage";

function mockFetch(body: unknown, ok = true, status = 200) {
  return jest.fn(async () => ({ ok, status, json: async () => body })) as unknown as typeof fetch;
}

afterEach(() => jest.clearAllMocks());

test("getPicks hits /picks with the three auth headers", async () => {
  const f = mockFetch({ picks: [], counts: {}, cooled: [] });
  global.fetch = f;
  await api.getPicks();
  const [url, init] = (f as jest.Mock).mock.calls[0];
  expect(url).toBe("https://api.example.com/picks");
  expect(init.headers.Authorization).toBe("Bearer tok");
  expect(init.headers["CF-Access-Client-Id"]).toBe("cfid");
  expect(init.headers["CF-Access-Client-Secret"]).toBe("cfsecret");
});

test("refreshPick URL-encodes the pipe-and-space pick id", async () => {
  const f = mockFetch({ fair: null, books: [], credits: 0 });
  global.fetch = f;
  await api.refreshPick("E1|batter_total_bases|Over|1.5|Brandon Nimmo");
  const [url] = (f as jest.Mock).mock.calls[0];
  expect(url).toBe(
    "https://api.example.com/picks/E1%7Cbatter_total_bases%7COver%7C1.5%7CBrandon%20Nimmo/refresh",
  );
});

test("postBet sends a JSON body and POST method", async () => {
  const f = mockFetch({ id: 7 });
  global.fetch = f;
  const out = await api.postBet({ event_id: "E1", market: "h2h", side: "NYM", source: "one_tap" });
  expect(out.id).toBe(7);
  const [, init] = (f as jest.Mock).mock.calls[0];
  expect(init.method).toBe("POST");
  expect(JSON.parse(init.body).event_id).toBe("E1");
});

test("a non-ok response throws ApiError with the status", async () => {
  global.fetch = mockFetch({}, false, 401);
  await expect(api.getPicks()).rejects.toBeInstanceOf(ApiError);
  await expect(api.getPicks()).rejects.toMatchObject({ status: 401 });
});

test("missing credentials throws before fetch", async () => {
  (getCredentials as jest.Mock).mockResolvedValueOnce(null);
  const f = mockFetch({});
  global.fetch = f;
  await expect(api.getPicks()).rejects.toBeInstanceOf(ApiError);
  expect((f as jest.Mock).mock.calls).toHaveLength(0);
});
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/api/client.test.ts --watchAll=false`
Expected: FAIL — `Cannot find module './client'`.

- [ ] **Step 4: Write minimal implementation**

```typescript
// app/src/api/client.ts
import { getCredentials } from "../storage";
import type { BetIn, MyBets, Pick, PicksResponse, RefreshResponse, Settings } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const c = await getCredentials();
  if (!c) throw new ApiError(0, "No API credentials configured");
  const res = await fetch(c.baseUrl + path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${c.token}`,
      "CF-Access-Client-Id": c.cfId,
      "CF-Access-Client-Secret": c.cfSecret,
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) throw new ApiError(res.status, `${init?.method ?? "GET"} ${path} -> ${res.status}`);
  return (await res.json()) as T;
}

const enc = encodeURIComponent;

export const api = {
  getPicks: () => request<PicksResponse>("/picks"),
  getPick: (id: string) => request<Pick>(`/picks/${enc(id)}`),
  refreshPick: (id: string) => request<RefreshResponse>(`/picks/${enc(id)}/refresh`),
  postBet: (bet: BetIn) => request<{ id: number }>("/bets", { method: "POST", body: JSON.stringify(bet) }),
  dismissPick: (id: string) => request<{ ok: boolean }>(`/picks/${enc(id)}/dismiss`, { method: "POST" }),
  getMyBets: () => request<MyBets>("/mybets"),
  getSettings: () => request<Settings>("/settings"),
  putSettings: (s: Settings) => request<{ ok: boolean }>("/settings", { method: "PUT", body: JSON.stringify(s) }),
  registerPush: (token: string) =>
    request<{ ok: boolean }>("/push/register", { method: "POST", body: JSON.stringify({ token }) }),
};
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/api/client.test.ts --watchAll=false`
Expected: PASS (5 tests). Now also re-run Task 3: `npx jest src/format.test.ts --watchAll=false` → PASS (the `LiveProgress` import resolves).

- [ ] **Step 6: Commit**

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
git add app/src/api/types.ts app/src/api/client.ts app/src/api/client.test.ts
git commit -m "feat(softbook-app): API types + authenticated fetch client"
```

---

### Task 5: Config + deep-link open

**Files:**
- Create: `app/src/config.ts`, `app/src/deeplink.ts`
- Test: `app/src/deeplink.test.ts`

`config.ts` holds app constants (notification category/action ids that **must match the backend's `high_pick`/`BET_PLACED`/`NOT_INTERESTED`**, sport labels, the canonical book list, the verified-prefill allowlist, default settings). `deeplink.ts` opens the server-built link with the honest fallback chain and gates the "✓ 슬립 프리필" label.

- [ ] **Step 1: Write `app/src/config.ts`**

```typescript
// app/src/config.ts
import type { Settings } from "./api/types";

// Must match scripts/softbook_push_worker.py categories + the buttons this app registers.
export const CATEGORY_HIGH = "high_pick";
export const CATEGORY_SETTLE = "settle";
export const ACTION_BET = "BET_PLACED";
export const ACTION_DISMISS = "NOT_INTERESTED";

// Canonical NY book labels (from core.NY_LEGAL values) — used by the mute UI.
export const ALL_BOOKS = [
  "FanDuel", "DraftKings", "BetMGM", "Caesars", "BetRivers", "Fanatics", "theScore", "BallyBet",
];

export const SPORT_LABELS: Record<string, string> = {
  baseball_mlb: "MLB",
  basketball_wnba: "WNBA",
  basketball_nba: "NBA",
};

// Books whose bet-slip PREFILL has been verified on the real NY iPhone (app installed)
// to actually populate the slip. EMPTY until verified — see DEEPLINK_VERIFICATION.md.
// Until a book label is in here, even a server "prefill"-tier link is shown as plain
// "앱 열기", never "✓ 슬립 프리필". (Research: FanDuel addToBetslip is confirmed-but-best-effort;
// DraftKings dksb:// is UNVERIFIED by any source.)
export const VERIFIED_PREFILL_BOOKS: string[] = [];

export const DEFAULT_SETTINGS: Settings = { min_ev: 0, sports: null, muted_books: null, sort: "ev" };

// EV thresholds for the filter stepper (matches the pipeline's 1–8% band).
export const MIN_EV_STEPS = [0, 0.01, 0.02, 0.03, 0.04, 0.05];
```

- [ ] **Step 2: Write the failing deep-link test**

```typescript
// app/src/deeplink.test.ts
jest.mock("expo-linking", () => ({
  canOpenURL: jest.fn(),
  openURL: jest.fn(async () => true),
}));
jest.mock("expo-clipboard", () => ({ setStringAsync: jest.fn(async () => true) }));
jest.mock("./config", () => ({ VERIFIED_PREFILL_BOOKS: ["FanDuel"] }));

import * as Linking from "expo-linking";
import * as Clipboard from "expo-clipboard";
import { openOrCopy, isVerifiedPrefill } from "./deeplink";
import type { Deeplink } from "./api/types";

afterEach(() => jest.clearAllMocks());

const COPY = "FanDuel · Nimmo TB Over 1.5 @ 2.20";

test("https link opens directly without canOpenURL", async () => {
  const dl: Deeplink = { tier: "prefill", url: "https://sportsbook.fanduel.com/addToBetslip?x=1", copy_text: COPY };
  const r = await openOrCopy(dl);
  expect(r).toBe("opened");
  expect(Linking.canOpenURL).not.toHaveBeenCalled();
  expect(Linking.openURL).toHaveBeenCalledWith(dl.url);
});

test("custom scheme checks canOpenURL then opens", async () => {
  (Linking.canOpenURL as jest.Mock).mockResolvedValue(true);
  const r = await openOrCopy({ tier: "prefill", url: "dksb://sb/addbet/S1", copy_text: COPY });
  expect(r).toBe("opened");
  expect(Linking.canOpenURL).toHaveBeenCalled();
});

test("custom scheme that throws canOpenURL falls back to clipboard", async () => {
  (Linking.canOpenURL as jest.Mock).mockRejectedValue(new Error("not whitelisted"));
  const r = await openOrCopy({ tier: "home", url: "dksb://sb/addbet/S1", copy_text: COPY });
  expect(r).toBe("copied");
  expect(Clipboard.setStringAsync).toHaveBeenCalledWith(COPY);
});

test("null url copies the text", async () => {
  const r = await openOrCopy({ tier: "home", url: null, copy_text: COPY });
  expect(r).toBe("copied");
  expect(Clipboard.setStringAsync).toHaveBeenCalledWith(COPY);
});

test("isVerifiedPrefill is true only for prefill tier on an allowlisted book", () => {
  const pre: Deeplink = { tier: "prefill", url: "x", copy_text: COPY };
  expect(isVerifiedPrefill("FanDuel", pre)).toBe(true);
  expect(isVerifiedPrefill("DraftKings", pre)).toBe(false); // not allowlisted
  expect(isVerifiedPrefill("FanDuel", { ...pre, tier: "event" })).toBe(false);
});
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/deeplink.test.ts --watchAll=false`
Expected: FAIL — `Cannot find module './deeplink'`.

- [ ] **Step 4: Write minimal implementation**

```typescript
// app/src/deeplink.ts
import * as Linking from "expo-linking";
import * as Clipboard from "expo-clipboard";
import type { Deeplink } from "./api/types";
import { VERIFIED_PREFILL_BOOKS } from "./config";

export function isVerifiedPrefill(book: string, dl: Deeplink): boolean {
  return dl.tier === "prefill" && VERIFIED_PREFILL_BOOKS.includes(book);
}

/** Open the server-built link via the system handler, falling back to clipboard.
 *  https universal links are always openable; custom schemes (dksb://) must pass
 *  canOpenURL, which THROWS on iOS when the scheme isn't whitelisted — treat a
 *  throw as "not installed" and copy instead. Never a dead end. */
export async function openOrCopy(dl: Deeplink): Promise<"opened" | "copied"> {
  const { url, copy_text } = dl;
  if (url) {
    const isHttp = url.startsWith("http://") || url.startsWith("https://");
    let canOpen = isHttp;
    if (!isHttp) {
      try {
        canOpen = await Linking.canOpenURL(url);
      } catch {
        canOpen = false;
      }
    }
    if (canOpen) {
      try {
        await Linking.openURL(url);
        return "opened";
      } catch {
        /* fall through to clipboard */
      }
    }
  }
  await Clipboard.setStringAsync(copy_text);
  return "copied";
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/deeplink.test.ts --watchAll=false`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
git add app/src/config.ts app/src/deeplink.ts app/src/deeplink.test.ts
git commit -m "feat(softbook-app): config constants + tiered deep-link open with clipboard fallback"
```

---

### Task 6: Push registration + notification routing

**Files:**
- Create: `app/src/push.ts`
- Test: `app/src/push.test.ts`

Three responsibilities: (1) `registerForPush()` — iOS permission flow + Expo push token; (2) `registerCategories()` — attach the `베팅함`/`관심없음` buttons to the `high_pick` category the backend already targets; (3) `notifTarget(response)` — a **pure** mapper from a tapped notification (or action button) to an in-app navigation intent, unit-tested in isolation. The side-effecting launch wiring is Task 13.

- [ ] **Step 1: Write the failing test**

```typescript
// app/src/push.test.ts
jest.mock("expo-notifications", () => ({
  DEFAULT_ACTION_IDENTIFIER: "expo.modules.notifications.actions.DEFAULT",
  getPermissionsAsync: jest.fn(async () => ({ status: "granted" })),
  requestPermissionsAsync: jest.fn(async () => ({ status: "granted" })),
  getExpoPushTokenAsync: jest.fn(async () => ({ data: "ExponentPushToken[abc]" })),
  setNotificationCategoryAsync: jest.fn(async () => undefined),
}));
jest.mock("expo-device", () => ({ isDevice: true }));
jest.mock("expo-constants", () => ({ expoConfig: { extra: { eas: { projectId: "uuid-1" } } } }));

import * as Notifications from "expo-notifications";
import { registerForPush, registerCategories, notifTarget } from "./push";
import { ACTION_BET, ACTION_DISMISS } from "./config";

afterEach(() => jest.clearAllMocks());

function resp(data: Record<string, unknown>, actionIdentifier: string) {
  return {
    actionIdentifier,
    notification: { request: { content: { data } } },
  } as unknown as Notifications.NotificationResponse;
}

test("registerForPush returns the Expo token on a granted device", async () => {
  expect(await registerForPush()).toBe("ExponentPushToken[abc]");
  expect(Notifications.getExpoPushTokenAsync).toHaveBeenCalledWith({ projectId: "uuid-1" });
});

test("registerForPush returns null when permission denied", async () => {
  (Notifications.getPermissionsAsync as jest.Mock).mockResolvedValueOnce({ status: "denied" });
  (Notifications.requestPermissionsAsync as jest.Mock).mockResolvedValueOnce({ status: "denied" });
  expect(await registerForPush()).toBeNull();
});

test("registerCategories registers high_pick with two actions", async () => {
  await registerCategories();
  const [id, actions] = (Notifications.setNotificationCategoryAsync as jest.Mock).mock.calls[0];
  expect(id).toBe("high_pick");
  expect(actions.map((a: { identifier: string }) => a.identifier)).toEqual([ACTION_BET, ACTION_DISMISS]);
  const dismiss = actions.find((a: { identifier: string }) => a.identifier === ACTION_DISMISS);
  expect(dismiss.options.opensAppToForeground).toBe(false);
});

test("notifTarget maps the BET button to a capture intent", () => {
  expect(notifTarget(resp({ pick_id: "E1|h2h|NYM|0|" }, ACTION_BET))).toEqual({ kind: "capture", pickId: "E1|h2h|NYM|0|" });
});

test("notifTarget maps the DISMISS button to a dismiss intent", () => {
  expect(notifTarget(resp({ pick_id: "E1|h2h|NYM|0|" }, ACTION_DISMISS))).toEqual({ kind: "dismiss", pickId: "E1|h2h|NYM|0|" });
});

test("notifTarget maps a body tap with pick_id to pickDetail", () => {
  expect(notifTarget(resp({ pick_id: "E1|h2h|NYM|0|" }, Notifications.DEFAULT_ACTION_IDENTIFIER))).toEqual({
    kind: "pickDetail",
    pickId: "E1|h2h|NYM|0|",
  });
});

test("notifTarget maps a settle notification to mybets", () => {
  expect(notifTarget(resp({ app_bet_id: 12 }, Notifications.DEFAULT_ACTION_IDENTIFIER))).toEqual({ kind: "mybets", appBetId: 12 });
});

test("notifTarget returns null for an unrecognized payload", () => {
  expect(notifTarget(resp({}, Notifications.DEFAULT_ACTION_IDENTIFIER))).toBeNull();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/push.test.ts --watchAll=false`
Expected: FAIL — `Cannot find module './push'`.

- [ ] **Step 3: Write minimal implementation**

```typescript
// app/src/push.ts
import * as Notifications from "expo-notifications";
import * as Device from "expo-device";
import Constants from "expo-constants";
import { ACTION_BET, ACTION_DISMISS, CATEGORY_HIGH } from "./config";

export type NotifTarget =
  | { kind: "capture"; pickId: string }
  | { kind: "dismiss"; pickId: string }
  | { kind: "pickDetail"; pickId: string }
  | { kind: "mybets"; appBetId: number }
  | null;

/** Pure: tapped notification / action button -> in-app intent. */
export function notifTarget(response: Notifications.NotificationResponse): NotifTarget {
  const data = (response.notification.request.content.data ?? {}) as {
    pick_id?: string;
    app_bet_id?: number;
  };
  const action = response.actionIdentifier;
  if (action === ACTION_BET && data.pick_id) return { kind: "capture", pickId: data.pick_id };
  if (action === ACTION_DISMISS && data.pick_id) return { kind: "dismiss", pickId: data.pick_id };
  if (data.pick_id) return { kind: "pickDetail", pickId: data.pick_id };
  if (data.app_bet_id != null) return { kind: "mybets", appBetId: data.app_bet_id };
  return null;
}

/** Attach the 베팅함 / 관심없음 buttons to the backend's `high_pick` category. */
export async function registerCategories(): Promise<void> {
  await Notifications.setNotificationCategoryAsync(CATEGORY_HIGH, [
    { identifier: ACTION_BET, buttonTitle: "베팅함", options: { opensAppToForeground: true } },
    { identifier: ACTION_DISMISS, buttonTitle: "관심없음", options: { opensAppToForeground: false, isDestructive: true } },
  ]);
}

/** iOS permission flow + Expo push token. Returns null on simulator / denied / no projectId. */
export async function registerForPush(): Promise<string | null> {
  if (!Device.isDevice) return null;
  const { status: existing } = await Notifications.getPermissionsAsync();
  let status = existing;
  if (existing !== "granted") status = (await Notifications.requestPermissionsAsync()).status;
  if (status !== "granted") return null;
  const projectId =
    Constants?.expoConfig?.extra?.eas?.projectId ??
    (Constants as unknown as { easConfig?: { projectId?: string } })?.easConfig?.projectId;
  if (!projectId) return null;
  const token = (await Notifications.getExpoPushTokenAsync({ projectId })).data;
  return token;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/push.test.ts --watchAll=false`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
git add app/src/push.ts app/src/push.test.ts
git commit -m "feat(softbook-app): push registration + categories + pure notification router"
```

---

### Task 7: Navigation shell + app root

**Files:**
- Create: `app/src/navigation/types.ts`, `app/src/navigation/index.tsx`
- Modify: `app/App.tsx`
- Test: `app/src/navigation/types.test.ts`

A 3-tab bottom navigator (픽 / 내 베팅 / 설정) where the 픽 tab is its own native stack (`PicksList` → `PickDetail` → `CaptureSheet`). A module-level `navigationRef` lets Task 13 route notification taps imperatively (the backend sends `data.pick_id`, not a URL, so we route in code rather than via a linking config). This task wires placeholder screens so the shell renders; Tasks 8–12 replace each placeholder.

- [ ] **Step 1: Write `app/src/navigation/types.ts`**

```typescript
// app/src/navigation/types.ts
import type { NavigatorScreenParams } from "@react-navigation/native";

export type PicksStackParamList = {
  PicksList: undefined;
  PickDetail: { pickId: string };
  CaptureSheet: { pickId: string };
};

export type TabParamList = {
  Picks: NavigatorScreenParams<PicksStackParamList> | undefined;
  MyBets: undefined;
  Settings: undefined;
};

export type RootParamList = {
  Tabs: NavigatorScreenParams<TabParamList>;
};
```

- [ ] **Step 2: Write the failing test** (a pure check that the navigationRef + helpers exist and the param-list deep-link path is well-formed)

```typescript
// app/src/navigation/types.test.ts
import { navigationRef, captureRoute, pickDetailRoute } from "./index";

test("navigationRef is created", () => {
  expect(navigationRef).toBeDefined();
  expect(typeof navigationRef.navigate).toBe("function");
});

test("captureRoute builds a nested Tabs>Picks>CaptureSheet intent", () => {
  expect(captureRoute("E1|h2h|NYM|0|")).toEqual([
    "Tabs",
    { screen: "Picks", params: { screen: "CaptureSheet", params: { pickId: "E1|h2h|NYM|0|" } } },
  ]);
});

test("pickDetailRoute builds a nested Tabs>Picks>PickDetail intent", () => {
  expect(pickDetailRoute("E1|h2h|NYM|0|")).toEqual([
    "Tabs",
    { screen: "Picks", params: { screen: "PickDetail", params: { pickId: "E1|h2h|NYM|0|" } } },
  ]);
});
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/navigation/types.test.ts --watchAll=false`
Expected: FAIL — `Cannot find module './index'`.

- [ ] **Step 4: Write `app/src/navigation/index.tsx`**

```tsx
// app/src/navigation/index.tsx
import { createNavigationContainerRef } from "@react-navigation/native";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import type { PicksStackParamList, RootParamList, TabParamList } from "./types";
import { PicksScreen } from "../screens/PicksScreen";
import { PickDetailScreen } from "../screens/PickDetailScreen";
import { CaptureSheet } from "../screens/CaptureSheet";
import { MyBetsScreen } from "../screens/MyBetsScreen";
import { SettingsScreen } from "../screens/SettingsScreen";

export const navigationRef = createNavigationContainerRef<RootParamList>();

// Imperative nested-route builders (used by PushBridge in Task 13). Pure + testable.
export function captureRoute(pickId: string): ["Tabs", object] {
  return ["Tabs", { screen: "Picks", params: { screen: "CaptureSheet", params: { pickId } } }];
}
export function pickDetailRoute(pickId: string): ["Tabs", object] {
  return ["Tabs", { screen: "Picks", params: { screen: "PickDetail", params: { pickId } } }];
}
export function myBetsRoute(): ["Tabs", object] {
  return ["Tabs", { screen: "MyBets" }];
}

const PicksStack = createNativeStackNavigator<PicksStackParamList>();
function PicksNavigator() {
  return (
    <PicksStack.Navigator>
      <PicksStack.Screen name="PicksList" component={PicksScreen} options={{ title: "픽" }} />
      <PicksStack.Screen name="PickDetail" component={PickDetailScreen} options={{ title: "픽 상세" }} />
      <PicksStack.Screen name="CaptureSheet" component={CaptureSheet} options={{ presentation: "modal", title: "베팅 기록" }} />
    </PicksStack.Navigator>
  );
}

const Tabs = createBottomTabNavigator<TabParamList>();
export function RootNavigator() {
  return (
    <Tabs.Navigator screenOptions={{ headerShown: false }}>
      <Tabs.Screen name="Picks" component={PicksNavigator} options={{ title: "픽" }} />
      <Tabs.Screen name="MyBets" component={MyBetsScreen} options={{ title: "내 베팅" }} />
      <Tabs.Screen name="Settings" component={SettingsScreen} options={{ title: "설정" }} />
    </Tabs.Navigator>
  );
}
```

- [ ] **Step 5: Write placeholder screens so the shell compiles**

Create each of these five files with a minimal placeholder (Tasks 8–12 replace them). Example for one — repeat the pattern for `PickDetailScreen`, `CaptureSheet`, `MyBetsScreen`, `SettingsScreen`:

```tsx
// app/src/screens/PicksScreen.tsx  (placeholder — replaced in Task 8)
import { Text, View } from "react-native";
export function PicksScreen() {
  return (
    <View>
      <Text>Picks</Text>
    </View>
  );
}
```

```tsx
// app/src/screens/PickDetailScreen.tsx  (placeholder — replaced in Task 9)
import { Text, View } from "react-native";
export function PickDetailScreen() {
  return (<View><Text>Pick detail</Text></View>);
}
```

```tsx
// app/src/screens/CaptureSheet.tsx  (placeholder — replaced in Task 10)
import { Text, View } from "react-native";
export function CaptureSheet() {
  return (<View><Text>Capture</Text></View>);
}
```

```tsx
// app/src/screens/MyBetsScreen.tsx  (placeholder — replaced in Task 11)
import { Text, View } from "react-native";
export function MyBetsScreen() {
  return (<View><Text>My bets</Text></View>);
}
```

```tsx
// app/src/screens/SettingsScreen.tsx  (placeholder — replaced in Task 12)
import { Text, View } from "react-native";
export function SettingsScreen() {
  return (<View><Text>Settings</Text></View>);
}
```

- [ ] **Step 6: Replace `app/App.tsx`**

```tsx
// app/App.tsx
import { NavigationContainer } from "@react-navigation/native";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { StatusBar } from "expo-status-bar";
import * as Notifications from "expo-notifications";
import { navigationRef, RootNavigator } from "./src/navigation";

// Foreground display (SDK 51+ fields; shouldShowAlert is deprecated).
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  }),
});

export default function App() {
  return (
    <SafeAreaProvider>
      <NavigationContainer ref={navigationRef}>
        <RootNavigator />
      </NavigationContainer>
      <StatusBar style="light" />
    </SafeAreaProvider>
  );
}
```

> `expo-status-bar` ships with the template. `react-native-safe-area-context` was installed in Task 1. The `<PushBridge/>` that registers the token and routes taps is added in Task 13 (it needs the screens to exist first).

- [ ] **Step 7: Run the navigation test + a typecheck**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/navigation/types.test.ts --watchAll=false && npx tsc --noEmit`
Expected: jest PASS (3 tests); `tsc` clean (the shell compiles with placeholders).

- [ ] **Step 8: Commit**

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
git add app/src/navigation app/src/screens app/App.tsx app/src/navigation/types.test.ts
git commit -m "feat(softbook-app): navigation shell (3 tabs + Picks stack) + app root"
```

---

### Task 8: Picks feed screen + card + filter bar

**Files:**
- Create: `app/src/components/PickCard.tsx`
- Modify: `app/src/screens/PicksScreen.tsx` (replace the placeholder)
- Test: `app/src/components/PickCard.test.tsx`, `app/src/screens/PicksScreen.test.tsx`

The feed: fetch `GET /picks` on focus, render active picks as cards (label, best book @ odds, EV, conf · n_books, countdown), each tappable to detail with inline `베팅` (→ Capture) / `관심없음` (→ dismiss) buttons; a collapsed "식은 픽" (cooled) section; a filter bar (min-EV stepper, sport chips, sort) that persists via `PUT /settings` then refetches; pull-to-refresh.

> **UX note (conscious tradeoff):** the spec mentions swipe-to-dismiss. This plan ships **inline action buttons** as the reliable, testable MVP (no `react-native-gesture-handler`/reanimated dependency, no native-gesture flakiness). Swipe is a drop-in enhancement later: `npx expo install react-native-gesture-handler`, wrap the card in a `Swipeable`. Buttons are the tested core so a missed bet is never a gesture misfire — consistent with the user's "not missing bets matters more" priority.

- [ ] **Step 1: Write the failing card test**

```tsx
// app/src/components/PickCard.test.tsx
import { render, screen, fireEvent } from "@testing-library/react-native";
import { PickCard } from "./PickCard";
import type { Pick } from "../api/types";

const PICK: Pick = {
  id: "E1|batter_total_bases|Over|1.5|Brandon Nimmo",
  status: "active",
  event_id: "E1",
  sport: "baseball_mlb",
  home: "MIA",
  away: "NYM",
  commence_time: "2099-01-01T00:00:00Z",
  market: "batter_total_bases",
  side: "Over",
  point: 1.5,
  player: "Brandon Nimmo",
  label: "Brandon Nimmo TB Over 1.5",
  ev: 0.074,
  book: "theScore",
  odds: 2.2,
  conf: 60,
  n_books: 9,
};

test("renders label, book@odds and EV", () => {
  render(<PickCard pick={PICK} onOpen={() => {}} onBet={() => {}} onDismiss={() => {}} />);
  expect(screen.getByText("Brandon Nimmo TB Over 1.5")).toBeOnTheScreen();
  expect(screen.getByText(/theScore/)).toBeOnTheScreen();
  expect(screen.getByText("+7.4%")).toBeOnTheScreen();
});

test("fires onBet and onDismiss", () => {
  const onBet = jest.fn();
  const onDismiss = jest.fn();
  render(<PickCard pick={PICK} onOpen={() => {}} onBet={onBet} onDismiss={onDismiss} />);
  fireEvent.press(screen.getByText("베팅"));
  fireEvent.press(screen.getByText("관심없음"));
  expect(onBet).toHaveBeenCalledWith(PICK);
  expect(onDismiss).toHaveBeenCalledWith(PICK);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/components/PickCard.test.tsx --watchAll=false`
Expected: FAIL — `Cannot find module './PickCard'`.

- [ ] **Step 3: Write `app/src/components/PickCard.tsx`**

```tsx
// app/src/components/PickCard.tsx
import { Pressable, StyleSheet, Text, View } from "react-native";
import type { Pick } from "../api/types";
import { decimalOdds, evPct, countdown } from "../format";

export function PickCard({
  pick,
  onOpen,
  onBet,
  onDismiss,
}: {
  pick: Pick;
  onOpen: (p: Pick) => void;
  onBet: (p: Pick) => void;
  onDismiss: (p: Pick) => void;
}) {
  return (
    <Pressable style={s.card} onPress={() => onOpen(pick)}>
      <View style={s.row}>
        <Text style={s.label} numberOfLines={2}>{pick.label}</Text>
        <Text style={s.ev}>{evPct(pick.ev)}</Text>
      </View>
      <Text style={s.meta}>
        {pick.book ?? "—"} @ {decimalOdds(pick.odds)} · {pick.n_books ?? 0}북 · {countdown(pick.commence_time)}
      </Text>
      <View style={s.actions}>
        <Pressable style={[s.btn, s.bet]} onPress={() => onBet(pick)}><Text style={s.btnText}>베팅</Text></Pressable>
        <Pressable style={[s.btn, s.dismiss]} onPress={() => onDismiss(pick)}><Text style={s.btnText}>관심없음</Text></Pressable>
      </View>
    </Pressable>
  );
}

const s = StyleSheet.create({
  card: { backgroundColor: "#1A1A22", borderRadius: 12, padding: 14, marginVertical: 6, marginHorizontal: 12 },
  row: { flexDirection: "row", justifyContent: "space-between" },
  label: { color: "#FFF", fontSize: 16, fontWeight: "600", flex: 1, paddingRight: 8 },
  ev: { color: "#34D399", fontSize: 16, fontWeight: "700" },
  meta: { color: "#9CA3AF", marginTop: 6, fontSize: 13 },
  actions: { flexDirection: "row", marginTop: 10, gap: 8 },
  btn: { flex: 1, paddingVertical: 8, borderRadius: 8, alignItems: "center" },
  bet: { backgroundColor: "#7C3AED" },
  dismiss: { backgroundColor: "#374151" },
  btnText: { color: "#FFF", fontWeight: "600" },
});
```

- [ ] **Step 4: Write the failing screen test**

```tsx
// app/src/screens/PicksScreen.test.tsx
jest.mock("../api/client", () => ({
  api: {
    getPicks: jest.fn(async () => ({
      picks: [
        {
          id: "E1|h2h|NYM|0|", status: "active", event_id: "E1", sport: "baseball_mlb",
          home: "MIA", away: "NYM", commence_time: "2099-01-01T00:00:00Z", market: "h2h",
          side: "NYM", point: 0, player: "", label: "NYM ML", ev: 0.05, book: "FanDuel",
          odds: 2.1, conf: 55, n_books: 8,
        },
      ],
      counts: { active: 1, cooled: 0 },
      cooled: [],
    })),
    dismissPick: jest.fn(async () => ({ ok: true })),
    putSettings: jest.fn(async () => ({ ok: true })),
    getSettings: jest.fn(async () => ({ min_ev: 0, sports: null, muted_books: null, sort: "ev" })),
  },
}));
jest.mock("@react-navigation/native", () => ({
  useFocusEffect: (cb: () => void) => cb(),
  useNavigation: () => ({ navigate: jest.fn(), push: jest.fn() }),
}));

import { render, screen, waitFor } from "@testing-library/react-native";
import { PicksScreen } from "./PicksScreen";

test("renders the active feed from the API", async () => {
  render(<PicksScreen />);
  await waitFor(() => expect(screen.getByText("NYM ML")).toBeOnTheScreen());
});
```

- [ ] **Step 5: Run it to verify it fails**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/screens/PicksScreen.test.tsx --watchAll=false`
Expected: FAIL — placeholder renders "Picks", not "NYM ML".

- [ ] **Step 6: Replace `app/src/screens/PicksScreen.tsx`**

```tsx
// app/src/screens/PicksScreen.tsx
import { useCallback, useState } from "react";
import { ActivityIndicator, FlatList, Pressable, StyleSheet, Text, View } from "react-native";
import { useFocusEffect, useNavigation } from "@react-navigation/native";
import { api } from "../api/client";
import type { Pick, PicksResponse, SortKey } from "../api/types";
import { PickCard } from "../components/PickCard";
import { MIN_EV_STEPS } from "../config";

export function PicksScreen() {
  const nav = useNavigation<{ push: (s: string, p?: object) => void }>();
  const [data, setData] = useState<PicksResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [minEv, setMinEv] = useState(0);
  const [sort, setSort] = useState<SortKey>("ev");
  const [showCooled, setShowCooled] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.getPicks());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { void load(); }, [load]));

  const applyFilters = useCallback(
    async (next: { min_ev?: number; sort?: SortKey }) => {
      const min_ev = next.min_ev ?? minEv;
      const nextSort = next.sort ?? sort;
      setMinEv(min_ev);
      setSort(nextSort);
      try {
        await api.putSettings({ min_ev, sports: null, muted_books: null, sort: nextSort });
      } catch {
        /* settings sync best-effort; UI already updated */
      }
      void load();
    },
    [minEv, sort, load],
  );

  const dismiss = useCallback(async (p: Pick) => {
    setData((d) => (d ? { ...d, picks: d.picks.filter((x) => x.id !== p.id) } : d)); // optimistic
    try {
      await api.dismissPick(p.id);
    } catch {
      void load(); // revert by reloading on failure
    }
  }, [load]);

  if (error) {
    return (
      <View style={s.center}>
        <Text style={s.err}>로드 실패: {error}</Text>
        <Pressable style={s.retry} onPress={() => void load()}><Text style={s.retryText}>다시 시도</Text></Pressable>
      </View>
    );
  }

  return (
    <View style={s.container}>
      <View style={s.filterBar}>
        <Text style={s.filterLabel}>최소 EV</Text>
        {MIN_EV_STEPS.map((v) => (
          <Pressable key={v} onPress={() => void applyFilters({ min_ev: v })} style={[s.chip, minEv === v && s.chipOn]}>
            <Text style={s.chipText}>{v === 0 ? "전체" : `${(v * 100).toFixed(0)}%`}</Text>
          </Pressable>
        ))}
        {(["ev", "time", "n_books"] as SortKey[]).map((k) => (
          <Pressable key={k} onPress={() => void applyFilters({ sort: k })} style={[s.chip, sort === k && s.chipOn]}>
            <Text style={s.chipText}>{k === "ev" ? "EV순" : k === "time" ? "시간순" : "북수순"}</Text>
          </Pressable>
        ))}
      </View>
      <FlatList
        data={data?.picks ?? []}
        keyExtractor={(p) => p.id}
        refreshing={loading}
        onRefresh={() => void load()}
        renderItem={({ item }) => (
          <PickCard
            pick={item}
            onOpen={(p) => nav.push("PickDetail", { pickId: p.id })}
            onBet={(p) => nav.push("CaptureSheet", { pickId: p.id })}
            onDismiss={(p) => void dismiss(p)}
          />
        )}
        ListEmptyComponent={loading ? <ActivityIndicator style={{ marginTop: 40 }} /> : <Text style={s.empty}>활성 픽 없음</Text>}
        ListFooterComponent={
          data && data.cooled.length > 0 ? (
            <View>
              <Pressable style={s.cooledHeader} onPress={() => setShowCooled((v) => !v)}>
                <Text style={s.cooledText}>식은 픽 {data.cooled.length} {showCooled ? "▲" : "▼"}</Text>
              </Pressable>
              {showCooled &&
                data.cooled.map((p) => (
                  <PickCard key={p.id} pick={p} onOpen={(x) => nav.push("PickDetail", { pickId: x.id })} onBet={(x) => nav.push("CaptureSheet", { pickId: x.id })} onDismiss={(x) => void dismiss(x)} />
                ))}
            </View>
          ) : null
        }
      />
    </View>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#0E0E13" },
  filterBar: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 6, padding: 10 },
  filterLabel: { color: "#9CA3AF", fontSize: 12, marginRight: 4 },
  chip: { paddingHorizontal: 10, paddingVertical: 5, borderRadius: 14, backgroundColor: "#1F2937" },
  chipOn: { backgroundColor: "#7C3AED" },
  chipText: { color: "#FFF", fontSize: 12 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: "#0E0E13", padding: 24 },
  err: { color: "#F87171", textAlign: "center", marginBottom: 12 },
  retry: { backgroundColor: "#7C3AED", paddingHorizontal: 16, paddingVertical: 10, borderRadius: 8 },
  retryText: { color: "#FFF", fontWeight: "600" },
  empty: { color: "#6B7280", textAlign: "center", marginTop: 40 },
  cooledHeader: { padding: 14 },
  cooledText: { color: "#9CA3AF", fontWeight: "600" },
});
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/components/PickCard.test.tsx src/screens/PicksScreen.test.tsx --watchAll=false`
Expected: PASS (3 tests).

- [ ] **Step 8: Commit**

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
git add app/src/components/PickCard.tsx app/src/components/PickCard.test.tsx app/src/screens/PicksScreen.tsx app/src/screens/PicksScreen.test.tsx
git commit -m "feat(softbook-app): picks feed screen + card + filter bar + cooled section"
```

---

### Task 9: Pick detail screen (on-demand refresh + per-book deep-links)

**Files:**
- Modify: `app/src/screens/PickDetailScreen.tsx` (replace the placeholder)
- Test: `app/src/screens/PickDetailScreen.test.tsx`

On open, call `GET /picks/{id}/refresh` for fresh per-book odds + server-built deep-links. Render one row per book (sorted best-first by the server), each tappable to `openOrCopy`. Show the honest label: "✓ 슬립 프리필" only when `isVerifiedPrefill`, else "앱 열기 + 복사". A manual refresh button re-fetches. A "베팅함" button pushes the capture sheet.

- [ ] **Step 1: Write the failing test**

```tsx
// app/src/screens/PickDetailScreen.test.tsx
jest.mock("../api/client", () => ({
  api: {
    refreshPick: jest.fn(async () => ({
      fair: 0.46,
      books: [
        { book: "FanDuel", odds: 2.2, ev: 0.05, deeplink: { tier: "prefill", url: "https://fd/x", copy_text: "FanDuel · NYM ML @ 2.20" } },
        { book: "BetMGM", odds: 2.1, ev: 0.02, deeplink: { tier: "event", url: "https://mgm/e", copy_text: "BetMGM · NYM ML @ 2.10" } },
      ],
      credits: 4,
    })),
  },
}));
jest.mock("../deeplink", () => ({ openOrCopy: jest.fn(async () => "opened"), isVerifiedPrefill: () => false }));
jest.mock("@react-navigation/native", () => ({ useNavigation: () => ({ push: jest.fn() }) }));

import { render, screen, waitFor, fireEvent } from "@testing-library/react-native";
import { PickDetailScreen } from "./PickDetailScreen";
import { openOrCopy } from "../deeplink";

const route = { params: { pickId: "E1|h2h|NYM|0|" } } as never;

test("loads refresh quotes and renders per-book rows", async () => {
  render(<PickDetailScreen route={route} />);
  await waitFor(() => expect(screen.getByText(/FanDuel/)).toBeOnTheScreen());
  expect(screen.getByText(/BetMGM/)).toBeOnTheScreen();
});

test("tapping a book row opens or copies its deep-link", async () => {
  render(<PickDetailScreen route={route} />);
  await waitFor(() => expect(screen.getByText(/FanDuel/)).toBeOnTheScreen());
  fireEvent.press(screen.getByText(/FanDuel/));
  expect(openOrCopy).toHaveBeenCalled();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/screens/PickDetailScreen.test.tsx --watchAll=false`
Expected: FAIL — placeholder.

- [ ] **Step 3: Replace `app/src/screens/PickDetailScreen.tsx`**

```tsx
// app/src/screens/PickDetailScreen.tsx
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useNavigation } from "@react-navigation/native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import { api } from "../api/client";
import type { RefreshResponse } from "../api/types";
import type { PicksStackParamList } from "../navigation/types";
import { openOrCopy, isVerifiedPrefill } from "../deeplink";
import { decimalOdds, evPct } from "../format";

// Indexed access (not the `Pick<>` utility) so files that import the API `Pick`
// type don't shadow it.
type Props = { route: NativeStackScreenProps<PicksStackParamList, "PickDetail">["route"] };

export function PickDetailScreen({ route }: Props) {
  const nav = useNavigation<{ push: (s: string, p?: object) => void }>();
  const { pickId } = route.params;
  const [data, setData] = useState<RefreshResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.refreshPick(pickId));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [pickId]);

  useEffect(() => { void load(); }, [load]);

  return (
    <ScrollView style={s.container} contentContainerStyle={{ padding: 12 }}>
      <View style={s.headerRow}>
        <Text style={s.fair}>{data?.fair != null ? `공정확률 ${(data.fair * 100).toFixed(1)}%` : "—"}</Text>
        <Pressable style={s.refresh} onPress={() => void load()}><Text style={s.refreshText}>새로고침{data ? ` (${data.credits})` : ""}</Text></Pressable>
      </View>
      {loading && <ActivityIndicator style={{ marginTop: 24 }} />}
      {error && <Text style={s.err}>로드 실패: {error}</Text>}
      {data?.books.map((b) => {
        const verified = isVerifiedPrefill(b.book, b.deeplink);
        return (
          <Pressable key={b.book} style={s.bookRow} onPress={() => void openOrCopy(b.deeplink)}>
            <View style={{ flex: 1 }}>
              <Text style={s.bookName}>{b.book}</Text>
              <Text style={s.tier}>{verified ? "✓ 슬립 프리필" : "앱 열기 + 복사"}</Text>
            </View>
            <Text style={s.odds}>{decimalOdds(b.odds)}</Text>
            <Text style={s.bookEv}>{evPct(b.ev)}</Text>
          </Pressable>
        );
      })}
      <Pressable style={s.betBtn} onPress={() => nav.push("CaptureSheet", { pickId })}>
        <Text style={s.betText}>베팅함 기록</Text>
      </Pressable>
    </ScrollView>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#0E0E13" },
  headerRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 8 },
  fair: { color: "#9CA3AF", fontSize: 14 },
  refresh: { backgroundColor: "#1F2937", paddingHorizontal: 12, paddingVertical: 6, borderRadius: 8 },
  refreshText: { color: "#FFF", fontSize: 12 },
  err: { color: "#F87171", marginTop: 12 },
  bookRow: { flexDirection: "row", alignItems: "center", backgroundColor: "#1A1A22", borderRadius: 10, padding: 14, marginVertical: 5 },
  bookName: { color: "#FFF", fontSize: 16, fontWeight: "600" },
  tier: { color: "#60A5FA", fontSize: 12, marginTop: 2 },
  odds: { color: "#FFF", fontSize: 16, fontWeight: "700", marginHorizontal: 10 },
  bookEv: { color: "#34D399", fontSize: 14, width: 56, textAlign: "right" },
  betBtn: { backgroundColor: "#7C3AED", borderRadius: 10, padding: 14, alignItems: "center", marginTop: 16 },
  betText: { color: "#FFF", fontWeight: "700", fontSize: 16 },
});
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/screens/PickDetailScreen.test.tsx --watchAll=false`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
git add app/src/screens/PickDetailScreen.tsx app/src/screens/PickDetailScreen.test.tsx
git commit -m "feat(softbook-app): pick detail — on-demand refresh + per-book deep-link rows"
```

---

### Task 10: One-tap capture sheet

**Files:**
- Create: `app/src/capture.ts` (pure `buildBetIn`)
- Modify: `app/src/screens/CaptureSheet.tsx` (replace the placeholder)
- Test: `app/src/capture.test.ts`, `app/src/screens/CaptureSheet.test.tsx`

The confirm sheet fetches the pick (`GET /picks/{id}`), pre-fills everything from it, and lets the user edit only **book / stake / filled odds** before `POST /bets` (`source:"one_tap"`). `buildBetIn(pick, inputs)` is pure and tested separately; the screen test covers fetch → prefill → submit.

- [ ] **Step 1: Write the failing pure-logic test**

```typescript
// app/src/capture.test.ts
import { buildBetIn } from "./capture";
import type { Pick } from "./api/types";

const PICK: Pick = {
  id: "E1|batter_total_bases|Over|1.5|Brandon Nimmo", status: "active", event_id: "E1",
  sport: "baseball_mlb", home: "MIA", away: "NYM", commence_time: "2099-01-01T00:00:00Z",
  market: "batter_total_bases", side: "Over", point: 1.5, player: "Brandon Nimmo",
  label: "Brandon Nimmo TB Over 1.5", ev: 0.074, book: "theScore", odds: 2.2, conf: 60, n_books: 9,
};

test("buildBetIn carries pick fields and applies user inputs", () => {
  const bet = buildBetIn(PICK, { book: "FanDuel", stake: 5, fillOdds: 2.25 });
  expect(bet).toMatchObject({
    event_id: "E1", sport: "baseball_mlb", market: "batter_total_bases", side: "Over",
    point: 1.5, player: "Brandon Nimmo", label: "Brandon Nimmo TB Over 1.5",
    book: "FanDuel", rec_odds: 2.2, fill_odds: 2.25, stake: 5, source: "one_tap",
  });
});

test("buildBetIn defaults book/fill_odds to the pick's best when unedited", () => {
  const bet = buildBetIn(PICK, { stake: 2 });
  expect(bet.book).toBe("theScore");
  expect(bet.fill_odds).toBe(2.2);
  expect(bet.rec_odds).toBe(2.2);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/capture.test.ts --watchAll=false`
Expected: FAIL — `Cannot find module './capture'`.

- [ ] **Step 3: Write `app/src/capture.ts`**

```typescript
// app/src/capture.ts
import type { BetIn, Pick } from "./api/types";

export interface CaptureInputs {
  book?: string;
  stake?: number;
  fillOdds?: number;
}

/** Build the POST /bets body from a recommended pick + the only three fields the
 *  user edits (book / stake / filled odds). Everything else is carried from the pick. */
export function buildBetIn(pick: Pick, inputs: CaptureInputs): BetIn {
  return {
    event_id: pick.event_id,
    sport: pick.sport,
    home: pick.home,
    away: pick.away,
    commence_time: pick.commence_time,
    market: pick.market,
    side: pick.side,
    point: pick.point,
    player: pick.player,
    label: pick.label,
    book: inputs.book ?? pick.book,
    rec_odds: pick.odds,
    fill_odds: inputs.fillOdds ?? pick.odds,
    stake: inputs.stake ?? null,
    source: "one_tap",
  };
}
```

- [ ] **Step 4: Write the failing screen test**

```tsx
// app/src/screens/CaptureSheet.test.tsx
jest.mock("../api/client", () => ({
  api: {
    getPick: jest.fn(async () => ({
      id: "E1|h2h|NYM|0|", status: "active", event_id: "E1", sport: "baseball_mlb",
      home: "MIA", away: "NYM", commence_time: "2099-01-01T00:00:00Z", market: "h2h",
      side: "NYM", point: 0, player: "", label: "NYM ML", ev: 0.05, book: "FanDuel",
      odds: 2.1, conf: 55, n_books: 8,
    })),
    postBet: jest.fn(async () => ({ id: 9 })),
  },
}));
jest.mock("@react-navigation/native", () => ({ useNavigation: () => ({ popToTop: jest.fn(), goBack: jest.fn() }) }));

import { render, screen, waitFor, fireEvent } from "@testing-library/react-native";
import { CaptureSheet } from "./CaptureSheet";
import { api } from "../api/client";

const route = { params: { pickId: "E1|h2h|NYM|0|" } } as never;

test("prefills from the pick and posts a bet", async () => {
  render(<CaptureSheet route={route} />);
  await waitFor(() => expect(screen.getByText("NYM ML")).toBeOnTheScreen());
  fireEvent.changeText(screen.getByPlaceholderText("스테이크 ($)"), "5");
  fireEvent.press(screen.getByText("기록"));
  await waitFor(() => expect(api.postBet).toHaveBeenCalled());
  const body = (api.postBet as jest.Mock).mock.calls[0][0];
  expect(body).toMatchObject({ event_id: "E1", stake: 5, fill_odds: 2.1, source: "one_tap" });
});
```

- [ ] **Step 5: Run it to verify it fails**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/screens/CaptureSheet.test.tsx --watchAll=false`
Expected: FAIL — placeholder.

- [ ] **Step 6: Replace `app/src/screens/CaptureSheet.tsx`**

```tsx
// app/src/screens/CaptureSheet.tsx
import { useEffect, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { useNavigation } from "@react-navigation/native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import { api } from "../api/client";
import type { Pick } from "../api/types";
import type { PicksStackParamList } from "../navigation/types";
import { buildBetIn } from "../capture";
import { decimalOdds } from "../format";

// Indexed access (not the `Pick<>` utility — `Pick` is imported above as the API type).
type Props = { route: NativeStackScreenProps<PicksStackParamList, "CaptureSheet">["route"] };

export function CaptureSheet({ route }: Props) {
  const nav = useNavigation<{ popToTop: () => void; goBack: () => void }>();
  const { pickId } = route.params;
  const [pick, setPick] = useState<Pick | null>(null);
  const [stake, setStake] = useState("");
  const [fillOdds, setFillOdds] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const p = await api.getPick(pickId);
        setPick(p);
        setFillOdds(p.odds != null ? String(p.odds) : "");
      } catch (e) {
        setError((e as Error).message);
      }
    })();
  }, [pickId]);

  const save = async () => {
    if (!pick) return;
    setSaving(true);
    setError(null);
    try {
      await api.postBet(
        buildBetIn(pick, {
          stake: stake ? Number(stake) : undefined,
          fillOdds: fillOdds ? Number(fillOdds) : undefined,
        }),
      );
      nav.popToTop();
    } catch (e) {
      setError((e as Error).message);
      setSaving(false);
    }
  };

  if (!pick) {
    return <View style={s.center}>{error ? <Text style={s.err}>{error}</Text> : <ActivityIndicator />}</View>;
  }

  return (
    <View style={s.container}>
      <Text style={s.label}>{pick.label}</Text>
      <Text style={s.meta}>추천: {pick.book ?? "—"} @ {decimalOdds(pick.odds)}</Text>
      <Text style={s.field}>스테이크</Text>
      <TextInput style={s.input} placeholder="스테이크 ($)" placeholderTextColor="#6B7280" keyboardType="decimal-pad" value={stake} onChangeText={setStake} />
      <Text style={s.field}>실제 체결 배당</Text>
      <TextInput style={s.input} placeholder="실배당" placeholderTextColor="#6B7280" keyboardType="decimal-pad" value={fillOdds} onChangeText={setFillOdds} />
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
  container: { flex: 1, backgroundColor: "#0E0E13", padding: 18 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: "#0E0E13" },
  label: { color: "#FFF", fontSize: 18, fontWeight: "700" },
  meta: { color: "#9CA3AF", marginTop: 6, marginBottom: 16 },
  field: { color: "#9CA3AF", fontSize: 13, marginTop: 12, marginBottom: 4 },
  input: { backgroundColor: "#1A1A22", color: "#FFF", borderRadius: 8, padding: 12, fontSize: 16 },
  actions: { flexDirection: "row", gap: 10, marginTop: 24 },
  btn: { flex: 1, padding: 14, borderRadius: 10, alignItems: "center" },
  cancel: { backgroundColor: "#374151" },
  save: { backgroundColor: "#7C3AED" },
  btnText: { color: "#FFF", fontWeight: "700", fontSize: 16 },
  err: { color: "#F87171", marginTop: 12 },
});
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/capture.test.ts src/screens/CaptureSheet.test.tsx --watchAll=false`
Expected: PASS (4 tests).

- [ ] **Step 8: Commit**

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
git add app/src/capture.ts app/src/capture.test.ts app/src/screens/CaptureSheet.tsx app/src/screens/CaptureSheet.test.tsx
git commit -m "feat(softbook-app): one-tap capture sheet (prefill from pick → POST /bets)"
```

---

### Task 11: My bets screen (calendar PnL + live open bets)

**Files:**
- Create: `app/src/mybets.ts` (pure `calendarSummary`)
- Modify: `app/src/screens/MyBetsScreen.tsx` (replace the placeholder)
- Test: `app/src/mybets.test.ts`, `app/src/screens/MyBetsScreen.test.tsx`

`GET /mybets` → calendar PnL on top (per-day sums + a running total) and live open bets below (status color + progress label from `liveProgressLabel`). `calendarSummary(calendar)` is pure (total + sorted day rows) and tested separately.

- [ ] **Step 1: Write the failing pure-logic test**

```typescript
// app/src/mybets.test.ts
import { calendarSummary } from "./mybets";

test("calendarSummary totals and sorts days descending", () => {
  const out = calendarSummary({ "2026-06-11": 2.4, "2026-06-12": -1.0, "2026-06-10": 5.0 });
  expect(out.total).toBeCloseTo(6.4);
  expect(out.days.map((d) => d.day)).toEqual(["2026-06-12", "2026-06-11", "2026-06-10"]);
});

test("calendarSummary handles an empty calendar", () => {
  expect(calendarSummary({})).toEqual({ total: 0, days: [] });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/mybets.test.ts --watchAll=false`
Expected: FAIL — `Cannot find module './mybets'`.

- [ ] **Step 3: Write `app/src/mybets.ts`**

```typescript
// app/src/mybets.ts
export interface DayPnl {
  day: string;
  pnl: number;
}
export interface CalendarSummary {
  total: number;
  days: DayPnl[];
}

export function calendarSummary(calendar: Record<string, number>): CalendarSummary {
  const days = Object.entries(calendar)
    .map(([day, pnl]) => ({ day, pnl }))
    .sort((a, b) => (a.day < b.day ? 1 : -1));
  const total = days.reduce((sum, d) => sum + d.pnl, 0);
  return { total, days };
}
```

- [ ] **Step 4: Write the failing screen test**

```tsx
// app/src/screens/MyBetsScreen.test.tsx
jest.mock("../api/client", () => ({
  api: {
    getMyBets: jest.fn(async () => ({
      calendar: { "2026-06-12": 2.4 },
      open: [
        {
          id: 1, event_id: "E1", sport: "baseball_mlb", home: "MIA", away: "NYM",
          commence_time: "2099-01-01T00:00:00Z", market: "batter_total_bases", side: "Over",
          point: 1.5, player: "Brandon Nimmo", label: "Nimmo TB Over 1.5", book: "FanDuel",
          fill_odds: 2.2, stake: 5, live_status: "winning",
          live_progress: { current: 2, needed: 1.5, remaining: 0, game_state: "Mid 2nd" },
        },
      ],
    })),
  },
}));
jest.mock("@react-navigation/native", () => ({ useFocusEffect: (cb: () => void) => cb() }));

import { render, screen, waitFor } from "@testing-library/react-native";
import { MyBetsScreen } from "./MyBetsScreen";

test("renders calendar total and a live open bet with progress", async () => {
  render(<MyBetsScreen />);
  await waitFor(() => expect(screen.getByText("Nimmo TB Over 1.5")).toBeOnTheScreen());
  expect(screen.getByText(/2 \/ 1.5/)).toBeOnTheScreen();
  expect(screen.getByText(/\+2\.40/)).toBeOnTheScreen(); // calendar total
});
```

- [ ] **Step 5: Run it to verify it fails**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/screens/MyBetsScreen.test.tsx --watchAll=false`
Expected: FAIL — placeholder.

- [ ] **Step 6: Replace `app/src/screens/MyBetsScreen.tsx`**

```tsx
// app/src/screens/MyBetsScreen.tsx
import { useCallback, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from "react-native";
import { useFocusEffect } from "@react-navigation/native";
import { api } from "../api/client";
import type { MyBets, OpenBet } from "../api/types";
import { calendarSummary } from "../mybets";
import { decimalOdds, liveProgressLabel, pnl } from "../format";

const STATUS_COLOR: Record<string, string> = {
  winning: "#34D399",
  losing: "#F87171",
  pending: "#9CA3AF",
};

export function MyBetsScreen() {
  const [data, setData] = useState<MyBets | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await api.getMyBets());
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { void load(); }, [load]));

  const summary = calendarSummary(data?.calendar ?? {});

  return (
    <ScrollView style={s.container} contentContainerStyle={{ padding: 12 }}>
      <View style={s.totalCard}>
        <Text style={s.totalLabel}>누적 PnL</Text>
        <Text style={[s.total, { color: summary.total >= 0 ? "#34D399" : "#F87171" }]}>{pnl(summary.total)}</Text>
      </View>
      {summary.days.map((d) => (
        <View key={d.day} style={s.dayRow}>
          <Text style={s.day}>{d.day}</Text>
          <Text style={[s.dayPnl, { color: d.pnl >= 0 ? "#34D399" : "#F87171" }]}>{pnl(d.pnl)}</Text>
        </View>
      ))}

      <Text style={s.section}>진행 중 ({data?.open.length ?? 0})</Text>
      {loading && <ActivityIndicator style={{ marginTop: 16 }} />}
      {data?.open.map((b: OpenBet) => (
        <View key={b.id} style={s.betCard}>
          <View style={s.betRow}>
            <Text style={s.betLabel} numberOfLines={1}>{b.label ?? `${b.side} ${b.point ?? ""}`}</Text>
            <Text style={[s.betStatus, { color: STATUS_COLOR[b.live_status ?? "pending"] ?? "#9CA3AF" }]}>
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
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#0E0E13" },
  totalCard: { backgroundColor: "#1A1A22", borderRadius: 12, padding: 16, alignItems: "center", marginBottom: 10 },
  totalLabel: { color: "#9CA3AF", fontSize: 13 },
  total: { fontSize: 28, fontWeight: "800", marginTop: 4 },
  dayRow: { flexDirection: "row", justifyContent: "space-between", paddingVertical: 8, paddingHorizontal: 4, borderBottomColor: "#1F2937", borderBottomWidth: 1 },
  day: { color: "#D1D5DB" },
  dayPnl: { fontWeight: "600" },
  section: { color: "#FFF", fontSize: 16, fontWeight: "700", marginTop: 20, marginBottom: 8 },
  betCard: { backgroundColor: "#1A1A22", borderRadius: 10, padding: 14, marginVertical: 5 },
  betRow: { flexDirection: "row", justifyContent: "space-between" },
  betLabel: { color: "#FFF", fontWeight: "600", flex: 1, paddingRight: 8 },
  betStatus: { fontWeight: "700", fontSize: 13 },
  betMeta: { color: "#9CA3AF", marginTop: 6, fontSize: 13 },
  empty: { color: "#6B7280", textAlign: "center", marginTop: 20 },
});
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/mybets.test.ts src/screens/MyBetsScreen.test.tsx --watchAll=false`
Expected: PASS (4 tests).

- [ ] **Step 8: Commit**

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
git add app/src/mybets.ts app/src/mybets.test.ts app/src/screens/MyBetsScreen.tsx app/src/screens/MyBetsScreen.test.tsx
git commit -m "feat(softbook-app): my bets — calendar PnL + live open-bet tracking"
```

---

### Task 12: Settings screen (filter prefs + credentials)

**Files:**
- Modify: `app/src/screens/SettingsScreen.tsx` (replace the placeholder)
- Test: `app/src/screens/SettingsScreen.test.tsx`

Two sections: (1) **filter prefs** (`min_ev`, sport toggles, muted-book toggles, sort) loaded from `GET /settings`, saved with `PUT /settings`; (2) **credentials** (API base URL + bearer + CF id/secret) saved to the Keychain via `storage.saveCredentials`, with a "푸시 등록" button that re-runs `registerForPush` → `POST /push/register`.

- [ ] **Step 1: Write the failing test**

```tsx
// app/src/screens/SettingsScreen.test.tsx
jest.mock("../api/client", () => ({
  api: {
    getSettings: jest.fn(async () => ({ min_ev: 0.02, sports: ["baseball_mlb"], muted_books: ["BallyBet"], sort: "ev" })),
    putSettings: jest.fn(async () => ({ ok: true })),
    registerPush: jest.fn(async () => ({ ok: true })),
  },
}));
jest.mock("../storage", () => ({ saveCredentials: jest.fn(async () => undefined), getCredentials: jest.fn(async () => null) }));
jest.mock("../push", () => ({ registerForPush: jest.fn(async () => "ExponentPushToken[x]") }));

import { render, screen, waitFor, fireEvent } from "@testing-library/react-native";
import { SettingsScreen } from "./SettingsScreen";
import { api } from "../api/client";
import { saveCredentials } from "../storage";

test("loads current settings and saves a changed sort", async () => {
  render(<SettingsScreen />);
  await waitFor(() => expect(screen.getByText("MLB")).toBeOnTheScreen());
  fireEvent.press(screen.getByText("시간순"));
  await waitFor(() => expect(api.putSettings).toHaveBeenCalled());
  const sent = (api.putSettings as jest.Mock).mock.calls.at(-1)?.[0];
  expect(sent.sort).toBe("time");
});

test("saves typed credentials to the keychain", async () => {
  render(<SettingsScreen />);
  await waitFor(() => expect(screen.getByPlaceholderText("API base URL")).toBeOnTheScreen());
  fireEvent.changeText(screen.getByPlaceholderText("API base URL"), "https://api.example.com");
  fireEvent.changeText(screen.getByPlaceholderText("APP_API_TOKEN"), "tok");
  fireEvent.changeText(screen.getByPlaceholderText("CF-Access-Client-Id"), "cfid");
  fireEvent.changeText(screen.getByPlaceholderText("CF-Access-Client-Secret"), "cfsecret");
  fireEvent.press(screen.getByText("자격 증명 저장"));
  await waitFor(() => expect(saveCredentials).toHaveBeenCalledWith({ baseUrl: "https://api.example.com", token: "tok", cfId: "cfid", cfSecret: "cfsecret" }));
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/screens/SettingsScreen.test.tsx --watchAll=false`
Expected: FAIL — placeholder.

- [ ] **Step 3: Replace `app/src/screens/SettingsScreen.tsx`**

```tsx
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/screens/SettingsScreen.test.tsx --watchAll=false`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
git add app/src/screens/SettingsScreen.tsx app/src/screens/SettingsScreen.test.tsx
git commit -m "feat(softbook-app): settings — filter prefs + keychain credentials + push register"
```

---

### Task 13: Push bridge, notification routing, on-device verification + TestFlight

**Files:**
- Create: `app/src/PushBridge.tsx`
- Modify: `app/App.tsx` (mount `<PushBridge/>`)
- Create: `app/DEEPLINK_VERIFICATION.md`
- Test: `app/src/PushBridge.test.tsx`

The launch glue: register the push token + categories, and route notification taps (warm + cold start) through the pure `notifTarget` to `navigationRef` (or a background `dismissPick`). `runTarget(target, deps)` is pure and tested; the component just wires real listeners to it.

- [ ] **Step 1: Write the failing test**

```tsx
// app/src/PushBridge.test.tsx
import { runTarget } from "./PushBridge";

test("runTarget routes a capture intent to the capture screen", () => {
  const navigate = jest.fn();
  const dismiss = jest.fn();
  runTarget({ kind: "capture", pickId: "E1|h2h|NYM|0|" }, { navigate, dismiss });
  expect(navigate).toHaveBeenCalledWith("Tabs", { screen: "Picks", params: { screen: "CaptureSheet", params: { pickId: "E1|h2h|NYM|0|" } } });
});

test("runTarget routes a pickDetail intent", () => {
  const navigate = jest.fn();
  runTarget({ kind: "pickDetail", pickId: "E1|h2h|NYM|0|" }, { navigate, dismiss: jest.fn() });
  expect(navigate).toHaveBeenCalledWith("Tabs", { screen: "Picks", params: { screen: "PickDetail", params: { pickId: "E1|h2h|NYM|0|" } } });
});

test("runTarget routes a settle intent to MyBets", () => {
  const navigate = jest.fn();
  runTarget({ kind: "mybets", appBetId: 7 }, { navigate, dismiss: jest.fn() });
  expect(navigate).toHaveBeenCalledWith("Tabs", { screen: "MyBets" });
});

test("runTarget calls dismiss (no navigation) for a dismiss intent", () => {
  const navigate = jest.fn();
  const dismiss = jest.fn();
  runTarget({ kind: "dismiss", pickId: "E1|h2h|NYM|0|" }, { navigate, dismiss });
  expect(dismiss).toHaveBeenCalledWith("E1|h2h|NYM|0|");
  expect(navigate).not.toHaveBeenCalled();
});

test("runTarget is a no-op for null", () => {
  const navigate = jest.fn();
  const dismiss = jest.fn();
  runTarget(null, { navigate, dismiss });
  expect(navigate).not.toHaveBeenCalled();
  expect(dismiss).not.toHaveBeenCalled();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest src/PushBridge.test.tsx --watchAll=false`
Expected: FAIL — `Cannot find module './PushBridge'`.

- [ ] **Step 3: Write `app/src/PushBridge.tsx`**

```tsx
// app/src/PushBridge.tsx
import { useEffect } from "react";
import * as Notifications from "expo-notifications";
import { api } from "./api/client";
import { getCredentials } from "./storage";
import { registerCategories, registerForPush, notifTarget, type NotifTarget } from "./push";
import { captureRoute, myBetsRoute, navigationRef, pickDetailRoute } from "./navigation";

export interface TargetDeps {
  navigate: (name: string, params?: object) => void;
  dismiss: (pickId: string) => void;
}

/** Pure: apply a notification intent to navigation / dismiss side-effects. */
export function runTarget(target: NotifTarget, deps: TargetDeps): void {
  if (!target) return;
  switch (target.kind) {
    case "capture":
      return deps.navigate(...captureRoute(target.pickId));
    case "pickDetail":
      return deps.navigate(...pickDetailRoute(target.pickId));
    case "mybets":
      return deps.navigate(...myBetsRoute());
    case "dismiss":
      return deps.dismiss(target.pickId);
  }
}

function navigate(name: string, params?: object) {
  if (navigationRef.isReady()) navigationRef.navigate(name as never, params as never);
}
function dismiss(pickId: string) {
  void api.dismissPick(pickId).catch(() => {});
}

export function PushBridge() {
  // Register token + categories once credentials exist.
  useEffect(() => {
    void (async () => {
      await registerCategories();
      if (!(await getCredentials())) return; // user hasn't entered creds yet
      const token = await registerForPush();
      if (token) await api.registerPush(token).catch(() => {});
    })();
  }, []);

  // Cold-start tap (app was killed).
  const last = Notifications.useLastNotificationResponse();
  useEffect(() => {
    if (last) runTarget(notifTarget(last), { navigate, dismiss });
  }, [last]);

  // Warm taps + action buttons.
  useEffect(() => {
    const sub = Notifications.addNotificationResponseReceivedListener((r) =>
      runTarget(notifTarget(r), { navigate, dismiss }),
    );
    return () => sub.remove();
  }, []);

  return null;
}
```

- [ ] **Step 4: Mount it in `app/App.tsx`**

Add the import and render `<PushBridge/>` inside `<NavigationContainer>` (so `navigationRef` is ready):

```tsx
// app/App.tsx — add the import:
import { PushBridge } from "./src/PushBridge";

// ...and inside <NavigationContainer ref={navigationRef}> wrap:
//   <RootNavigator />
//   <PushBridge />
```

The container becomes:

```tsx
      <NavigationContainer ref={navigationRef}>
        <RootNavigator />
        <PushBridge />
      </NavigationContainer>
```

- [ ] **Step 5: Write the on-device deep-link verification checklist**

```markdown
<!-- app/DEEPLINK_VERIFICATION.md -->
# Deep-link prefill verification (gates the "✓ 슬립 프리필" label)

The server returns a `prefill`-tier link for FanDuel and DraftKings, but a prefill
link only *proves the app opens* — the AASA wildcard does NOT prove the slip fills.
Research (2026): FanDuel's `addToBetslip` universal link is confirmed but best-effort;
**DraftKings `dksb://` is unverified by any public source.** So a book's label stays
"앱 열기 + 복사" until you confirm the slip actually populates on YOUR phone.

## Procedure (per book: FanDuel, then DraftKings)
1. Install the production TestFlight build on the NY iPhone, with the sportsbook app
   installed and logged in.
2. Open a High pick → 픽 상세 → tap the FanDuel (then DraftKings) row.
3. Confirm the sportsbook app opens AND the exact selection + line is pre-loaded in
   the bet slip (not just the app home / event page).
4. If — and only if — the slip is correctly pre-filled, add that book's label to
   `VERIFIED_PREFILL_BOOKS` in `src/config.ts` (e.g. `["FanDuel"]`). Rebuild.
5. If it opens to home/event or the wrong selection, leave it OUT — the honest
   "앱 열기 + 복사" label + copyable text is the correct UX. Re-test after book-app
   updates; prefill can regress silently.

## Notes
- Always test via the system handler (tapping the row → `Linking.openURL`), never an
  in-app webview.
- Single-leg only (these picks are single legs — the best case for prefill).
- `LSApplicationQueriesSchemes: ["dksb", ...]` (app.json) must be present or
  `canOpenURL('dksb://…')` throws and the row silently falls back to copy.
```

- [ ] **Step 6: Run the FULL test suite + typecheck**

Run:
```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app && npx jest --watchAll=false && npx tsc --noEmit
```
Expected: PASS — all suites green (smoke, storage, format, client, deeplink, push, navigation, PickCard, PicksScreen, PickDetailScreen, capture, CaptureSheet, mybets, MyBetsScreen, SettingsScreen, PushBridge); `tsc` clean.

- [ ] **Step 7: Build for device + TestFlight (human-assisted, needs Prerequisite 2)**

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb/app
# Dev build to test push + deep-links on the physical device (Expo Go can't do SDK-56 push):
eas build --platform ios --profile development
# Then for durable install:
eas build --platform ios --profile production
eas submit --platform ios --profile production   # uploads to TestFlight
```

On first launch on the device: open 설정 → enter the API base URL + the three credentials → "자격 증명 저장" → "푸시 등록". Then run the `DEEPLINK_VERIFICATION.md` procedure. Set a calendar reminder to re-upload the TestFlight build before its ~90-day expiry (a silent-alerting-failure risk, mirroring the repo's ufw-blocked-cloud-routine lesson).

- [ ] **Step 8: Commit**

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
git add app/src/PushBridge.tsx app/src/PushBridge.test.tsx app/App.tsx app/DEEPLINK_VERIFICATION.md
git commit -m "feat(softbook-app): push bridge + notification routing + on-device verification checklist"
```

---

## Decommission Telegram (after the app is verified)

Per the spec ("Decommission Telegram alert + fill-listener once the app is verified"), once the device run confirms pushes arrive, the feed loads, capture writes `app_bet`, and `/mybets` shows PnL:

- Stop/disable the Telegram-side units on EC2 (the push worker `kalshi-softbook-push.timer` already replaces the alert path; the fill-listener service is superseded by in-app capture). Do this in a follow-up ops session, not in this plan — keep both running in parallel during a soak period so no alert is missed. This is a deliberate **no-action-in-plan** item.

## Deferred to Phase 2 (per spec — not in this plan)

- Screenshot-AI capture (`POST /bets/scan` + vision model) for off-feed bets.
- Price-move / cashout push triggers.
- Saved mute-rules / advanced triage beyond `filter_prefs`.
- Swipe-to-dismiss gesture (currently inline buttons — see Task 8 note).
- Android (the app is `platforms:["ios"]` only).

## Self-Review

**Spec coverage (Phase-1 front-end slice):**
- Picks feed + filters + cooled/dismiss lifecycle → Tasks 8 (+ server feed from Plan 1).
- Pick detail + on-demand odds refresh + tiered deep-link open with honest label → Tasks 5, 9.
- One-tap capture (prefilled, stake + fill odds) → Task 10.
- 내 베팅: calendar PnL + live in-game tracking → Task 11.
- Push: registration + actionable `베팅함`/`관심없음` buttons + new-High/settle routing → Tasks 6, 13.
- Settings (filter prefs + credentials + push enable) → Task 12.
- Security: bearer + CF Access service-token headers in the Keychain → Tasks 2, 4.
- On-device deep-link verification gate → Task 13 (`DEEPLINK_VERIFICATION.md` + `VERIFIED_PREFILL_BOOKS`).
- Screenshot-AI / price-move push / swipe → explicitly Phase 2.

**Type/name consistency:** `Pick`/`BookQuote`/`Deeplink`/`BetIn`/`OpenBet`/`Settings` defined once in `api/types.ts` (Task 4), consumed unchanged by `format`, `deeplink`, `capture`, `mybets`, and all five screens. `notifTarget` (Task 6) feeds `runTarget` (Task 13); the `captureRoute`/`pickDetailRoute`/`myBetsRoute` builders (Task 7) are the single source of the nested-navigation shape used by `runTarget`'s test and impl. Category/action ids (`high_pick`/`BET_PLACED`/`NOT_INTERESTED`) are defined once in `config.ts` and must match `scripts/softbook_push_worker.py` — verified against the backend (it sends `category="high_pick"`, `data={pick_id}` and `category="settle"`, `data={app_bet_id}`).

**Known cross-task ordering note (not a blocker):** `format.ts` (Task 3) imports `LiveProgress` from `api/types.ts` (Task 4 Step 1). Create `types.ts` before running Task 3's tests green — called out inline in both tasks.

**Backend assumptions this plan relies on (all true in master `326b73c`):** `/picks` returns `{picks,counts,cooled}` with `Pick.id = "event_id|market|side|point|player"`; `/picks/{id}/refresh` returns server-built `deeplink` per book; `POST /bets` accepts the `BetIn` shape with `source`; `/mybets` parses `live_progress` to an object; auth is bearer + CF headers.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-13-ev-companion-app-ios.md`. Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration. REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**2. Inline Execution** — execute tasks in this session with checkpoints. REQUIRED SUB-SKILL: superpowers:executing-plans.

Note: Tasks 1, 9-step EAS init, and Task 13's build/submit need the human prerequisites (Apple Dev account, EAS login, the live backend + four credentials). Tasks 2–12 and Task 13 Steps 1–6 are fully codeable and testable offline with mocks — an agent can take the app to a green full suite + clean `tsc` before any paid/device step.
