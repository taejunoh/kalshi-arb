// app/src/push.ts
import * as Notifications from "expo-notifications";
import * as Device from "expo-device";
import Constants from "expo-constants";
import { ACTION_BET, ACTION_DISMISS, CATEGORY_HIGH, CATEGORY_PRICE_MOVE } from "./config";

export type NotifTarget =
  | { kind: "capture"; pickId: string }
  | { kind: "dismiss"; pickId: string }
  | { kind: "pickDetail"; pickId: string }
  | null;

/** Pure: tapped notification / action button -> in-app intent. Settle / price-move
 * pushes carry only app_bet_id; with the 내 베팅 tab removed there is no in-app
 * destination for them, so they return null (the notification still informs — act at
 * the book / Pikkit). */
export function notifTarget(response: Notifications.NotificationResponse): NotifTarget {
  const data = (response.notification.request.content.data ?? {}) as {
    pick_id?: string;
  };
  const action = response.actionIdentifier;
  if (action === ACTION_BET && data.pick_id) return { kind: "capture", pickId: data.pick_id };
  if (action === ACTION_DISMISS && data.pick_id) return { kind: "dismiss", pickId: data.pick_id };
  if (data.pick_id) return { kind: "pickDetail", pickId: data.pick_id };
  return null;
}

/** Attach the 베팅함 / 관심없음 buttons to the backend's `high_pick` category. */
export async function registerCategories(): Promise<void> {
  await Notifications.setNotificationCategoryAsync(CATEGORY_HIGH, [
    { identifier: ACTION_BET, buttonTitle: "베팅함", options: { opensAppToForeground: true } },
    { identifier: ACTION_DISMISS, buttonTitle: "관심없음", options: { opensAppToForeground: false, isDestructive: true } },
  ]);
  await Notifications.setNotificationCategoryAsync(CATEGORY_PRICE_MOVE, []);
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
