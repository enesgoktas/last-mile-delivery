import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "../../test/renderWithProviders";
import { LiveOpsPage } from "../LiveOpsPage";
import { usePolling } from "../../hooks/usePolling";
import { useLiveOpsExceptions } from "../../hooks/useLiveOpsExceptions";
import { useOptimizationRunPreview } from "../../hooks/useOptimizationRunPreview";
import type { AnalyticsSummary, OptimizationRun, Route as RouteModel } from "../../types";

vi.mock("../../state/AuthContext", () => ({
  useAuth: () => ({ token: "test-tok" }),
}));

vi.mock("../../hooks/usePolling");
vi.mock("../../hooks/useLiveOpsExceptions");
vi.mock("../../hooks/useOptimizationRunPreview");

const mockUsePolling = vi.mocked(usePolling);
const mockUseLiveOpsExceptions = vi.mocked(useLiveOpsExceptions);
const mockUseOptimizationRunPreview = vi.mocked(useOptimizationRunPreview);

vi.mock("../../hooks/useCourierLocations", () => ({
  useCourierLocations: () => [],
}));
vi.mock("../../hooks/useRouteGeometries", () => ({
  useRouteGeometries: () => new Map(),
}));

vi.mock("../../components/CoordinateMap", () => ({
  CoordinateMap: () => <div data-testid="coordinate-map" />,
}));
vi.mock("../../components/RouteEditorPanel", () => ({
  RouteEditorPanel: () => <div data-testid="route-editor-panel" />,
}));
vi.mock("../../components/StatusBadge", () => ({
  StatusBadge: ({ value }: { value: string }) => <span>{value}</span>,
}));

const noData = { data: null, isLoading: false, error: null, refetch: vi.fn() };
const HOOKS_PER_RENDER = 4;

function setupPollingCalls(values: ReturnType<typeof usePolling>[]) {
  let callCount = 0;
  mockUsePolling.mockImplementation(() => {
    const slot = callCount % HOOKS_PER_RENDER;
    callCount += 1;
    return values[slot] ?? noData;
  });
}

const mockSummary: AnalyticsSummary = {
  total_orders: 50,
  delivered_orders: 30,
  failed_orders: 5,
  pending_orders: 15,
  completion_rate: 0.6,
  failed_rate: 0.1,
  on_time_rate: 0.88,
};

function makeRoute(overrides: Record<string, unknown> = {}): RouteModel {
  return {
    id: "route-aaa",
    courier_id: "c1",
    status: "ACTIVE",
    stops: [],
    started_at: "2026-03-01T08:00:00Z",
    completed_at: null,
    created_at: "2026-03-01T07:00:00Z",
    actual_distance_km: null,
    optimization_run_id: null,
    planned_distance_km: 18.5,
    ...overrides,
  } as RouteModel;
}

function makePendingRun(overrides: Partial<OptimizationRun> = {}): OptimizationRun {
  return {
    id: "run-1",
    status: "PENDING_APPROVAL",
    routes_created: 3,
    unassigned_count: 2,
    started_at: "2026-03-01T06:00:00Z",
    finished_at: null,
    run_type: "BATCH",
    solver_status: "ROUTING_SUCCESS",
    ...overrides,
  } as OptimizationRun;
}

