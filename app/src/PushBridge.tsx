// app/src/PushBridge.tsx
import { useEffect } from "react";
import * as Notifications from "expo-notifications";
import { api } from "./api/client";
import { getCredentials } from "./storage";
import { registerCategories, registerForPush, notifTarget, type NotifTarget } from "./push";
import { captureRoute, navigationRef, pickDetailRoute } from "./navigation";

export interface TargetDeps {
  navigate: (name: string, params?: object) => void;
  dismiss: (pickId: string) => void;
}

/** Pure: apply a notification intent to navigation / dismiss side-effects. */
export function runTarget(target: NotifTarget, deps: TargetDeps): void {
  if (!target) return;
  switch (target.kind) {
    case "capture": {
      const [name, params] = captureRoute(target.pickId);
      return deps.navigate(name, params);
    }
    case "pickDetail": {
      const [name, params] = pickDetailRoute(target.pickId);
      return deps.navigate(name, params);
    }
    case "dismiss":
      return deps.dismiss(target.pickId);
  }
}

function navigate(name: string, params?: object) {
  if (navigationRef.isReady()) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (navigationRef as any).navigate(name, params);
  }
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
