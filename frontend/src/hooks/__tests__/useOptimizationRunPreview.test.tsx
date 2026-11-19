import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getOptimizationRunPreview } from "../../api/client";
import { useOptimizationRunPreview } from "../useOptimizationRunPreview";

vi.mock("../../api/client", () => ({
  getOptimizationRunPreview: vi.fn(),
}));

const mockGetOptimizationRunPreview = vi.mocked(getOptimizationRunPreview);

function makeEconomicBreakdown(overrides: Record<string, number> = {}) {
  return {
    total_generalized_cost: 0,
    distance_cost: 0,
    labor_cost: 0,
    fuel_cost: 0,
    fixed_route_cost: 0,
    lateness_penalty: 0,
    overtime_penalty: 0,
    unserved_penalty: 0,
    late_minutes: 0,
    overtime_minutes: 0,
    active_vehicle_count: 0,
    ...overrides,
  };
}

describe("useOptimizationRunPreview", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("loads preview data when enabled", async () => {
    mockGetOptimizationRunPreview.mockResolvedValue({
      optimization_run_id: "run-1",
      run_status: "PENDING_APPROVAL",
      solver_status: "FEASIBLE",
      routes: [],
      unassigned_order_ids: [],
      summary: {
        total_routes: 1,
        total_orders: 1,
        total_unassigned: 0,
        total_distance_km: 12.5,
        total_violations: 0,
        violation_breakdown: {},
        on_time_rate: 1,
        cost_breakdown: makeEconomicBreakdown({ total_generalized_cost: 1234 }),
        cost_delta_vs_current: makeEconomicBreakdown({ total_generalized_cost: -45 }),
      },
      explainability: {
        no_significant_changes: false,
        summary: "1 new route.",
        orders_newly_assigned: 1,
        orders_moved_between_couriers: 0,
        orders_now_unassigned: 0,
        orders_reordered: 0,
        routes_created: 1,
        routes_removed: 0,
        changed_orders: [],
        reasons: [],
        attention_items: [],
      },
    } as never);

    const { result } = renderHook(() =>
      useOptimizationRunPreview({
        token: "token-123",
        runId: "run-1",
        enabled: true,
      }),
    );

    await waitFor(() => {
      expect(result.current.preview?.optimization_run_id).toBe("run-1");
    });

    expect(mockGetOptimizationRunPreview).toHaveBeenCalledWith("token-123", "run-1");
    expect(result.current.error).toBeNull();
  });

  it("clears preview state when disabled", async () => {
    const { result, rerender } = renderHook(
      ({ enabled }) =>
        useOptimizationRunPreview({
          token: "token-123",
          runId: enabled ? "run-1" : null,
          enabled,
        }),
      {
        initialProps: { enabled: true },
      },
    );

    mockGetOptimizationRunPreview.mockResolvedValue({
      optimization_run_id: "run-1",
      run_status: "PENDING_APPROVAL",
      solver_status: "FEASIBLE",
      routes: [],
      unassigned_order_ids: [],
      summary: {
        total_routes: 0,
        total_orders: 0,
        total_unassigned: 0,
        total_distance_km: 0,
        total_violations: 0,
        violation_breakdown: {},
        on_time_rate: null,
        cost_breakdown: makeEconomicBreakdown(),
        cost_delta_vs_current: makeEconomicBreakdown(),
      },
      explainability: {
        no_significant_changes: true,
        summary: "No significant changes.",
        orders_newly_assigned: 0,
        orders_moved_between_couriers: 0,
        orders_now_unassigned: 0,
        orders_reordered: 0,
        routes_created: 0,
        routes_removed: 0,
        changed_orders: [],
        reasons: [],
        attention_items: [],
      },
    } as never);

    await waitFor(() => {
      expect(result.current.preview?.optimization_run_id).toBe("run-1");
    });

    rerender({ enabled: false });

    await waitFor(() => {
      expect(result.current.preview).toBeNull();
    });
  });

  it("captures fetch errors and exposes retry", async () => {
    mockGetOptimizationRunPreview.mockRejectedValueOnce(new Error("Preview unavailable"));
    mockGetOptimizationRunPreview.mockResolvedValueOnce({
      optimization_run_id: "run-2",
      run_status: "PENDING_APPROVAL",
      solver_status: "FEASIBLE",
      routes: [],
      unassigned_order_ids: [],
      summary: {
        total_routes: 0,
        total_orders: 0,
        total_unassigned: 0,
        total_distance_km: 0,
        total_violations: 0,
        violation_breakdown: {},
        on_time_rate: null,
        cost_breakdown: makeEconomicBreakdown(),
        cost_delta_vs_current: makeEconomicBreakdown(),
      },
      explainability: {
        no_significant_changes: true,
        summary: "No significant changes.",
        orders_newly_assigned: 0,
        orders_moved_between_couriers: 0,
        orders_now_unassigned: 0,
        orders_reordered: 0,
        routes_created: 0,
        routes_removed: 0,
        changed_orders: [],
        reasons: [],
        attention_items: [],
      },
    } as never);

    const { result } = renderHook(() =>
      useOptimizationRunPreview({
        token: "token-123",
        runId: "run-2",
        enabled: true,
      }),
    );

    await waitFor(() => {
      expect(result.current.error).toBe("Preview unavailable");
    });

    await act(async () => {
      await result.current.refetch();
    });

    expect(result.current.error).toBeNull();
    expect(result.current.preview?.optimization_run_id).toBe("run-2");
  });
});
