import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { VehicleSpecsTab } from "../VehicleSpecsTab";
import {
  listVehicleSpecs,
  deleteVehicleSpec,
  createVehicleSpec,
} from "../../../api/client";
import { usePolling } from "../../../hooks/usePolling";
import { ToastProvider } from "../../../state/ToastContext";
import { ToastContainer } from "../../../components/Toast";
import type { VehicleSpec } from "../../../types";

vi.mock("../../../state/AuthContext", () => ({
  useAuth: () => ({ token: "tok" }),
}));

vi.mock("../../../hooks/usePolling");
const mockUsePolling = vi.mocked(usePolling);

vi.mock("../../../api/client", () => ({
  listVehicleSpecs: vi.fn(),
  deleteVehicleSpec: vi.fn(),
  createVehicleSpec: vi.fn(),
  searchEPAYears: vi.fn(),
  searchEPAMakes: vi.fn(),
  searchEPAModels: vi.fn(),
  searchEPA: vi.fn(),
  importEPASpec: vi.fn(),
}));
const mockCreate = vi.mocked(createVehicleSpec);
const mockDelete = vi.mocked(deleteVehicleSpec);

function makeSpec(overrides: Partial<VehicleSpec> = {}): VehicleSpec {
  return {
    id: "spec-1",
    tenant_id: "tenant-1",
    make: "Toyota",
    model: "Corolla",
    year: 2022,
    vehicle_type: "CAR_VAN",
    fuel_type: "GASOLINE",
    fuel_efficiency_km_per_liter: 14.5,
    co2_emissions_g_per_km: 120,
    axle_count: null,
    emission_class: null,
    epa_vehicle_id: null,
    gross_weight_kg: null,
    hazmat_capable: false,
    height_cm: null,
    length_cm: null,
    osrm_profile: "driving",
    width_cm: null,
    source: "MANUAL",
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-01T00:00:00Z",
    ...overrides,
  };
}

function makePollingResult(
  data: VehicleSpec[] | undefined,
  isLoading = false,
  refetch = vi.fn(),
) {
  return { data, isLoading, error: null, refetch };
}

function renderTab() {
  return render(
    <ToastProvider>
      <VehicleSpecsTab />
      <ToastContainer />
    </ToastProvider>,
  );
}

