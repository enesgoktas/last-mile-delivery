import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  rescheduleOrder,
  setPreferredCourier,
  triggerOptimizePreview,
} from "../../api/client";
import { usePolling } from "../usePolling";
import { useLiveOpsExceptions } from "../useLiveOpsExceptions";

vi.mock("../usePolling");
vi.mock("../../api/client", () => ({
  acknowledgeSLAAlert: vi.fn(),
  approveOptimizationRun: vi.fn(),
  assignOrder: vi.fn(),
  cancelOrder: vi.fn(),
  getExceptionCenterSummary: vi.fn(),
  rejectOptimizationRun: vi.fn(),
  rescheduleOrder: vi.fn(),
  setPreferredCourier: vi.fn(),
  triggerOptimizePreview: vi.fn(),
}));

const mockUsePolling = vi.mocked(usePolling);
const mockSetPreferredCourier = vi.mocked(setPreferredCourier);
const mockTriggerOptimizePreview = vi.mocked(triggerOptimizePreview);
const mockRescheduleOrder = vi.mocked(rescheduleOrder);

const emptySummary = {
  counts: {
    pending_approval_runs: 0,
    critical_alerts: 0,
    failed_orders: 0,
    unassigned_orders: 0,
    total_open_items: 0,
  },
  pending_run: null,
  critical_alerts: [],
  failed_orders: [],
  unassigned_orders: [],
};

describe("useLiveOpsExceptions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useRealTimers();
    mockUsePolling.mockReturnValue({
      data: emptySummary,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
  });

  it("pins a preferred courier and refreshes exception data", async () => {
    const refetchExceptionSummary = vi.fn();
    mockUsePolling.mockReturnValue({
      data: emptySummary,
      isLoading: false,
      error: null,
      refetch: refetchExceptionSummary,
    });
    mockSetPreferredCourier.mockResolvedValue({} as never);
    const addToast = vi.fn();

    const { result } = renderHook(() =>
      useLiveOpsExceptions({
        token: "test-token",
        addToast,
        refetchRoutes: vi.fn(),
      }),
    );

    await act(async () => {
      await result.current.handleSetPreferredCourier("order-1", "courier-1");
    });

    expect(mockSetPreferredCourier).toHaveBeenCalledWith("test-token", "order-1", "courier-1");
    expect(addToast).toHaveBeenCalledWith(
      "Preferred courier pinned for the next optimization run",
      "success",
    );
    expect(refetchExceptionSummary).toHaveBeenCalled();
  });

  it("schedules follow-up refreshes after optimization preview", async () => {
    vi.useFakeTimers();
    const refetchExceptionSummary = vi.fn();
    const refetchRoutes = vi.fn();
    mockUsePolling.mockReturnValue({
      data: emptySummary,
      isLoading: false,
      error: null,
      refetch: refetchExceptionSummary,
    });
    mockTriggerOptimizePreview.mockResolvedValue({ message: "Optimization queued" } as never);
    const addToast = vi.fn();

    const { result } = renderHook(() =>
      useLiveOpsExceptions({
        token: "test-token",
        addToast,
        refetchRoutes,
      }),
    );

    await act(async () => {
      await result.current.handleOptimizePreview();
    });

    expect(mockTriggerOptimizePreview).toHaveBeenCalledWith("test-token");
    expect(addToast).toHaveBeenCalledWith("Optimization queued", "info");

    act(() => {
      vi.advanceTimersByTime(5_000);
    });
    expect(refetchExceptionSummary).toHaveBeenCalledTimes(1);
    expect(refetchRoutes).toHaveBeenCalledTimes(1);

    act(() => {
      vi.advanceTimersByTime(7_000);
    });
    expect(refetchExceptionSummary).toHaveBeenCalledTimes(2);
    expect(refetchRoutes).toHaveBeenCalledTimes(2);
  });

  it("rejects invalid reschedule windows before calling the API", async () => {
    mockUsePolling.mockReturnValue({
      data: {
        ...emptySummary,
        failed_orders: [
          {
            id: "order-1",
            status: "FAILED",
            type: "ON_DEMAND",
            priority: "NORMAL",
            pickup_address: "Warehouse A",
            delivery_address: "Customer Address",
            customer_email: "ops@example.com",
            earliest_delivery: "2026-03-01T09:00:00Z",
            latest_delivery: "2026-03-01T10:00:00Z",
            attempt_count: 1,
            max_attempts: 3,
            failed_at: "2026-03-01T10:15:00Z",
            created_at: "2026-03-01T08:00:00Z",
          },
        ],
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    const addToast = vi.fn();

    const { result } = renderHook(() =>
      useLiveOpsExceptions({
        token: "test-token",
        addToast,
        refetchRoutes: vi.fn(),
      }),
    );

    act(() => {
      result.current.openRescheduleModal({
        id: "order-1",
        status: "FAILED",
        type: "ON_DEMAND",
        priority: "NORMAL",
        pickup_address: "Warehouse A",
        delivery_address: "Customer Address",
        customer_email: "ops@example.com",
        earliest_delivery: "2026-03-01T09:00:00Z",
        latest_delivery: "2026-03-01T10:00:00Z",
        attempt_count: 1,
        max_attempts: 3,
        failed_at: "2026-03-01T10:15:00Z",
        created_at: "2026-03-01T08:00:00Z",
      });
    });

    act(() => {
      result.current.setRescheduleEarliest("2026-03-02T11:00");
      result.current.setRescheduleLatest("2026-03-02T10:00");
    });

    let success = true;
    await act(async () => {
      success = await result.current.handleConfirmReschedule();
    });

    expect(success).toBe(false);
    expect(mockRescheduleOrder).not.toHaveBeenCalled();
    expect(addToast).toHaveBeenCalledWith(
      "Latest delivery must be after earliest delivery",
      "error",
    );
  });
});
