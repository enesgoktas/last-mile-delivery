import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "../../test/renderWithProviders";
import { OrdersPage } from "../OrdersPage";
import { usePolling } from "../../hooks/usePolling";
import {
  createOrderReturn,
  createRouteScenarioComparison,
  getOperationsPolicy,
  getOrder,
  getOrderLegs,
  getRouteScenarioPreview,
  listOrdersPaginated,
  triggerAssignRange,
  getOptimizationTaskStatus,
} from "../../api/client";
import { useRouteGeometries } from "../../hooks/useRouteGeometries";
import type { OperationsPolicy, OptimizationRunPreview, Order, OrderLeg, RouteScenarioComparison, RouteScenarioSummary } from "../../types";

vi.mock("../../state/AuthContext", () => ({
  useAuth: () => ({ token: "test-token", role: "DISPATCHER" }),
}));

// Mock the entire api/client so no real network calls are made.
// usePolling is mocked separately to control what data the page sees.
vi.mock("../../api/client", () => ({
  listOrdersPaginated: vi.fn(),
  listOrders: vi.fn(),
  listCouriers: vi.fn(),
  listDepots: vi.fn(),
  createOrder: vi.fn(),
  createOrderReturn: vi.fn(),
  getOrderLegs: vi.fn().mockResolvedValue([]),
  getOrder: vi.fn(),
  getOrderHistory: vi.fn().mockResolvedValue([]),
  assignOrder: vi.fn(),
  cancelOrder: vi.fn(),
  rescheduleOrder: vi.fn(),
  importOrdersPreview: vi.fn(),
  importOrdersPublish: vi.fn(),
  listOrderPackages: vi.fn().mockResolvedValue([]),
  getPackageEvents: vi.fn(),
  getOperationsPolicy: vi.fn(),
  getOptimizationTaskStatus: vi.fn(),
  triggerAssignOptimal: vi.fn(),
  triggerAssignRange: vi.fn(),
  triggerUnassign: vi.fn(),
  dispatchOrderNow: vi.fn(),
  createRouteScenarioComparison: vi.fn(),
  getRouteScenarioComparison: vi.fn(),
  getRouteScenarioPreview: vi.fn(),
  selectRouteScenario: vi.fn(),
  deleteRouteScenarioComparison: vi.fn(),
  geocodeSearch: vi.fn().mockResolvedValue([]),
}));

vi.mock("../../hooks/usePolling");
vi.mock("../../hooks/useRouteGeometries", () => ({
  useRouteGeometries: vi.fn(() => new Map()),
}));
const mockUsePolling = vi.mocked(usePolling);
const mockUseRouteGeometries = vi.mocked(useRouteGeometries);

const noData = { data: null, isLoading: false, error: null, refetch: vi.fn() };
const mockedGetOperationsPolicy = vi.mocked(getOperationsPolicy);

