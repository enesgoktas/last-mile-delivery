import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { OperationsPolicyTab } from "../OperationsPolicyTab";
import {
  getOperationsPolicy,
  listOperationsPolicyPresets,
  saveOperationsPolicy,
  applyOperationsPolicyPreset,
} from "../../../api/client";
import { ToastProvider } from "../../../state/ToastContext";
import { ToastContainer } from "../../../components/Toast";
import type { OperationsPolicy, PolicyPreset } from "../../../types";

vi.mock("../../../state/AuthContext", () => ({
  useAuth: () => ({ token: "tok" }),
}));

vi.mock("../../../api/client", () => ({
  getOperationsPolicy: vi.fn(),
  listOperationsPolicyPresets: vi.fn(),
  saveOperationsPolicy: vi.fn(),
  applyOperationsPolicyPreset: vi.fn(),
}));
const mockGetPolicy = vi.mocked(getOperationsPolicy);
const mockListPresets = vi.mocked(listOperationsPolicyPresets);
const mockSave = vi.mocked(saveOperationsPolicy);
const mockApplyPreset = vi.mocked(applyOperationsPolicyPreset);

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
      require_arrival_scan: false,
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
        receiver_name_mode: "OFF",
        notes_mode: "OPTIONAL",
      },
      by_order_type: {},
    },
    failure_handling: {
      allowed_standard_reasons: ["ACCESS_DENIED", "NOT_HOME"],
      custom_reasons: [],
      require_notes_for_other: false,
      post_failure_action: "DISPATCH_REVIEW",
      max_attempts: 3,
      retry_timing: "NEXT_DAY",
      escalation_hours: 24,
      self_service_mode: "RESCHEDULE_ONLY",
    },
    notifications: {
      language: "en",
      branding_name: "Acme",
      quiet_hours_enabled: false,
      quiet_hours_start: "22:00",
      quiet_hours_end: "08:00",
      timezone: "UTC",
      eta_visibility: "ETA_RANGE",
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

function renderTab() {
  return render(
    <ToastProvider>
      <OperationsPolicyTab />
      <ToastContainer />
    </ToastProvider>,
  );
}

describe("OperationsPolicyTab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockListPresets.mockResolvedValue([]);
  });

  it("shows loading spinner while fetching policy", async () => {
    mockGetPolicy.mockReturnValue(new Promise(() => {}) as never);
    renderTab();
    expect(screen.getByText(/loading operations settings/i)).toBeInTheDocument();
  });

  it("renders policy sections after load", async () => {
    mockGetPolicy.mockResolvedValue(makePolicy());
    renderTab();
    expect(await screen.findByText("Industry Presets")).toBeInTheDocument();
    expect(screen.getByText("Dispatch")).toBeInTheDocument();
    expect(screen.getByText("Proof of Delivery")).toBeInTheDocument();
  });

  it("calls saveOperationsPolicy and shows success toast on Save", async () => {
    const policy = makePolicy();
    mockGetPolicy.mockResolvedValue(policy);
    mockSave.mockResolvedValue(policy);
    renderTab();

    await screen.findByText("Industry Presets");
    fireEvent.click(screen.getByRole("button", { name: /save operations settings/i }));

    await waitFor(() => expect(mockSave).toHaveBeenCalledWith("tok", expect.any(Object)));
    expect(await screen.findByText("Operations policy saved")).toBeInTheDocument();
  });

  it("persists routing objective as the public optimization knob", async () => {
    const policy = makePolicy();
    mockGetPolicy.mockResolvedValue(policy);
    mockSave.mockResolvedValue({
      ...policy,
      optimization: {
        ...policy.optimization,
        routing_objective: "SERVICE_FIRST",
        preset_key: "SERVICE_FIRST",
        economic_batch_optimizer_enabled: true,
        fuel_priority_multiplier: 0,
        shadow_mode_enabled: false,
      },
    });
    renderTab();

    await screen.findByText("Batch Optimization");
    fireEvent.click(screen.getByRole("button", { name: /service-first/i }));
    fireEvent.click(screen.getByRole("button", { name: /save operations settings/i }));

    await waitFor(() => expect(mockSave).toHaveBeenCalledWith(
      "tok",
      expect.objectContaining({
        optimization: expect.objectContaining({
          routing_objective: "SERVICE_FIRST",
          preset_key: "SERVICE_FIRST",
          economic_batch_optimizer_enabled: true,
          fuel_priority_multiplier: 0,
          shadow_mode_enabled: false,
        }),
      }),
    ));
  });

  it("shows error toast when save fails", async () => {
    mockGetPolicy.mockResolvedValue(makePolicy());
    mockSave.mockRejectedValue(new Error("Conflict"));
    renderTab();

    await screen.findByText("Industry Presets");
    fireEvent.click(screen.getByRole("button", { name: /save operations settings/i }));

    expect(await screen.findByText("Conflict")).toBeInTheDocument();
  });

  it("renders preset cards when presets are available", async () => {
    const preset: PolicyPreset = {
      key: "courier_express",
      label: "Express Delivery",
      description: "Optimized for same-day delivery",
      policy: makePolicy(),
    };
    mockGetPolicy.mockResolvedValue(makePolicy());
    mockListPresets.mockResolvedValue([preset]);
    renderTab();

    expect(await screen.findByText("Express Delivery")).toBeInTheDocument();
    expect(screen.getByText("Optimized for same-day delivery")).toBeInTheDocument();
  });

  it("calls applyOperationsPolicyPreset and shows success toast", async () => {
    const preset: PolicyPreset = {
      key: "express",
      label: "Express",
      description: "Fast",
      policy: makePolicy(),
    };
    mockGetPolicy.mockResolvedValue(makePolicy());
    mockListPresets.mockResolvedValue([preset]);
    mockApplyPreset.mockResolvedValue(makePolicy());
    renderTab();

    await screen.findByText("Express");
    fireEvent.click(screen.getByRole("button", { name: /apply preset/i }));

    await waitFor(() => expect(mockApplyPreset).toHaveBeenCalledWith("tok", "express"));
    expect(await screen.findByText("Preset applied")).toBeInTheDocument();
  });

  it("shows error toast when policy load fails", async () => {
    mockGetPolicy.mockRejectedValue(new Error("Network error"));
    renderTab();

    expect(await screen.findByText("Network error")).toBeInTheDocument();
  });
});
