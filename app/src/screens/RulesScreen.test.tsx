jest.mock("../api/client", () => ({
  api: {
    getRules: jest.fn(async () => ({ rules: [{ id: 1, field: "player", value: "Nico Hoerner", created_at: "t" }] })),
    addRule: jest.fn(async () => ({ id: 2 })),
    deleteRule: jest.fn(async () => ({ ok: true })),
  },
}));

import { render, screen, waitFor, fireEvent } from "@testing-library/react-native";
import { RulesEditor } from "./RulesScreen";
import { api } from "../api/client";

test("lists existing rules and adds a new one", async () => {
  await render(<RulesEditor />);
  await waitFor(() => expect(screen.getByText(/Nico Hoerner/)).toBeOnTheScreen());
  await fireEvent.changeText(screen.getByPlaceholderText("값 (예: Aaron Judge / total / EVT123)"), "total");
  await fireEvent.press(screen.getByText("규칙 추가"));
  await waitFor(() => expect(api.addRule).toHaveBeenCalled());
});

test("deletes a rule", async () => {
  await render(<RulesEditor />);
  await waitFor(() => expect(screen.getByText(/Nico Hoerner/)).toBeOnTheScreen());
  await fireEvent.press(screen.getByTestId("delete-rule-1"));
  await waitFor(() => expect(api.deleteRule).toHaveBeenCalledWith(1));
});