function makePolicy(overrides: Partial<OperationsPolicy> = {}): OperationsPolicy {
  return {
    dispatch: {
      dispatch_mode: "HYBRID",
      assignment_strategy: "NEAREST_COURIER",
      route_approval: { mode: "NONE", large_route_stop_threshold: 12 },
    },
    optimization: {
      routing_objective: "FUEL_AWARE",
      preset_key: "BALANCED",
      economic_batch_optimizer_enabled: false,
      fuel_priority_multiplier: 0,
      shadow_mode_enabled: true,
      internal_override_enabled: false,
      vehicle_class_costs: {
        CAR_VAN: {
          fixed_route_cost: 750,
          distance_cost_per_km: 18,
          labor_cost_per_minute: 12,
          fuel_cost_per_km: 7,
          overtime_cost_per_minute: 18,
        },
      },
      fuel_prices: {
        GASOLINE: 35,
        DIESEL: 38,
        LPG: 20,
        ELECTRIC: 12,
        HYBRID: 30,
      },
      lateness_penalty_per_minute: 750,
      overtime_penalty_per_minute: 60,
      unserved_penalty: 1_000_000,
      max_lateness_minutes: 90,
      max_overtime_minutes: 60,
      priority_penalty_multipliers: {
        LOW: 0.75,
        NORMAL: 1,
        HIGH: 1.5,
        URGENT: 2,
      },
      decomposition_order_threshold: 250,
      fleet_utilization: "FULL_FLEET" as const,
    },
    delivery_workflow: {
      require_pickup_step: true,
      require_arrival_scan: true,
      pre_arrival_call: "SUGGESTED",
      wait_time_minutes: 10,
      safe_drop_policy: "ALLOWED",
      customer_status_labels: {},
    },
    proof_of_delivery: {
      defaults: {
        require_photo: true,
        photo_count: 1,
        require_signature: false,
        require_otp: false,
        receiver_name_mode: "OPTIONAL",
        notes_mode: "OPTIONAL",
      },
      by_order_type: {},
    },
    failure_handling: {
      allowed_standard_reasons: ["NOT_HOME", "WRONG_ADDRESS", "REFUSED", "DAMAGED", "ACCESS_DENIED", "OTHER"],
      custom_reasons: [],
      require_notes_for_other: true,
      post_failure_action: "CUSTOMER_RESCHEDULE",
      max_attempts: 3,
      retry_timing: "MANUAL_ONLY",
      escalation_hours: 48,
      self_service_mode: "RESCHEDULE_ONLY",
    },
    notifications: {
      language: "en",
      branding_name: "LastMile",
      quiet_hours_enabled: false,
      quiet_hours_start: "22:00",
      quiet_hours_end: "08:00",
      timezone: "UTC",
      eta_visibility: "LIVE_MAP",
      event_channels: {},
    },
    order_form: {
      standard_fields: {
        customer_phone: "OPTIONAL",
        notes: "OPTIONAL",
        external_ref: "OPTIONAL",
        depot_id: "OPTIONAL",
        is_fragile: "OPTIONAL",
        is_temp_sensitive: "OPTIONAL",
      },
      custom_fields: [],
    },
    team_permissions: {
      OWNER: { cancel_order: true, edit_order: true, reassign_order: true, override_pod: true, manage_settings: true },
      ADMIN: { cancel_order: true, edit_order: true, reassign_order: true, override_pod: true, manage_settings: true },
      DISPATCHER: { cancel_order: true, edit_order: true, reassign_order: true, override_pod: false, manage_settings: false },
      COURIER: { cancel_order: false, edit_order: false, reassign_order: false, override_pod: false, manage_settings: false },
      COMPANY_ADMIN: { cancel_order: false, edit_order: false, reassign_order: false, override_pod: false, manage_settings: false },
    },
    feature_flags: {
      deliveries_enabled: true,
      rides_enabled: false,
      ride_pickup_buffer_enabled: false,
      ride_strict_lateness_enabled: false,
      ride_airport_features_enabled: false,
      ride_no_show_handling_enabled: false,
      ev_battery_awareness_enabled: false,
      ev_low_battery_alerts_enabled: false,
      ev_auto_charging_enabled: false,
      multi_depot_vehicles_enabled: false,
      multi_trip_enabled: true,
      traffic_aware_routing_enabled: true,
    },
    ride_settings: {
      pickup_buffer_minutes: 5,
      service_time_pickup_seconds: 60,
      passenger_no_show_wait_minutes: 10,
      lateness_grace_seconds: 0,
      default_passengers_per_vehicle: 4,
    },
    ev_settings: {
      battery_low_threshold_pct: 20,
      default_charge_target_pct: 80,
      allowed_charger_networks: [],
      min_charger_kw: 50,
      max_charger_search_radius_km: 5,
      charge_target_pct_for_planning: 80,
      estimated_kw_used_for_planning: 50,
      charging_setup_overhead_minutes: 15,
    },
    ...overrides,
  };
}

function makeOrder(overrides: Partial<Order> = {}): Order {
  return {
    id: "order-1",
    status: "PENDING",
    type: "ON_DEMAND",
    priority: "NORMAL",
    pickup_address: "123 Main St",
    delivery_address: "456 Oak Ave",
    customer_email: "customer@example.com",
    earliest_delivery: "2026-03-13T09:00:00Z",
    latest_delivery: "2026-03-13T17:00:00Z",
    attempt_count: 0,
    max_attempts: 3,
    created_at: "2026-03-13T08:00:00Z",
    ...overrides,
  };
}

