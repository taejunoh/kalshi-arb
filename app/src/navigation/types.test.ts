// app/src/navigation/types.test.ts
import { navigationRef, captureRoute, pickDetailRoute } from "./index";

test("navigationRef is created", () => {
  expect(navigationRef).toBeDefined();
  expect(typeof navigationRef.navigate).toBe("function");
});

test("captureRoute builds a nested Tabs>Picks>CaptureSheet intent", () => {
  expect(captureRoute("E1|h2h|NYM|0|")).toEqual([
    "Tabs",
    { screen: "Picks", params: { screen: "CaptureSheet", params: { pickId: "E1|h2h|NYM|0|" } } },
  ]);
});

test("pickDetailRoute builds a nested Tabs>Picks>PickDetail intent", () => {
  expect(pickDetailRoute("E1|h2h|NYM|0|")).toEqual([
    "Tabs",
    { screen: "Picks", params: { screen: "PickDetail", params: { pickId: "E1|h2h|NYM|0|" } } },
  ]);
});
