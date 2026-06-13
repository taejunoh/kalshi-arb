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