function makeScenarioSummary(overrides: Partial<RouteScenarioSummary> = {}): RouteScenarioSummary {
  return {
    route_count: 2,
    assigned_count: 10,
    unassigned_count: 0,
    vehicles_used: 2,
    distance_km: 100,
    duration_minutes: 300,
    on_time_rate: 1,
    late_minutes: 0,
    realized_energy_cost_try: 1_000,
    fuel_liters: 20,
    electric_kwh: 0,
    co2_kg: 10,
    solver_status: "FEASIBLE",
    objective_breakdown: null,
    energy_price_sources: [],
    energy_price_freshness_status: "FRESH",
    warnings: [],
    ...overrides,
  };
}

function makeRouteComparison(): RouteScenarioComparison {
  const baseScenario = {
    comparison_id: "comparison-1",
    solver_kind: "ortools_cvrptw",
    status: "SUCCEEDED",
    optimization_run_id: null,
    error_message: null,
    created_at: "2026-05-04T08:00:00Z",
    completed_at: "2026-05-04T08:02:00Z",
  };
  return {
    id: "comparison-1",
    target_date: "2026-05-05",
    status: "READY",
    task_id: "task-1",
    selected_scenario_id: null,
    current_policy_snapshot: null,
    error_message: null,
    created_at: "2026-05-04T08:00:00Z",
    completed_at: "2026-05-04T08:12:00Z",
    applied_at: null,
    scenarios: [
      {
        ...baseScenario,
        id: "scenario-baseline",
        key: "human_dispatcher_baseline",
        label: "Human dispatcher baseline",
        routing_objective: "SERVICE_FIRST" as const,
        fleet_utilization: "FULL_FLEET" as const,
        summary: makeScenarioSummary(),
      },
      {
        ...baseScenario,
        id: "scenario-fuel-full",
        key: "fuel_aware_full_fleet",
        label: "Fuel-aware + Full fleet",
        routing_objective: "FUEL_AWARE" as const,
        fleet_utilization: "FULL_FLEET" as const,
        summary: makeScenarioSummary({ realized_energy_cost_try: 900, duration_minutes: 280 }),
      },
      {
        ...baseScenario,
        id: "scenario-fuel-efficient",
        key: "fuel_aware_efficient",
        label: "Fuel-aware + Efficient fleet",
        routing_objective: "FUEL_AWARE" as const,
        fleet_utilization: "EFFICIENT" as const,
        summary: makeScenarioSummary({ realized_energy_cost_try: 850, duration_minutes: 330 }),
      },
      {
        ...baseScenario,
        id: "scenario-service-full",
        key: "service_first_full_fleet",
        label: "Service-first + Full fleet",
        routing_objective: "SERVICE_FIRST" as const,
        fleet_utilization: "FULL_FLEET" as const,
        summary: makeScenarioSummary({ realized_energy_cost_try: 1_100, duration_minutes: 250 }),
      },
      {
        ...baseScenario,
        id: "scenario-service-efficient",
        key: "service_first_efficient",
        label: "Service-first + Efficient fleet",
        routing_objective: "SERVICE_FIRST" as const,
        fleet_utilization: "EFFICIENT" as const,
        summary: makeScenarioSummary({ realized_energy_cost_try: 1_050, duration_minutes: 260 }),
      },
    ],
  };
}

function makeRoutePreview(): OptimizationRunPreview {
  return {
    optimization_run_id: "run-fuel-full",
    run_status: "PENDING_APPROVAL",
    solver_status: "FEASIBLE",
    routes: [
      {
        route_id: "draft-route-1",
        courier_id: "courier-1",
        vehicle_type: "CAR_VAN",
        planned_distance_km: 18.5,
        stop_count: 2,
        order_count: 1,
        total_weight_kg: 3.2,
        eta: {
          earliest_departure: "2026-05-05T09:00:00Z",
          latest_arrival: "2026-05-05T10:00:00Z",
          total_duration_minutes: 60,
        },
        violations: [],
        stops: [
          {
            stop_id: "stop-1",
            sequence_number: 1,
            stop_type: "PICKUP",
            lat: 41,
            lon: 29,
            address: "Warehouse",
            planned_arrival: "2026-05-05T09:00:00Z",
            order: null,
          },
          {
            stop_id: "stop-2",
            sequence_number: 2,
            stop_type: "DELIVERY",
            lat: 41.01,
            lon: 29.01,
            address: "Customer",
            planned_arrival: "2026-05-05T10:00:00Z",
            order: {
              order_id: "order-1",
              pickup_address: "Warehouse",
              delivery_address: "Customer",
              weight_kg: 3.2,
              is_fragile: false,
              is_temp_sensitive: false,
              priority: "NORMAL",
              earliest_delivery: "2026-05-05T09:00:00Z",
              latest_delivery: "2026-05-05T12:00:00Z",
              on_time: true,
            },
          },
        ],
        explainability: null,
        cost_breakdown: null,
        cost_delta_vs_current: null,
      },
    ],
    unassigned_order_ids: [],
    summary: {
      total_routes: 1,
      total_orders: 1,
      total_unassigned: 0,
      total_distance_km: 18.5,
      total_violations: 0,
      violation_breakdown: {},
      on_time_rate: 1,
      cost_breakdown: null,
      cost_delta_vs_current: null,
    },
    explainability: null,
  } as unknown as OptimizationRunPreview;
}

