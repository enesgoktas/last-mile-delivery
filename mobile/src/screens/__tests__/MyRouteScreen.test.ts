/**
 * Screen-level logic tests for MyRouteScreen.
 *
 * The screen itself is a React Native component and cannot be rendered in the
 * node test environment without @testing-library/react-native. These tests
 * instead verify the business-logic layer that backs the screen: sync-summary
 * construction and the API service contracts the screen relies on.
 */

import { buildSyncSummary } from "../myRouteHelpers";
import type { QueueSyncReport } from "../../services/offlineQueue";
import { buildCourierSystemWarnings } from "../myRouteHelpers";

// ── buildSyncSummary ───────────────────────────────────────────────────────────

const baseReport = (): QueueSyncReport => ({
  processed: 0,
  deferred: 0,
  skipped: 0,
  conflicts: 0,
  conflictItems: [],
  remaining: 0,
});

describe("MyRouteScreen — buildSyncSummary", () => {
  test("all-zero report displays every counter at zero", () => {
    const summary = buildSyncSummary(baseReport());
    expect(summary).toContain("Processed: 0");
    expect(summary).toContain("Deferred: 0");
    expect(summary).toContain("Skipped: 0");
    expect(summary).toContain("Conflicts: 0");
    expect(summary).toContain("Remaining: 0");
  });

  test("large processed + remaining values are formatted correctly", () => {
    const summary = buildSyncSummary({ ...baseReport(), processed: 99, remaining: 10 });
    expect(summary).toContain("Processed: 99");
    expect(summary).toContain("Remaining: 10");
  });

  test("conflict preview omitted by default when includeConflictPreview is false", () => {
    const report = {
      ...baseReport(),
      conflicts: 2,
      conflictItems: [
        { itemId: "i1", actionType: "complete", stopId: "stop-aaa-bbb", reason: "Version mismatch" },
        { itemId: "i2", actionType: "fail", stopId: "stop-ccc-ddd", reason: "Already failed" },
      ],
    };
    const summary = buildSyncSummary(report, false);
    expect(summary).toContain("Conflicts: 2");
    expect(summary).not.toContain("Conflict samples");
  });

  test("conflict preview included up to 3 items when includeConflictPreview is true", () => {
    const report = {
      ...baseReport(),
      conflicts: 4,
      conflictItems: [
        { itemId: "i1", actionType: "complete", stopId: "stop-111-aaa", reason: "Stale" },
        { itemId: "i2", actionType: "fail",     stopId: "stop-222-bbb", reason: "Duplicate" },
        { itemId: "i3", actionType: "arrive",   stopId: "stop-333-ccc", reason: "Outdated" },
        { itemId: "i4", actionType: "complete", stopId: "stop-444-ddd", reason: "Conflict" },
      ],
    };
    const summary = buildSyncSummary(report, true);
    expect(summary).toContain("Conflict samples");
    // Stop IDs are sliced to first 8 chars
    expect(summary).toContain("stop-111");
    expect(summary).toContain("stop-222");
    expect(summary).toContain("stop-333");
    // 4th item is beyond the 3-item preview limit
    expect(summary).not.toContain("stop-444");
  });

  test("conflict preview not appended when conflictItems is empty even if flag is true", () => {
    const summary = buildSyncSummary({ ...baseReport(), conflicts: 0, conflictItems: [] }, true);
    expect(summary).not.toContain("Conflict samples");
  });

  test("deferred count reflects partial queue drains correctly", () => {
    const summary = buildSyncSummary({ ...baseReport(), deferred: 3, remaining: 3 });
    expect(summary).toContain("Deferred: 3");
    expect(summary).toContain("Remaining: 3");
  });
});

describe("MyRouteScreen — buildCourierSystemWarnings", () => {
  const baseHealth = {
    mode: "idle" as const,
    lastUploadError: null,
    lastAttemptAt: null,
    lastSuccessfulUploadAt: null,
  };

  test("warns when courier is online with foreground-only location tracking", () => {
    const warnings = buildCourierSystemWarnings({
      courierStatus: "ONLINE",
      platformOs: "android",
      pushTokenState: "registered",
      pushWarning: null,
      locationTrackingHealth: { ...baseHealth, mode: "foreground" },
    });

    expect(warnings).toContain(
      "Background location permission is not granted. Keep the app open or enable Always Allow location access.",
    );
  });

  test("warns when location uploads are failing", () => {
    const warnings = buildCourierSystemWarnings({
      courierStatus: "ONLINE",
      platformOs: "android",
      pushTokenState: "registered",
      pushWarning: null,
      locationTrackingHealth: { ...baseHealth, lastUploadError: "upload failed" },
    });

    expect(warnings).toContain("Location uploads are failing: upload failed");
  });

  test("warns that iOS push delivery is not wired even when token looks registered", () => {
    const warnings = buildCourierSystemWarnings({
      courierStatus: "ONLINE",
      platformOs: "ios",
      pushTokenState: "registered",
      pushWarning: null,
      locationTrackingHealth: baseHealth,
    });

    expect(warnings).toContain(
      "iOS push delivery is not wired to the backend yet. Keep the app open for route updates.",
    );
  });
});

// ── shouldQueueOfflineOnError (used by MyRouteScreen sync path) ───────────────

import { shouldQueueOfflineOnError } from "../stopDetailHelpers";

describe("MyRouteScreen — shouldQueueOfflineOnError", () => {
  test("queues only on explicit network reachability errors", () => {
    expect(shouldQueueOfflineOnError(new Error("Cannot reach API at http://localhost:8000"))).toBe(true);
    expect(shouldQueueOfflineOnError(new Error("Network request failed"))).toBe(true);
    expect(shouldQueueOfflineOnError("raw string error")).toBe(false);
    expect(shouldQueueOfflineOnError(null)).toBe(false);
  });

  test("queues on 500-series server errors", () => {
    const err = Object.assign(new Error("Internal Server Error"), { status: 500 });
    expect(shouldQueueOfflineOnError(err)).toBe(true);
    const err503 = Object.assign(new Error("Service Unavailable"), { status: 503 });
    expect(shouldQueueOfflineOnError(err503)).toBe(true);
  });

  test("queues on 429 rate-limit and 408 timeout", () => {
    const err429 = Object.assign(new Error("Rate limited"), { status: 429 });
    expect(shouldQueueOfflineOnError(err429)).toBe(true);
    const err408 = Object.assign(new Error("Request Timeout"), { status: 408 });
    expect(shouldQueueOfflineOnError(err408)).toBe(true);
  });

  test("does NOT queue on 400-series client errors except 408/429", () => {
    const err400 = Object.assign(new Error("Bad Request"), { status: 400 });
    expect(shouldQueueOfflineOnError(err400)).toBe(false);
    const err401 = Object.assign(new Error("Unauthorised"), { status: 401 });
    expect(shouldQueueOfflineOnError(err401)).toBe(false);
    const err422 = Object.assign(new Error("Unprocessable Entity"), { status: 422 });
    expect(shouldQueueOfflineOnError(err422)).toBe(false);
  });
});
