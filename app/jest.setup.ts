// Suppress the "overlapping act() calls" console.error that React 19 emits
// when @testing-library/react-native v14's async render() is used with jest-expo.
// This is a known React 19 + RNTL v14 compatibility issue; the warnings are
// cosmetic — tests pass correctly and act() behaviour is not broken.
const originalError = console.error.bind(console);
console.error = (...args: unknown[]) => {
  if (
    typeof args[0] === "string" &&
    args[0].includes("overlapping act() calls")
  ) {
    return;
  }
  originalError(...args);
};

// react-native-safe-area-context needs a SafeAreaProvider at runtime; screens that
// call useSafeAreaInsets() (Settings) would throw when rendered bare in tests.
// Mock it to zero insets (the screens only read insets.top for notch padding).
jest.mock("react-native-safe-area-context", () => {
  const passthrough = (props: { children?: unknown }) => props.children;
  return {
    useSafeAreaInsets: () => ({ top: 0, right: 0, bottom: 0, left: 0 }),
    SafeAreaProvider: passthrough,
    SafeAreaView: passthrough,
  };
});