describe("OrdersPage", () => {
  async function renderOrders() {
    await act(async () => {
      renderWithProviders(<OrdersPage />);
    });
  }

  beforeEach(() => {
    vi.clearAllMocks();
    // Default: all usePolling calls return empty state
    mockUsePolling.mockReturnValue(noData);
    mockedGetOperationsPolicy.mockImplementation(
      () => new Promise<OperationsPolicy>(() => undefined),
    );
    mockUseRouteGeometries.mockReturnValue(new Map());
  });

  it("renders the page header and status tabs", async () => {
    await renderOrders();
    expect(screen.getByText("Orders")).toBeInTheDocument();
    expect(screen.getByText("All")).toBeInTheDocument();
    expect(screen.getByText("Pending")).toBeInTheDocument();
    expect(screen.getByText("Delivered")).toBeInTheDocument();
  });

  it("shows route comparison beside optimal assignment", async () => {
    await renderOrders();
    expect(screen.getByRole("button", { name: /assign optimally/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /compare routes/i })).toBeInTheDocument();
  });

  it("renders the human baseline first in route comparisons", async () => {
    vi.mocked(createRouteScenarioComparison).mockResolvedValueOnce({
      comparison: makeRouteComparison(),
      task_id: "task-1",
      message: "Route comparison started",
    });

    await renderOrders();
    await act(async () => {
      fireEvent.click(screen.getByTestId("compare-routes-btn"));
    });

    const baseline = (await screen.findAllByText("Human dispatcher baseline"))[0];
    const firstModel = screen.getByText("Fuel-aware + Full fleet");
    expect(
      baseline.compareDocumentPosition(firstModel) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.getByText("Service-first + Efficient fleet")).toBeInTheDocument();
    expect(screen.getAllByText(/vs baseline/i)).toHaveLength(4);
  });

  it("fetches road geometry for route comparison preview maps", async () => {
    const comparison = makeRouteComparison();
    comparison.scenarios[1].optimization_run_id = "run-fuel-full";
    vi.mocked(createRouteScenarioComparison).mockResolvedValueOnce({
      comparison,
      task_id: "task-1",
      message: "Route comparison started",
    });
    vi.mocked(getRouteScenarioPreview).mockResolvedValueOnce(makeRoutePreview());

    await renderOrders();
    await act(async () => {
      fireEvent.click(screen.getByTestId("compare-routes-btn"));
    });
    await act(async () => {
      fireEvent.click(await screen.findByText("Fuel-aware + Full fleet"));
    });

    await waitFor(() => {
      expect(mockUseRouteGeometries).toHaveBeenCalledWith(
        expect.arrayContaining([
          expect.objectContaining({ id: "draft-route-1" }),
        ]),
        "test-token",
      );
    });
  });

  it("renders orders returned by the polling hook", async () => {
    const orders = [
      makeOrder({ id: "order-1", delivery_address: "456 Oak Ave" }),
      makeOrder({ id: "order-2", delivery_address: "789 Pine Rd", status: "DELIVERED" }),
    ];

    mockUsePolling
      // first call: listOrdersPaginated — main orders list
      .mockReturnValueOnce({
        data: { data: orders, total: 2 },
        isLoading: false,
        error: null,
        refetch: vi.fn(),
      })
      // subsequent calls: failedCount, couriers, depots
      .mockReturnValue(noData);

    await renderOrders();

    expect(screen.getByText(/456 Oak Ave/)).toBeInTheDocument();
    expect(screen.getByText(/789 Pine Rd/)).toBeInTheDocument();
  });

  it("shows empty state when order list is empty", async () => {
    mockUsePolling
      .mockReturnValueOnce({
        data: { data: [], total: 0 },
        isLoading: false,
        error: null,
        refetch: vi.fn(),
      })
      .mockReturnValue(noData);

    await renderOrders();

    expect(screen.getByText(/no orders/i)).toBeInTheDocument();
  });

  it("shows New Order button", async () => {
    await renderOrders();
    expect(screen.getByRole("button", { name: /new order/i })).toBeInTheDocument();
  });

  it("renders policy-driven custom fields in the create modal", async () => {
    mockedGetOperationsPolicy.mockResolvedValueOnce(
      makePolicy({
        order_form: {
          standard_fields: {
            customer_phone: "REQUIRED",
            notes: "OPTIONAL",
            external_ref: "REQUIRED",
            depot_id: "OPTIONAL",
            is_fragile: "OPTIONAL",
            is_temp_sensitive: "OPTIONAL",
          },
          custom_fields: [
            {
              key: "dock_number",
              label: "Dock Number",
              type: "TEXT",
              required: true,
              options: [],
              applies_to_order_types: ["BULK"],
            },
          ],
        },
      }),
    );

    await renderOrders();
    await waitFor(() => expect(mockedGetOperationsPolicy).toHaveBeenCalled());
    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /new order/i }));
    });

    await act(async () => {
      fireEvent.change(screen.getByLabelText("Type"), { target: { value: "BULK" } });
    });

    expect(await screen.findByLabelText("Reference Number *")).toBeInTheDocument();
    expect(await screen.findByLabelText("Dock Number *")).toBeInTheDocument();
    expect(await screen.findByLabelText("Customer Phone *")).toBeInTheDocument();
  });

  it("does not show a settings permission toast when dispatchers cannot read operations policy", async () => {
    mockedGetOperationsPolicy.mockRejectedValueOnce(
      Object.assign(new Error("Insufficient permissions. Required: ['ADMIN', 'OWNER']"), { status: 403 }),
    );

    await renderOrders();

    await waitFor(() => expect(mockedGetOperationsPolicy).toHaveBeenCalled());
    expect(screen.queryByText(/Insufficient permissions/i)).not.toBeInTheDocument();
  });

  // ── Feature: Pickup & Delivery order type ──────────────────────────────────

  it("shows Pickup & Delivery option in the type dropdown", async () => {
    await renderOrders();

    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /new order/i })); });

    const select = screen.getByLabelText("Type") as HTMLSelectElement;
    const options = Array.from(select.options).map((o) => o.value);
    expect(options).toContain("PICKUP_AND_DELIVERY");
  });

  it("shows pickup window inputs only when PICKUP_AND_DELIVERY is selected", async () => {
    await renderOrders();

    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /new order/i })); });

    // Not visible initially (ON_DEMAND default)
    expect(screen.queryByLabelText(/pickup opens/i)).not.toBeInTheDocument();

    await act(async () => {
      fireEvent.change(screen.getByLabelText("Type"), { target: { value: "PICKUP_AND_DELIVERY" } });
    });

    expect(screen.getByLabelText(/pickup opens at/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/pickup closes at/i)).toBeInTheDocument();
    expect(screen.getByText(/when can the courier collect/i)).toBeInTheDocument();
  });

  it("hides pickup window inputs when switching away from PICKUP_AND_DELIVERY", async () => {
    await renderOrders();

    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /new order/i })); });

    await act(async () => {
      fireEvent.change(screen.getByLabelText("Type"), { target: { value: "PICKUP_AND_DELIVERY" } });
    });
    expect(screen.getByLabelText(/pickup opens at/i)).toBeInTheDocument();

    await act(async () => {
      fireEvent.change(screen.getByLabelText("Type"), { target: { value: "ON_DEMAND" } });
    });
    expect(screen.queryByLabelText(/pickup opens at/i)).not.toBeInTheDocument();
  });

  // ── Feature: Request Return ────────────────────────────────────────────────

  function setupOrdersListWithDelivered(overrides: Partial<Order> = {}) {
    const order = makeOrder({ id: "order-delivered", status: "DELIVERED", ...overrides });
    mockUsePolling
      .mockReturnValueOnce({
        data: { data: [order], total: 1 },
        isLoading: false,
        error: null,
        refetch: vi.fn(),
      })
      .mockReturnValue(noData);
    vi.mocked(getOrder).mockResolvedValue(order);
    vi.mocked(getOrderLegs).mockResolvedValue([]);
    return order;
  }

  it("shows Request return button for DELIVERED orders without return_of_order_id", async () => {
    setupOrdersListWithDelivered();

    await renderOrders();

    await act(async () => { fireEvent.click(screen.getByTestId("order-row")); });
    await waitFor(() => expect(screen.getByRole("button", { name: /^request return$/i })).toBeInTheDocument());
    expect(screen.getByText(/schedule a courier/i)).toBeInTheDocument();
  });

  it("does not show Request return button for orders that are already returns", async () => {
    setupOrdersListWithDelivered({ return_of_order_id: "parent-order-id" });

    await renderOrders();

    await act(async () => { fireEvent.click(screen.getByTestId("order-row")); });
    // Wait for slide-over to load (shows "Return for" label for the linked parent order)
    await waitFor(() => expect(screen.getByText("Return for")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /^request return$/i })).not.toBeInTheDocument();
  });

  it("expands the return form when Request return button is clicked", async () => {
    setupOrdersListWithDelivered();

    await renderOrders();

    await act(async () => { fireEvent.click(screen.getByTestId("order-row")); });
    await waitFor(() => expect(screen.getByRole("button", { name: /^request return$/i })).toBeInTheDocument());

    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /^request return$/i })); });

    expect(screen.getByRole("button", { name: /submit return request/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/reason/i)).toBeInTheDocument();
  });

  it("calls createOrderReturn with the right payload on submit", async () => {
    const mockedCreateReturn = vi.mocked(createOrderReturn);
    mockedCreateReturn.mockResolvedValue(makeOrder({ id: "return-order-id", type: "RETURN" }));
    setupOrdersListWithDelivered();

    await renderOrders();

    await act(async () => { fireEvent.click(screen.getByTestId("order-row")); });
    await waitFor(() => expect(screen.getByRole("button", { name: /^request return$/i })).toBeInTheDocument());
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /^request return$/i })); });

    const inputs = screen.getAllByDisplayValue("");
    const dateInputs = inputs.filter((el) => (el as HTMLInputElement).type === "datetime-local");
    fireEvent.change(dateInputs[0], { target: { value: "2026-04-28T09:00" } });
    fireEvent.change(dateInputs[1], { target: { value: "2026-04-28T12:00" } });

    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /submit return request/i })); });

    await waitFor(() => expect(mockedCreateReturn).toHaveBeenCalledWith(
      "test-token",
      "order-delivered",
      expect.objectContaining({ earliest_pickup: expect.any(String), latest_pickup: expect.any(String) }),
    ));
  });

  // ── Feature: Return for link ────────────────────────────────────────────────

  it("shows ↩ indicator on return order rows in the table", async () => {
    const order = makeOrder({ id: "ret-1", return_of_order_id: "parent-id" });
    mockUsePolling
      .mockReturnValueOnce({
        data: { data: [order], total: 1 },
        isLoading: false,
        error: null,
        refetch: vi.fn(),
      })
      .mockReturnValue(noData);

    await renderOrders();
    expect(screen.getByTitle("Return order")).toBeInTheDocument();
  });

  it("shows 'Return for' link in the slide-over for orders with return_of_order_id", async () => {
    const order = makeOrder({ id: "ret-2", return_of_order_id: "parent-abc123" });
    mockUsePolling
      .mockReturnValueOnce({ data: { data: [order], total: 1 }, isLoading: false, error: null, refetch: vi.fn() })
      .mockReturnValue(noData);
    vi.mocked(getOrder).mockResolvedValue(order);
    vi.mocked(getOrderLegs).mockResolvedValue([]);

    await renderOrders();

    await act(async () => { fireEvent.click(screen.getByTestId("order-row")); });
    await waitFor(() => expect(screen.getByText("Return for")).toBeInTheDocument());
    // The link shows the first 8 chars of the parent ID: "parent-a"
    expect(screen.getByText(/^#parent-a$/i)).toBeInTheDocument();
  });

  // ── Feature: Journey timeline ─────────────────────────────────────────────

  it("hides Journey section when order has no legs", async () => {
    const order = makeOrder({ id: "order-no-legs" });
    mockUsePolling
      .mockReturnValueOnce({ data: { data: [order], total: 1 }, isLoading: false, error: null, refetch: vi.fn() })
      .mockReturnValue(noData);
    vi.mocked(getOrder).mockResolvedValue(order);
    vi.mocked(getOrderLegs).mockResolvedValue([]);

    await renderOrders();
    await act(async () => { fireEvent.click(screen.getByTestId("order-row")); });
    // Wait for the slide-over to be fully populated (the "Pickup" FieldRow label is unique to the detail panel)
    await waitFor(() => expect(screen.getByText("Pickup")).toBeInTheDocument());
    // Journey section heading must not appear when there are no legs
    expect(screen.queryAllByText("Journey")).toHaveLength(0);
  });

  it("renders Journey section with one row per leg when order has multi-depot legs", async () => {
    const order = makeOrder({ id: "order-legs" });
    const legs: OrderLeg[] = [
      {
        id: "leg-1",
        order_id: "order-legs",
        leg_kind: "FIRST_MILE",
        sequence_number: 1,
        from_depot_id: null,
        to_depot_id: "depot-hub",
        status: "COMPLETED",
        planned_pickup_at: "2026-04-28T09:00:00Z",
        planned_delivery_at: "2026-04-28T10:00:00Z",
        created_at: "2026-04-28T08:00:00Z",
        updated_at: "2026-04-28T10:00:00Z",
      },
      {
        id: "leg-2",
        order_id: "order-legs",
        leg_kind: "LAST_MILE",
        sequence_number: 2,
        from_depot_id: "depot-hub",
        to_depot_id: null,
        status: "IN_TRANSIT",
        planned_pickup_at: "2026-04-28T10:30:00Z",
        planned_delivery_at: "2026-04-28T12:00:00Z",
        created_at: "2026-04-28T08:00:00Z",
        updated_at: "2026-04-28T11:00:00Z",
      },
    ];
    mockUsePolling
      .mockReturnValueOnce({ data: { data: [order], total: 1 }, isLoading: false, error: null, refetch: vi.fn() })
      .mockReturnValue(noData);
    vi.mocked(getOrder).mockResolvedValue(order);
    vi.mocked(getOrderLegs).mockResolvedValue(legs);

    await renderOrders();
    await act(async () => { fireEvent.click(screen.getByTestId("order-row")); });
    // Wait for Journey section to appear
    await waitFor(() => expect(screen.getByText("Journey")).toBeInTheDocument());
    // Both leg kind labels must appear in the journey rows
    expect(screen.getByText(/First mile/i)).toBeInTheDocument();
    // "Last mile" appears in the journey timeline row (not ambiguous with email addresses)
    expect(screen.getAllByText(/Last mile/i).length).toBeGreaterThan(0);
  });

  // ── Feature: Optimize Range ─────────────────────────────────────────────────

  it("queries the selected operational day by service_date", async () => {
    await renderOrders();
    const fetchOrders = mockUsePolling.mock.calls[0][0] as () => Promise<unknown>;
    await fetchOrders();

    expect(listOrdersPaginated).toHaveBeenCalledWith(
      "test-token",
      expect.stringContaining("service_date="),
    );
    expect(listOrdersPaginated).not.toHaveBeenCalledWith(
      "test-token",
      expect.stringContaining("delivery_from="),
    );
  });

  it("opens the Optimize Range modal with date inputs", async () => {
    mockUsePolling
      .mockReturnValueOnce({ data: { data: [], total: 0 }, isLoading: false, error: null, refetch: vi.fn() })
      .mockReturnValue(noData);
    await renderOrders();
    fireEvent.click(screen.getByTestId("optimize-range-btn"));
    await waitFor(() => {
      expect(screen.getByText("Optimize a date range")).toBeInTheDocument();
    });
    expect(screen.getByTestId("optimize-range-from")).toBeInTheDocument();
    expect(screen.getByTestId("optimize-range-to")).toBeInTheDocument();
  });

  it("starts range optimization in the background and polls completion", async () => {
    mockUsePolling
      .mockReturnValueOnce({ data: { data: [], total: 0 }, isLoading: false, error: null, refetch: vi.fn() })
      .mockReturnValue(noData);
    vi.mocked(triggerAssignRange).mockResolvedValue({
      task_id: "task-range-1",
      message: "ok",
      span_days: 5,
    });
    vi.mocked(getOptimizationTaskStatus).mockResolvedValue({
      task_id: "task-range-1",
      status: "SUCCESS",
      result: {
        routes_created: 12,
        assigned_count: 87,
        unassigned_count: 3,
        span_days: 5,
        solver_status: "FEASIBLE",
      },
    });
    vi.useFakeTimers();
    try {
      await renderOrders();
      fireEvent.click(screen.getByTestId("optimize-range-btn"));
      fireEvent.change(screen.getByTestId("optimize-range-from"), {
        target: { value: "2026-04-28" },
      });
      fireEvent.change(screen.getByTestId("optimize-range-to"), {
        target: { value: "2026-05-02" },
      });
      await act(async () => {
        fireEvent.click(screen.getByTestId("optimize-range-run"));
      });
      expect(screen.queryByText("Optimize a date range")).not.toBeInTheDocument();
      expect(screen.getByText("Range optimization started for 5 days")).toBeInTheDocument();
      // Advance the polling interval (3s).
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3000);
      });
    } finally {
      vi.useRealTimers();
    }

    await waitFor(() => {
      expect(triggerAssignRange).toHaveBeenCalledWith(
        "test-token",
        "2026-04-28",
        "2026-05-02",
        { draft: false },
      );
    });
    await waitFor(() => {
      expect(screen.getByText(/Range plan ready: 87 assigned across 5 days/i)).toBeInTheDocument();
    });
  });

  it("blocks ranges longer than 180 days with an inline error", async () => {
    mockUsePolling
      .mockReturnValueOnce({ data: { data: [], total: 0 }, isLoading: false, error: null, refetch: vi.fn() })
      .mockReturnValue(noData);
    await renderOrders();
    fireEvent.click(screen.getByTestId("optimize-range-btn"));
    // A 365-day range exceeds the new 180-day safety net.
    fireEvent.change(screen.getByTestId("optimize-range-from"), {
      target: { value: "2026-01-01" },
    });
    fireEvent.change(screen.getByTestId("optimize-range-to"), {
      target: { value: "2026-12-31" },
    });
    fireEvent.click(screen.getByTestId("optimize-range-run"));
    await waitFor(() => {
      expect(screen.getByText(/180 days/i)).toBeInTheDocument();
    });
    expect(triggerAssignRange).not.toHaveBeenCalled();
  });

  it("allows a 30-day range with no inline error (14-day cap is gone)", async () => {
    mockUsePolling
      .mockReturnValueOnce({ data: { data: [], total: 0 }, isLoading: false, error: null, refetch: vi.fn() })
      .mockReturnValue(noData);
    vi.mocked(triggerAssignRange).mockResolvedValue({
      task_id: "task-range-30",
      message: "ok",
      span_days: 30,
    });
    await renderOrders();
    fireEvent.click(screen.getByTestId("optimize-range-btn"));
    fireEvent.change(screen.getByTestId("optimize-range-from"), {
      target: { value: "2026-04-01" },
    });
    fireEvent.change(screen.getByTestId("optimize-range-to"), {
      target: { value: "2026-04-30" },
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId("optimize-range-run"));
    });
    // No inline error.
    expect(screen.queryByText(/180 days/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/capped at 14/i)).not.toBeInTheDocument();
    expect(triggerAssignRange).toHaveBeenCalledWith(
      "test-token",
      "2026-04-01",
      "2026-04-30",
      { draft: false },
    );
  });

  it("preview button calls the draft variant", async () => {
    mockUsePolling
      .mockReturnValueOnce({ data: { data: [], total: 0 }, isLoading: false, error: null, refetch: vi.fn() })
      .mockReturnValue(noData);
    vi.mocked(triggerAssignRange).mockResolvedValue({
      task_id: "task-preview",
      message: "ok",
      span_days: 5,
    });
    await renderOrders();
    fireEvent.click(screen.getByTestId("optimize-range-btn"));
    fireEvent.change(screen.getByTestId("optimize-range-from"), {
      target: { value: "2026-04-28" },
    });
    fireEvent.change(screen.getByTestId("optimize-range-to"), {
      target: { value: "2026-05-02" },
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId("optimize-range-preview"));
    });
    expect(triggerAssignRange).toHaveBeenCalledWith(
      "test-token",
      "2026-04-28",
      "2026-05-02",
      { draft: true },
    );
  });
});
