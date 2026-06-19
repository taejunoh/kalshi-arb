# Expo HAS CHANGED

Read the exact versioned docs at https://docs.expo.dev/versions/v56.0.0/ before writing any code.

# OTA updates (expo-updates / EAS Update)

Since iOS build #15 the app embeds `expo-updates` (`runtimeVersion.policy = "fingerprint"`,
channel `production`, `fallbackToCacheTimeout = 0` so launch is never blocked — an update
downloaded on launch applies on the *next* launch). No JS code drives this; it's automatic.

- **JS/asset-only change → publish instantly, NO rebuild:**
  ```
  cd app && eas update --channel production --message "<what changed>"
  ```
  Installed builds with a matching fingerprint pick it up on next launch.

- **Native change → MUST rebuild + submit** (new native module, permission, plugin, or any
  edit to a fingerprint input like app.json `ios.*`/`plugins`/`updates`/`runtimeVersion` or
  package.json native deps). The fingerprint changes, so OTA will NOT target old binaries:
  ```
  cd app && LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 \
    eas build --platform ios --profile production --local --non-interactive --output ./x.ipa
  eas submit --platform ios --profile production --path ./x.ipa --non-interactive
  ```
  (`--local` avoids the EAS cloud-build quota; credentials + ASC key live on EAS servers.)

- **Which one does my change need?** `npx expo-updates fingerprint:generate --platform ios`
  — if the hash differs from the installed build's, it needs a rebuild, not an OTA.
  ⚠️ Do NOT "tidy" fingerprint inputs after a build (e.g. removing the remote-ignored
  `ios.buildNumber`) — it changes the fingerprint and orphans OTA for that build.
