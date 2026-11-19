import { describe, expect, it } from "vitest";

import {
  buildAnalyticsQueryParams,
  buildPresetFilterState,
  getAnalyticsFilterValidation,
} from "../filters";

describe("analytics filter helpers", () => {
  it("builds a 30-day month preset ending on the provided date", () => {
    const filters = buildPresetFilterState("month", new Date("2026-03-30T10:15:00"));
    expect(filters).toEqual({
      startDate: "2026-03-01",
      endDate: "2026-03-30",
    });
  });

  it("serializes filters into bounded datetime query params", () => {
    expect(
      buildAnalyticsQueryParams(
        { startDate: "2026-03-01", endDate: "2026-03-30" },
        "Europe/Istanbul",
      ),
    ).toEqual({
      date_from: "2026-03-01T00:00:00",
      date_to: "2026-03-30T23:59:59.999",
      timezone: "Europe/Istanbul",
    });
  });

  it("rejects ranges longer than the synchronous report limit", () => {
    expect(
      getAnalyticsFilterValidation({ startDate: "2026-01-01", endDate: "2026-04-15" }),
    ).toEqual({
      isValid: false,
      error: "Reports are capped at 92 days for synchronous downloads.",
      totalDays: 105,
    });
  });
});
