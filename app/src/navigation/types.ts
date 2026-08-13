// app/src/navigation/types.ts
import type { NavigatorScreenParams } from "@react-navigation/native";
import type { AppBet, BetInDraft } from "../api/types";

export type PicksStackParamList = {
  PicksList: undefined;
  PickDetail: { pickId: string; matchup?: string | null };
  CaptureSheet: { pickId?: string; scanDraft?: BetInDraft };
};

export type MyBetsStackParamList = {
  MyBetsList: undefined;
  BetEdit: { bet: AppBet };
};

export type TabParamList = {
  Picks: NavigatorScreenParams<PicksStackParamList> | undefined;
  MyBets: NavigatorScreenParams<MyBetsStackParamList> | undefined;
  Settings: undefined;
};

export type RootParamList = {
  Tabs: NavigatorScreenParams<TabParamList>;
};
