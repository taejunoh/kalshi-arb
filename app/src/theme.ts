// app/src/theme.ts
// Single source of truth for the app's dark visual language. Plain data — no theming lib.
import type { TextStyle } from "react-native";
import { DarkTheme } from "@react-navigation/native";

export const color = {
  bg: "#0B0B0F",
  surface: "#16161C",
  surfaceRaised: "#1E1E26",
  border: "#26262F",
  accent: "#7C5CFF",
  accentDim: "#241C46",
  accentText: "#C9BCFF",
  onAccent: "#FFFFFF",
  positive: "#2FD08A",
  positiveDim: "#11321F",
  positiveStrong: "#0F4A2C",
  negative: "#FB6B6B",
  negativeDim: "#3A1A1A",
  negativeStrong: "#5A2222",
  urgent: "#FBBF24",
  heatmapEmpty: "#141319",
  text: "#F4F4F6",
  muted: "#9A9AA6",
  faint: "#62626E",
} as const;

export const radius = { sm: 8, card: 14, button: 11, pill: 20 } as const;
export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 20 } as const;

export const font: Record<string, TextStyle> = {
  title: { fontSize: 21, fontWeight: "600", letterSpacing: -0.4, color: color.text },
  pickLabel: { fontSize: 18, fontWeight: "600", letterSpacing: -0.2, color: color.text },
  rowName: { fontSize: 15, fontWeight: "600", color: color.text },
  body: { fontSize: 14, color: color.text },
  meta: { fontSize: 13, color: color.muted },
  micro: { fontSize: 11, fontWeight: "500", letterSpacing: 0.4 },
};

export const navTheme = {
  ...DarkTheme,
  colors: {
    ...DarkTheme.colors,
    primary: color.accent,
    background: color.bg,
    card: color.bg,
    text: color.text,
    border: color.border,
    notification: color.accent,
  },
};
