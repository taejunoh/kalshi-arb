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
import { ACTION_BET, ACTION_DISMISS, CATEGORY_PRICE_MOVE } from "./config";

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

test("a price_move push routes to MyBets via app_bet_id", () => {
  const resp = {
    actionIdentifier: "default",
    notification: { request: { content: {
      categoryIdentifier: CATEGORY_PRICE_MOVE,
      data: { app_bet_id: 7 },
    } } },
  };
  expect(notifTarget(resp as never)).toEqual({ kind: "mybets", appBetId: 7 });
});

test("registerCategories includes price_move (no action buttons)", async () => {
  await registerCategories();
  const Notifications = require("expo-notifications");
  const ids = (Notifications.setNotificationCategoryAsync as jest.Mock).mock.calls.map((c) => c[0]);
  expect(ids).toContain(CATEGORY_PRICE_MOVE);
});
