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
