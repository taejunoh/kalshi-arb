// app/src/PushBridge.test.tsx
// Mock expo-notifications so the module import succeeds in Jest (the test only exercises
// the pure runTarget function — none of these hooks/listeners are called by runTarget itself).
jest.mock("expo-notifications", () => ({
  useLastNotificationResponse: jest.fn(() => null),
  addNotificationResponseReceivedListener: jest.fn(() => ({ remove: jest.fn() })),
  setNotificationCategoryAsync: jest.fn(async () => undefined),
  getPermissionsAsync: jest.fn(async () => ({ status: "granted" })),
  requestPermissionsAsync: jest.fn(async () => ({ status: "granted" })),
  getExpoPushTokenAsync: jest.fn(async () => ({ data: "ExponentPushToken[test]" })),
}));
jest.mock("expo-device", () => ({ isDevice: false }));
jest.mock("expo-constants", () => ({ expoConfig: { extra: { eas: { projectId: null } } } }));
jest.mock("expo-secure-store", () => ({
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => undefined),
  deleteItemAsync: jest.fn(async () => undefined),
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: "WHEN_UNLOCKED_THIS_DEVICE_ONLY",
}));
// Mock the navigation ref — runTarget calls navigate but does not import the ref directly.
// Use requireActual so the real route builders are exercised (not re-implemented inline).
jest.mock("./navigation", () => ({
  ...jest.requireActual("./navigation"),
  navigationRef: { isReady: () => true, navigate: jest.fn() },
}));
jest.mock("./api/client", () => ({
  api: {
    registerPush: jest.fn(async () => ({ ok: true })),
    dismissPick: jest.fn(async () => ({ ok: true })),
  },
}));

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