function makeExceptionState(overrides: Record<string, unknown> = {}) {
  return {
    counts: {
      pending_approval_runs: 0,
      critical_alerts: 0,
      failed_orders: 0,
      unassigned_orders: 0,
      total_open_items: 0,
    },
    criticalAlerts: [],
    error: null,
    failedOrders: [],
    hasAttention: false,
    isLoading: false,
    optimizing: false,
    pendingRun: null,
    unassignedOrders: [],
    acknowledgingAlertId: null,
    assigningOrderId: null,
    pinningOrderId: null,
    cancellingOrderId: null,
    rescheduleTarget: null,
    rescheduleEarliest: "",
    rescheduleLatest: "",
    rescheduling: false,
    setRescheduleEarliest: vi.fn(),
    setRescheduleLatest: vi.fn(),
    openRescheduleModal: vi.fn(),
    closeRescheduleModal: vi.fn(),
    handleOptimizePreview: vi.fn(),
    handleApprovePendingRun: vi.fn().mockResolvedValue(true),
    handleRejectPendingRun: vi.fn().mockResolvedValue(true),
    handleAcknowledgeAlert: vi.fn(),
    handleAssignOrder: vi.fn().mockResolvedValue(undefined),
    handleSetPreferredCourier: vi.fn().mockResolvedValue(undefined),
    handleCancelFailedOrder: vi.fn(),
    handleConfirmReschedule: vi.fn(),
    refetchExceptionSummary: vi.fn(),
    ...overrides,
  };
}

function makePreview(overrides: Record<string, unknown> = {}) {
  return {
    optimization_run_id: "run-1",
    run_status: "PENDING_APPROVAL",
    solver_status: "FEASIBLE",
    routes: [{
      route_id: "draft-route-1",
      courier_id: "courier-1",
      vehicle_type: "CAR_VAN",
      planned_distance_km: 18.5,
      stop_count: 2,
      order_count: 1,
      total_weight_kg: 3.2,
      eta: {
        earliest_departure: "2026-03-01T09:00:00Z",
        latest_arrival: "2026-03-01T10:00:00Z",
        total_duration_minutes: 60,
      },
      violations: [],
      stops: [{
        stop_id: "stop-1",
        sequence_number: 1,
        stop_type: "PICKUP",
        lat: 41,
        lon: 29,
        address: "Warehouse",
        planned_arrival: "2026-03-01T09:00:00Z",
        order: {
          order_id: "order-1",
          pickup_address: "Warehouse",
          delivery_address: "123 Main St",
          weight_kg: 3.2,
          is_fragile: false,
          is_temp_sensitive: false,
          priority: "NORMAL",
          earliest_delivery: "2026-03-01T09:00:00Z",
          latest_delivery: "2026-03-01T12:00:00Z",
          on_time: true,
        },
      }],
      explainability: {
        change_type: "NEW_ROUTE",
        summary: "New draft route with 1 previously unassigned order.",
        previous_route_id: null,
        previous_courier_id: null,
        changed_orders: [{
          order_id: "order-1",
          delivery_address: "123 Main St",
          priority: "NORMAL",
          change_type: "ASSIGNED",
          from_courier_id: null,
          to_courier_id: "courier-1",
          from_route_id: null,
          to_route_id: "draft-route-1",
        }],
        reasons: [{ code: "ASSIGNED_PENDING_WORK", tone: "INFO", message: "1 pending order would be assigned on this route." }],
        attention_items: [],
      },
      cost_breakdown: {
        total_generalized_cost: 1250,
        distance_cost: 300,
        labor_cost: 400,
        fuel_cost: 150,
        fixed_route_cost: 250,
        lateness_penalty: 100,
        overtime_penalty: 50,
        unserved_penalty: 0,
        late_minutes: 8,
        overtime_minutes: 4,
        active_vehicle_count: 1,
      },
      cost_delta_vs_current: {
        total_generalized_cost: -75,
        distance_cost: -20,
        labor_cost: -15,
        fuel_cost: -10,
        fixed_route_cost: 0,
        lateness_penalty: -30,
        overtime_penalty: 0,
        unserved_penalty: 0,
        late_minutes: -2,
        overtime_minutes: 0,
        active_vehicle_count: 0,
      },
    }],
    unassigned_order_ids: [],
    summary: {
      total_routes: 1,
      total_orders: 1,
      total_unassigned: 0,
      total_distance_km: 18.5,
      total_violations: 0,
      violation_breakdown: {},
      on_time_rate: 1,
      cost_breakdown: {
        total_generalized_cost: 1250,
        distance_cost: 300,
        labor_cost: 400,
        fuel_cost: 150,
        fixed_route_cost: 250,
        lateness_penalty: 100,
        overtime_penalty: 50,
        unserved_penalty: 0,
        late_minutes: 8,
        overtime_minutes: 4,
        active_vehicle_count: 1,
      },
      cost_delta_vs_current: {
        total_generalized_cost: -75,
        distance_cost: -20,
        labor_cost: -15,
        fuel_cost: -10,
        fixed_route_cost: 0,
        lateness_penalty: -30,
        overtime_penalty: 0,
        unserved_penalty: 0,
        late_minutes: -2,
        overtime_minutes: 0,
        active_vehicle_count: 0,
      },
    },
    explainability: {
      no_significant_changes: false,
      summary: "1 new route. 1 newly assigned order.",
      orders_newly_assigned: 1,
      orders_moved_between_couriers: 0,
      orders_now_unassigned: 0,
      orders_reordered: 0,
      routes_created: 1,
      routes_removed: 0,
      changed_orders: [{
        order_id: "order-1",
        delivery_address: "123 Main St",
        priority: "NORMAL",
        change_type: "ASSIGNED",
        from_courier_id: null,
        to_courier_id: "courier-1",
        from_route_id: null,
        to_route_id: "draft-route-1",
      }],
      reasons: [{ code: "ASSIGNED_PENDING_WORK", tone: "INFO", message: "The preview creates new capacity for pending work." }],
      attention_items: [],
    },
    ...overrides,
  };
}

