// app/jest.globalSetup.js
//
// Pins the test runner's timezone to America/New_York for the local-month
// fallback test in src/screens/MyBetsScreen.test.tsx ("a response predating
// `month` falls back to the DEVICE's local month").
//
// That test asserts a LOCAL-zone derivation, so its discriminating case
// (2026-09-01T01:00Z -> "2026-08") is only meaningful in a zone west of UTC.
// The pin is load-bearing in the strong sense, not merely the anti-vacuity
// one: without it, a CORRECT implementation goes RED on a UTC host (verified —
// the assertion reads "2026-09"). It is what makes the suite host-independent,
// which is the whole point; EC2/CI run UTC.
//
// (Superseded rationale: this pin was introduced for a test asserting the
// calendar's month prefix was UTC. The calendar's day keys are Eastern as of
// the ET re-attribution, that test is gone, and the pin outlived it.)
//
// This CANNOT live in package.json's "test" script (e.g.
// `"test": "TZ=America/New_York jest"`). `scripts` is an Expo fingerprint
// input (`packageJson:scripts` — confirmed via
// `npx expo-updates fingerprint:generate --platform ios`), so editing it
// changes the app's runtimeVersion fingerprint and orphans OTA updates for
// every build already installed on a device (see app/AGENTS.md: "Do NOT
// 'tidy' fingerprint inputs after a build"). The `jest` config block and
// plain JS files under app/ are NOT fingerprint sources, so this file plus
// the `jest.globalSetup` key are the safe place for the pin.
//
// globalSetup runs once in Jest's parent process before it forks worker
// processes, so setting process.env.TZ here is inherited by every worker at
// its own boot — unlike setting it inside a test module, which runs after
// Node has already cached the zone and has no effect (see the comment in
// MyBetsScreen.test.tsx).
module.exports = async function globalSetup() {
  process.env.TZ = "America/New_York";
};
