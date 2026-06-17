// app/src/components/CalendarHeatmap.test.tsx
import { render, screen } from "@testing-library/react-native";
import { CalendarHeatmap } from "./CalendarHeatmap";

test("renders a cell per day of the month and marks the latest day", async () => {
  await render(<CalendarHeatmap calendar={{ "2026-06-03": 12, "2026-06-10": -8, "2026-06-15": 30 }} />);
  // 30 day cells for June + the month label
  expect(screen.getByText("2026-06")).toBeOnTheScreen();
  expect(screen.getByTestId("heat-2026-06-15")).toBeOnTheScreen();
  expect(screen.getByTestId("heat-2026-06-30")).toBeOnTheScreen();
});
