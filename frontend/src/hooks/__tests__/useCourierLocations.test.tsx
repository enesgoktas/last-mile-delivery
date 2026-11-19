import { describe, it, expect, vi } from "vitest";

vi.mock("../../api/client", () => ({
  listCourierLocations: vi.fn().mockResolvedValue([]),
}));

describe("useCourierLocations", () => {
  it("module exports useCourierLocations", async () => {
    const mod = await import("../useCourierLocations");
    expect(typeof mod.useCourierLocations).toBe("function");
  });
});
