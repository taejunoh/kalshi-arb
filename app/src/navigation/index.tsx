// app/src/navigation/index.tsx
import { createNavigationContainerRef } from "@react-navigation/native";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { Feather } from "@expo/vector-icons";
import { color } from "../theme";
import type { PicksStackParamList, RootParamList, TabParamList } from "./types";
import { PicksScreen } from "../screens/PicksScreen";
import { PickDetailScreen } from "../screens/PickDetailScreen";
import { CaptureSheet } from "../screens/CaptureSheet";
import { SettingsScreen } from "../screens/SettingsScreen";

export const navigationRef = createNavigationContainerRef<RootParamList>();

// Imperative nested-route builders (used by PushBridge in Task 13). Pure + testable.
export function captureRoute(pickId: string): ["Tabs", object] {
  return ["Tabs", { screen: "Picks", params: { screen: "CaptureSheet", params: { pickId } } }];
}
export function pickDetailRoute(pickId: string): ["Tabs", object] {
  return ["Tabs", { screen: "Picks", params: { screen: "PickDetail", params: { pickId } } }];
}

const PicksStack = createNativeStackNavigator<PicksStackParamList>();
function PicksNavigator() {
  return (
    <PicksStack.Navigator
      screenOptions={{
        headerStyle: { backgroundColor: color.bg },
        headerTintColor: color.accent,
        headerTitleStyle: { color: color.text },
        headerShadowVisible: false,
        contentStyle: { backgroundColor: color.bg },
      }}
    >
      <PicksStack.Screen name="PicksList" component={PicksScreen} options={{ title: "픽" }} />
      <PicksStack.Screen
        name="PickDetail"
        component={PickDetailScreen}
        options={({ route }) => ({ title: route.params?.matchup || "픽 상세" })}
      />
      <PicksStack.Screen name="CaptureSheet" component={CaptureSheet} options={{ presentation: "modal", title: "베팅 기록" }} />
    </PicksStack.Navigator>
  );
}

const Tabs = createBottomTabNavigator<TabParamList>();
export function RootNavigator() {
  return (
    <Tabs.Navigator
      screenOptions={{
        headerShown: false,
        tabBarStyle: { backgroundColor: color.bg, borderTopColor: color.border },
        tabBarActiveTintColor: color.accent,
        tabBarInactiveTintColor: color.faint,
      }}
    >
      <Tabs.Screen name="Picks" component={PicksNavigator} options={{ title: "픽", tabBarIcon: ({ color: c, size }) => <Feather name="zap" size={size} color={c} /> }} />
      <Tabs.Screen name="Settings" component={SettingsScreen} options={{ title: "설정", tabBarIcon: ({ color: c, size }) => <Feather name="settings" size={size} color={c} /> }} />
    </Tabs.Navigator>
  );
}