describe("VehicleSpecsTab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Default fleet profiles ──────────────────────────────────────────────

  it("shows delivery-fleet default profiles for all three vehicle classes", () => {
    mockUsePolling.mockReturnValue(makePollingResult([]));
    renderTab();

    // Each vehicle label appears in both the filter dropdown and the profile
    // cards, so we assert at least two occurrences.
    expect(screen.getAllByText("Motorcycle").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText("Car / Van").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText("Truck").length).toBeGreaterThanOrEqual(2);

    // Default efficiency values from VEHICLE_TYPE_FUEL_DEFAULTS — unique to cards
    expect(screen.getByText("36.0 km/L")).toBeInTheDocument();
    expect(screen.getByText("16.0 km/L")).toBeInTheDocument();
    expect(screen.getByText("5.0 km/L")).toBeInTheDocument();
  });

  // ── Spec list ───────────────────────────────────────────────────────────

  it("renders spec rows when specs are loaded", () => {
    const spec = makeSpec();
    mockUsePolling.mockReturnValue(makePollingResult([spec]));
    renderTab();

    expect(screen.getByText(/Toyota Corolla/)).toBeInTheDocument();
    expect(screen.getByText("14.50 km/L")).toBeInTheDocument();
  });

  it("shows empty state with add-manually link when no specs and no filters", () => {
    mockUsePolling.mockReturnValue(makePollingResult([]));
    renderTab();

    expect(screen.getByText(/No vehicle specs yet/i)).toBeInTheDocument();
    // Both the header button and the inline empty-state link use "add manually"
    expect(screen.getAllByRole("button", { name: /add manually/i }).length).toBeGreaterThanOrEqual(1);
  });

  it("shows loading state while fetching", () => {
    mockUsePolling.mockReturnValue(makePollingResult(undefined, true));
    renderTab();

    // "Loading…" appears in both the count area and the table — at least one is present
    expect(screen.getAllByText("Loading…").length).toBeGreaterThanOrEqual(1);
  });

  it("shows spec count when loaded", () => {
    mockUsePolling.mockReturnValue(makePollingResult([makeSpec(), makeSpec({ id: "spec-2" })]));
    renderTab();

    expect(screen.getByText("2 specs")).toBeInTheDocument();
  });

  // ── Primary vs secondary add buttons ───────────────────────────────────

  it("has Add manually as the primary action", () => {
    mockUsePolling.mockReturnValue(makePollingResult([]));
    renderTab();

    // The first "Add manually" button in the DOM is the header primary action
    const addBtns = screen.getAllByRole("button", { name: /add manually/i });
    expect(addBtns[0].className).toMatch(/bg-brand-600/);
  });

  it("has EPA import labeled as Advanced and styled as secondary", () => {
    mockUsePolling.mockReturnValue(makePollingResult([]));
    renderTab();

    const epaBtn = screen.getByRole("button", { name: /advanced epa import/i });
    expect(epaBtn).toBeInTheDocument();
    // Secondary styling — does NOT use the primary brand background
    expect(epaBtn.className).not.toMatch(/bg-brand-600/);
  });

  // ── Manual create modal ─────────────────────────────────────────────────

  it("opens manual create modal when Add manually is clicked", () => {
    mockUsePolling.mockReturnValue(makePollingResult([]));
    renderTab();

    fireEvent.click(screen.getAllByRole("button", { name: /add manually/i })[0]);

    expect(screen.getByText("Add vehicle spec manually")).toBeInTheDocument();
  });

  it("submits createVehicleSpec and shows success toast", async () => {
    const refetch = vi.fn();
    mockUsePolling.mockReturnValue(makePollingResult([], false, refetch));
    mockCreate.mockResolvedValue(makeSpec());
    renderTab();

    fireEvent.click(screen.getAllByRole("button", { name: /add manually/i })[0]);

    // Fill required fields
    fireEvent.change(screen.getByPlaceholderText(/e\.g\. Toyota/i), { target: { value: "Honda" } });
    fireEvent.change(screen.getByPlaceholderText(/e\.g\. Corolla/i), { target: { value: "CB500" } });
    fireEvent.click(screen.getByRole("button", { name: /add spec/i }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith("tok", expect.objectContaining({
      make: "Honda",
      model: "CB500",
    })));
    expect(await screen.findByText("Vehicle spec created")).toBeInTheDocument();
  });

  it("shows error toast when createVehicleSpec fails", async () => {
    mockUsePolling.mockReturnValue(makePollingResult([]));
    mockCreate.mockRejectedValue(new Error("Duplicate spec"));
    renderTab();

    fireEvent.click(screen.getAllByRole("button", { name: /add manually/i })[0]);
    fireEvent.change(screen.getByPlaceholderText(/e\.g\. Toyota/i), { target: { value: "Honda" } });
    fireEvent.change(screen.getByPlaceholderText(/e\.g\. Corolla/i), { target: { value: "CB500" } });
    fireEvent.click(screen.getByRole("button", { name: /add spec/i }));

    expect(await screen.findByText("Duplicate spec")).toBeInTheDocument();
  });

  // ── Delete ──────────────────────────────────────────────────────────────

  it("calls deleteVehicleSpec and refetches on confirm", async () => {
    const refetch = vi.fn();
    const spec = makeSpec({ tenant_id: "tenant-1" });
    mockUsePolling.mockReturnValue(makePollingResult([spec], false, refetch));
    mockDelete.mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderTab();

    // Hover to reveal delete button (opacity removed in test env via group-hover)
    fireEvent.click(screen.getByTitle("Delete spec"));

    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("tok", "spec-1"));
    expect(refetch).toHaveBeenCalled();
    expect(await screen.findByText("Spec deleted")).toBeInTheDocument();
  });

  it("does not delete when confirm is cancelled", () => {
    mockUsePolling.mockReturnValue(makePollingResult([makeSpec()]));
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderTab();

    fireEvent.click(screen.getByTitle("Delete spec"));

    expect(mockDelete).not.toHaveBeenCalled();
  });

  // ── Global (tenant_id=null) specs have no delete button ─────────────────

  it("does not show delete button for global specs", () => {
    const globalSpec = makeSpec({ tenant_id: null });
    mockUsePolling.mockReturnValue(makePollingResult([globalSpec]));
    renderTab();

    expect(screen.queryByTitle("Delete spec")).not.toBeInTheDocument();
  });

  // ── Search filter ───────────────────────────────────────────────────────

  it("filters spec rows by search text", () => {
    const specs = [makeSpec({ make: "Toyota", model: "Corolla" }), makeSpec({ id: "spec-2", make: "Honda", model: "CB500" })];
    mockUsePolling.mockReturnValue(makePollingResult(specs));
    renderTab();

    fireEvent.change(screen.getByPlaceholderText(/search make or model/i), { target: { value: "honda" } });

    expect(screen.queryByText(/Toyota Corolla/)).not.toBeInTheDocument();
    expect(screen.getByText(/Honda CB500/)).toBeInTheDocument();
  });
});
