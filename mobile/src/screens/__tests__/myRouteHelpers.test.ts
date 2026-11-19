import {
  buildSyncSummary,
  calculateRouteProgress,
  formatRelativeSyncTime,
  isRouteTerminal,
} from "../myRouteHelpers";

describe("myRouteHelpers", () => {
  test("buildSyncSummary returns base counters", () => {
    const summary = buildSyncSummary({
      processed: 2,
      deferred: 1,
      skipped: 0,
      conflicts: 0,
      conflictItems: [],
      remaining: 3,
    });

    expect(summary).toContain("Processed: 2");
    expect(summary).toContain("Deferred: 1");
    expect(summary).toContain("Skipped: 0");
    expect(summary).toContain("Conflicts: 0");
    expect(summary).toContain("Remaining: 3");
  });

  test("buildSyncSummary includes conflict preview when requested", () => {
    const summary = buildSyncSummary(
      {
        processed: 0,
        deferred: 0,
        skipped: 0,
        conflicts: 1,
        conflictItems: [
          {
            itemId: "item-1",
            actionType: "ARRIVE_STOP",
            routeId: "route-1",
            stopId: "stop-12345678",
            reason: "already reassigned",
          },
        ],
        remaining: 0,
      },
      true,
    );

    expect(summary).toContain("Conflict samples:");
    expect(summary).toContain("ARRIVE_STOP stop-123");
    expect(summary).toContain("already reassigned");
  });

  test("isRouteTerminal returns true when all stops are terminal", () => {
    expect(
      isRouteTerminal({
        id: "route-1",
        courier_id: "courier-1",
        status: "ACTIVE",
        planned_distance_km: null,
        started_at: null,
        completed_at: null,
        stops: [
          { id: "s1", status: "COMPLETED" },
          { id: "s2", status: "FAILED" },
        ],
      } as never),
    ).toBe(true);
  });

  test("isRouteTerminal returns false when route is null or non-terminal", () => {
    expect(isRouteTerminal(null)).toBe(false);
    expect(
      isRouteTerminal({
        id: "route-1",
        courier_id: "courier-1",
        status: "ACTIVE",
        planned_distance_km: null,
        started_at: null,
        completed_at: null,
        stops: [{ id: "s1", status: "ARRIVED" }],
      } as never),
    ).toBe(false);
  });

  test("calculateRouteProgress counts completed and failed stops", () => {
    expect(
      calculateRouteProgress([
        { id: "s1", status: "COMPLETED" } as never,
        { id: "s2", status: "FAILED" } as never,
        { id: "s3", status: "ARRIVED" } as never,
        { id: "s4", status: "PENDING" } as never,
      ]),
    ).toEqual({
      total: 4,
      terminal: 2,
      percent: 50,
    });
  });

  test("calculateRouteProgress returns zeros for empty routes", () => {
    expect(calculateRouteProgress([])).toEqual({ total: 0, terminal: 0, percent: 0 });
    expect(calculateRouteProgress(undefined)).toEqual({ total: 0, terminal: 0, percent: 0 });
  });

  test("formatRelativeSyncTime returns human readable relative strings", () => {
    const base = Date.parse("2026-04-01T12:00:00.000Z");
    expect(formatRelativeSyncTime("2026-04-01T12:00:00.000Z", base)).toBe("just now");
    expect(formatRelativeSyncTime("2026-04-01T11:59:30.000Z", base)).toBe("30s ago");
    expect(formatRelativeSyncTime("2026-04-01T11:57:00.000Z", base)).toBe("3m ago");
    expect(formatRelativeSyncTime("2026-04-01T09:00:00.000Z", base)).toBe("3h ago");
  });
});