describe("LiveOpsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUsePolling.mockReturnValue(noData);
    mockUseLiveOpsExceptions.mockReturnValue(makeExceptionState() as never);
    mockUseOptimizationRunPreview.mockReturnValue({
      preview: null,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as never);
  });

  it("renders the stats header and map", () => {
    setupPollingCalls([
      { data: mockSummary, isLoading: false, error: null, refetch: vi.fn() },
      noData,
      noData,
      noData,
    ]);

    renderWithProviders(<LiveOpsPage />);

    expect(screen.getByText("50")).toBeInTheDocument();
    expect(screen.getByText("30")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getByTestId("coordinate-map")).toBeInTheDocument();
  });

  it("shows the exception center when there are failed orders", () => {
    mockUseLiveOpsExceptions.mockReturnValue(
      makeExceptionState({
        counts: {
          pending_approval_runs: 0,
          critical_alerts: 0,
          failed_orders: 1,
          unassigned_orders: 0,
          total_open_items: 1,
        },
        failedOrders: [{
          id: "order-failed",
          status: "FAILED",
          type: "ON_DEMAND",
          priority: "NORMAL",
          pickup_address: "Pickup",
          delivery_address: "123 Main St",
          customer_email: "customer@example.com",
          earliest_delivery: "2026-03-01T09:00:00Z",
          latest_delivery: "2026-03-01T17:00:00Z",
          attempt_count: 2,
          max_attempts: 3,
          failed_at: "2026-03-01T11:00:00Z",
          created_at: "2026-03-01T08:00:00Z",
        }],
        hasAttention: true,
      }) as never,
    );

    renderWithProviders(<LiveOpsPage />);

    expect(screen.getByText("Needs attention")).toBeInTheDocument();
    expect(screen.getByText(/1 failed today/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /reschedule/i })).toBeInTheDocument();
  });

  it("shows pending run review in the exception center", () => {
    mockUseLiveOpsExceptions.mockReturnValue(
      makeExceptionState({
        counts: {
          pending_approval_runs: 1,
          critical_alerts: 0,
          failed_orders: 0,
          unassigned_orders: 0,
          total_open_items: 1,
        },
        pendingRun: makePendingRun({ routes_created: 4, unassigned_count: 1 }),
        hasAttention: true,
      }) as never,
    );

    renderWithProviders(<LiveOpsPage />);

    expect(screen.getByText(/4 routes · 1 unassigned/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^review$/i })).toBeInTheDocument();
  });

  it("opens the optimization overlay and approves a run", async () => {
    const handleApprovePendingRun = vi.fn().mockResolvedValue(true);
    mockUseLiveOpsExceptions.mockReturnValue(
      makeExceptionState({
        counts: {
          pending_approval_runs: 1,
          critical_alerts: 0,
          failed_orders: 0,
          unassigned_orders: 0,
          total_open_items: 1,
        },
        pendingRun: makePendingRun({ id: "run-99" }),
        handleApprovePendingRun,
        hasAttention: true,
      }) as never,
    );
    mockUseOptimizationRunPreview.mockReturnValue({
      preview: makePreview(),
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    setupPollingCalls([
      noData,
      noData,
      { data: [], isLoading: false, error: null, refetch: vi.fn() },
      noData,
    ]);

    renderWithProviders(<LiveOpsPage />);
    fireEvent.click(screen.getByRole("button", { name: /^review$/i }));
    fireEvent.click(await screen.findByRole("button", { name: /approve all routes/i }));

    await waitFor(() => expect(handleApprovePendingRun).toHaveBeenCalled());
  });

  it("rejects a pending optimization run", async () => {
    const handleRejectPendingRun = vi.fn().mockResolvedValue(true);
    mockUseLiveOpsExceptions.mockReturnValue(
      makeExceptionState({
        counts: {
          pending_approval_runs: 1,
          critical_alerts: 0,
          failed_orders: 0,
          unassigned_orders: 0,
          total_open_items: 1,
        },
        pendingRun: makePendingRun({ id: "run-88" }),
        handleRejectPendingRun,
        hasAttention: true,
      }) as never,
    );
    mockUseOptimizationRunPreview.mockReturnValue({
      preview: makePreview(),
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    setupPollingCalls([
      noData,
      noData,
      { data: [], isLoading: false, error: null, refetch: vi.fn() },
      noData,
    ]);

    renderWithProviders(<LiveOpsPage />);
    fireEvent.click(screen.getByRole("button", { name: /^review$/i }));
    fireEvent.click(await screen.findByRole("button", { name: /reject all/i }));

    await waitFor(() => expect(handleRejectPendingRun).toHaveBeenCalled());
  });

  it("shows run-level explainability from the preview overlay", async () => {
    mockUseLiveOpsExceptions.mockReturnValue(
      makeExceptionState({
        counts: {
          pending_approval_runs: 1,
          critical_alerts: 0,
          failed_orders: 0,
          unassigned_orders: 0,
          total_open_items: 1,
        },
        pendingRun: makePendingRun({ id: "run-1" }),
        hasAttention: true,
      }) as never,
    );
    mockUseOptimizationRunPreview.mockReturnValue({
      preview: makePreview(),
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    setupPollingCalls([noData, noData, noData, noData]);

    renderWithProviders(<LiveOpsPage />);
    fireEvent.click(screen.getByRole("button", { name: /^review$/i }));
    const whyButtons = await screen.findAllByRole("button", { name: /^why\?$/i });
    fireEvent.click(whyButtons[0]);

    expect(screen.getByText(/1 new route\. 1 newly assigned order\./i)).toBeInTheDocument();
    expect(screen.getByText(/the preview creates new capacity for pending work/i)).toBeInTheDocument();
  });

  it("shows route-level explainability from the preview overlay", async () => {
    mockUseLiveOpsExceptions.mockReturnValue(
      makeExceptionState({
        counts: {
          pending_approval_runs: 1,
          critical_alerts: 0,
          failed_orders: 0,
          unassigned_orders: 0,
          total_open_items: 1,
        },
        pendingRun: makePendingRun({ id: "run-1" }),
        hasAttention: true,
      }) as never,
    );
    mockUseOptimizationRunPreview.mockReturnValue({
      preview: makePreview(),
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    setupPollingCalls([noData, noData, noData, noData]);

    renderWithProviders(<LiveOpsPage />);
    fireEvent.click(screen.getByRole("button", { name: /^review$/i }));
    const whyButtons = await screen.findAllByRole("button", { name: /^why\?$/i });
    fireEvent.click(whyButtons[1]);

    expect(screen.getByText(/new draft route with 1 previously unassigned order/i)).toBeInTheDocument();
    expect(screen.getByText(/1 pending order would be assigned on this route/i)).toBeInTheDocument();
  });

  it("shows assign optimally from the exception center when there are unassigned orders", async () => {
    mockUseLiveOpsExceptions.mockReturnValue(
      makeExceptionState({
        counts: {
          pending_approval_runs: 0,
          critical_alerts: 0,
          failed_orders: 0,
          unassigned_orders: 1,
          total_open_items: 1,
        },
        unassignedOrders: [{
          id: "order-1",
          status: "PENDING",
          type: "ON_DEMAND",
          priority: "URGENT",
          pickup_address: "Pickup",
          delivery_address: "Deliver here",
          customer_email: "ops@example.com",
          earliest_delivery: "2026-03-01T09:00:00Z",
          latest_delivery: "2026-03-01T17:00:00Z",
          attempt_count: 0,
          max_attempts: 3,
          created_at: "2026-03-01T08:00:00Z",
        }],
        hasAttention: true,
      }) as never,
    );

    renderWithProviders(<LiveOpsPage />);

    expect(screen.getByRole("button", { name: /assign optimally/i })).toBeInTheDocument();
  });

  it("acknowledges a critical alert from the exception center", async () => {
    const handleAcknowledgeAlert = vi.fn();
    mockUseLiveOpsExceptions.mockReturnValue(
      makeExceptionState({
        counts: {
          pending_approval_runs: 0,
          critical_alerts: 1,
          failed_orders: 0,
          unassigned_orders: 0,
          total_open_items: 1,
        },
        criticalAlerts: [{
          id: "alert-1",
          route_id: "route-xyz",
          stop_id: null,
          order_id: "order-1",
          courier_id: "courier-1",
          severity: "CRITICAL",
          status: "ACTIVE",
          alert_type: "LATE_DELIVERY",
          message: "Courier is running late",
          estimated_delay_seconds: 1800,
          created_at: "2026-03-01T09:00:00Z",
          updated_at: "2026-03-01T09:00:00Z",
          acknowledged_at: null,
        }],
        handleAcknowledgeAlert,
        hasAttention: true,
      }) as never,
    );

    renderWithProviders(<LiveOpsPage />);
    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));

    expect(handleAcknowledgeAlert).toHaveBeenCalledWith("alert-1");
  });

  it("assigns an unassigned order from the exception center", async () => {
    const handleAssignOrder = vi.fn().mockResolvedValue(undefined);
    mockUseLiveOpsExceptions.mockReturnValue(
      makeExceptionState({
        counts: {
          pending_approval_runs: 0,
          critical_alerts: 0,
          failed_orders: 0,
          unassigned_orders: 1,
          total_open_items: 1,
        },
        unassignedOrders: [{
          id: "order-1",
          status: "PENDING",
          type: "ON_DEMAND",
          priority: "URGENT",
          pickup_address: "Pickup",
          delivery_address: "Deliver here",
          customer_email: "ops@example.com",
          earliest_delivery: "2026-03-01T09:00:00Z",
          latest_delivery: "2026-03-01T17:00:00Z",
          attempt_count: 0,
          max_attempts: 3,
          created_at: "2026-03-01T08:00:00Z",
        }],
        handleAssignOrder,
        hasAttention: true,
      }) as never,
    );
    setupPollingCalls([
      noData,
      {
        data: [{ id: "courier-1", availability_status: "ONLINE", vehicle_type: "CAR_VAN" }],
        isLoading: false,
        error: null,
        refetch: vi.fn(),
      },
      noData,
      noData,
    ]);

    renderWithProviders(<LiveOpsPage />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "courier-1" } });
    fireEvent.click(screen.getByRole("button", { name: /^assign$/i }));

    await waitFor(() => expect(handleAssignOrder).toHaveBeenCalledWith("order-1", "courier-1"));
  });

  it("pins an unassigned order to a preferred courier", async () => {
    const handleSetPreferredCourier = vi.fn().mockResolvedValue(undefined);
    mockUseLiveOpsExceptions.mockReturnValue(
      makeExceptionState({
        counts: {
          pending_approval_runs: 0,
          critical_alerts: 0,
          failed_orders: 0,
          unassigned_orders: 1,
          total_open_items: 1,
        },
        unassignedOrders: [{
          id: "order-1",
          status: "PENDING",
          type: "ON_DEMAND",
          priority: "URGENT",
          pickup_address: "Pickup",
          delivery_address: "Deliver here",
          customer_email: "ops@example.com",
          earliest_delivery: "2026-03-01T09:00:00Z",
          latest_delivery: "2026-03-01T17:00:00Z",
          attempt_count: 0,
          max_attempts: 3,
          created_at: "2026-03-01T08:00:00Z",
        }],
        handleSetPreferredCourier,
        hasAttention: true,
      }) as never,
    );
    setupPollingCalls([
      noData,
      {
        data: [{ id: "courier-1", availability_status: "ONLINE", vehicle_type: "CAR_VAN" }],
        isLoading: false,
        error: null,
        refetch: vi.fn(),
      },
      noData,
      noData,
    ]);

    renderWithProviders(<LiveOpsPage />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "courier-1" } });
    fireEvent.click(screen.getByRole("button", { name: /^pin$/i }));

    await waitFor(() => expect(handleSetPreferredCourier).toHaveBeenCalledWith("order-1", "courier-1"));
  });

  it("opens failed-order reschedule flow from the exception center", async () => {
    const openRescheduleModal = vi.fn();
    mockUseLiveOpsExceptions.mockReturnValue(
      makeExceptionState({
        counts: {
          pending_approval_runs: 0,
          critical_alerts: 0,
          failed_orders: 1,
          unassigned_orders: 0,
          total_open_items: 1,
        },
        failedOrders: [{
          id: "order-fail",
          status: "FAILED",
          type: "ON_DEMAND",
          priority: "NORMAL",
          pickup_address: "Pickup",
          delivery_address: "Deliver later",
          customer_email: "ops@example.com",
          earliest_delivery: "2026-03-01T09:00:00Z",
          latest_delivery: "2026-03-01T17:00:00Z",
          attempt_count: 1,
          max_attempts: 3,
          failed_at: "2026-03-01T11:00:00Z",
          created_at: "2026-03-01T08:00:00Z",
        }],
        openRescheduleModal,
        hasAttention: true,
      }) as never,
    );

    renderWithProviders(<LiveOpsPage />);
    fireEvent.click(screen.getByRole("button", { name: /reschedule/i }));

    expect(openRescheduleModal).toHaveBeenCalledWith(
      expect.objectContaining({ id: "order-fail" }),
    );
  });

  it("renders the routes section and activity feed", () => {
    const route = makeRoute({
      id: "route-act123",
      status: "ACTIVE",
      started_at: "2026-03-01T08:00:00Z",
      stops: [],
    });
    mockUseLiveOpsExceptions.mockReturnValue(
      makeExceptionState({
        counts: {
          pending_approval_runs: 0,
          critical_alerts: 0,
          failed_orders: 1,
          unassigned_orders: 0,
          total_open_items: 1,
        },
        failedOrders: [{ id: "order-fail123", failed_at: "2026-03-01T09:00:00Z", delivery_address: "5 Oak Ave, Town" }],
        hasAttention: true,
      }) as never,
    );
    setupPollingCalls([
      noData,
      noData,
      { data: [route], isLoading: false, error: null, refetch: vi.fn() },
      noData,
    ]);

    renderWithProviders(<LiveOpsPage />);

    expect(screen.getByText("Routes")).toBeInTheDocument();
    expect(screen.getByText(/Courier c1 — route route-ac · 0 stops/)).toBeInTheDocument();
  });
});
