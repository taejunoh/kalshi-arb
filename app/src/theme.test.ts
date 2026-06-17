// app/src/theme.test.ts
import { color, radius, space, font, navTheme } from "./theme";

test("tokens expose the load-bearing keys", () => {
  expect(color.bg).toBe("#0B0B0F");
  expect(color.accent).toBe("#7C5CFF");
  expect(color.positive).toBe("#2FD08A");
  expect(color.negative).toBe("#FB6B6B");
  expect(radius.card).toBe(14);
  expect(space.lg).toBe(16);
  expect(font.title.fontWeight).toBe("600");
});

test("navTheme is a dark React Navigation theme using the accent", () => {
  expect(navTheme.dark).toBe(true);
  expect(navTheme.colors.primary).toBe(color.accent);
  expect(navTheme.colors.background).toBe(color.bg);
});
