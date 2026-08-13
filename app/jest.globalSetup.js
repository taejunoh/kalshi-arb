// app/jest.globalSetup.js
//
// Pins the test runner's timezone to America/New_York so the "이번 달" UTC
// month-prefix test in src/screens/MyBetsScreen.test.tsx actually exercises
// the UTC/local boundary it guards, instead of being vacuous on a UTC host.
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
