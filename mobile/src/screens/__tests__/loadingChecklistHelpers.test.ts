import { buildRouteStartFailureMessage } from "../loadingChecklistHelpers";

describe("loadingChecklistHelpers", () => {
  test("keeps validation-style API errors concise", () => {
    expect(
      buildRouteStartFailureMessage(
        Object.assign(new Error("Route already active"), { status: 400 }),
      ),
    ).toBe("Route already active");
  });

  test("adds explicit connectivity guidance for retryable failures", () => {
    expect(
      buildRouteStartFailureMessage(
        Object.assign(new Error("Gateway timeout"), { status: 504 }),
      ),
    ).toBe(
      "Gateway timeout\n\nRoute start requires a live connection and is not queued offline. Reconnect and try again.",
    );
  });

  test("adds connectivity guidance for non-API transport errors", () => {
    expect(
      buildRouteStartFailureMessage(new Error("Network request failed")),
    ).toBe(
      "Network request failed\n\nRoute start requires a live connection and is not queued offline. Reconnect and try again.",
    );
  });
});
