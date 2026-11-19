import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "../../test/renderWithProviders";
import { RecurringOrdersPage } from "../RecurringOrdersPage";
import {
  createRecurringTemplate,
  deleteRecurringTemplate,
  listRecurringTemplates,
  previewRecurringTemplate,
  runRecurringTemplate,
  updateRecurringTemplate,
} from "../../api/client";
import type { RecurringOrderTemplate } from "../../types";

vi.mock("../../state/AuthContext", () => ({
  useAuth: () => ({ token: "test-token", role: "DISPATCHER" }),
}));

vi.mock("../../api/client", () => ({
  listRecurringTemplates: vi.fn(),
  getRecurringTemplate: vi.fn(),
  createRecurringTemplate: vi.fn(),
  updateRecurringTemplate: vi.fn(),
  deleteRecurringTemplate: vi.fn(),
  previewRecurringTemplate: vi.fn(),
  runRecurringTemplate: vi.fn(),
  runAllRecurringTemplates: vi.fn(),
  listDepots: vi.fn().mockResolvedValue([]),
  listVehicleTags: vi.fn().mockResolvedValue([]),
  geocodeSearch: vi.fn().mockResolvedValue([]),
}));

const mockedList = vi.mocked(listRecurringTemplates);
const mockedCreate = vi.mocked(createRecurringTemplate);
const mockedUpdate = vi.mocked(updateRecurringTemplate);
const mockedDelete = vi.mocked(deleteRecurringTemplate);
const mockedPreview = vi.mocked(previewRecurringTemplate);
const mockedRun = vi.mocked(runRecurringTemplate);

function makeTemplate(overrides: Partial<RecurringOrderTemplate> = {}): RecurringOrderTemplate {
  return {
    id: "tpl-1",
    tenant_id: "tenant-1",
    name: "Acme weekly drop",
    description: null,
    is_active: true,
    cadence: "WEEKLY",
    interval_days: 1,
    day_of_week_mask: 0b0010101,
    day_of_month: null,
    start_date: "2026-04-28",
    end_date: null,
    max_occurrences: null,
    occurrences_generated: 5,
    delivery_window_start: "09:00:00",
    delivery_window_end: "11:00:00",
    pickup_window_start: null,
    pickup_window_end: null,
    lead_time_days: 14,
    order_type: "SCHEDULED",
    priority: "NORMAL",
    pickup_address: "Warehouse, Istanbul",
    pickup_lat: 41.0431,
    pickup_lon: 28.9026,
    delivery_address: "Acme HQ",
    delivery_lat: 41.0426,
    delivery_lon: 29.0072,
    depot_id: null,
    weight_kg: 4,
    is_fragile: false,
    is_temp_sensitive: false,
    customer_email: "ops@acme.example",
    customer_phone: null,
    external_ref_prefix: "ACME-WK",
    custom_fields: null,
    notes: null,
    last_generated_for: null,
    next_generation_at: null,
    last_run_at: null,
    created_at: "2026-04-28T00:00:00",
    updated_at: "2026-04-28T00:00:00",
    ...overrides,
  };
}

describe("RecurringOrdersPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the empty state when there are no templates", async () => {
    mockedList.mockResolvedValue([]);
    renderWithProviders(<RecurringOrdersPage />);
    await waitFor(() => {
      expect(screen.getByText(/no recurring templates yet/i)).toBeInTheDocument();
    });
    expect(screen.getByTestId("recurring-new-btn")).toBeInTheDocument();
  });

  it("lists existing templates with their cadence summary", async () => {
    mockedList.mockResolvedValue([
      makeTemplate({ name: "Acme weekly drop" }),
      makeTemplate({
        id: "tpl-2",
        name: "Daily restock",
        cadence: "DAILY",
        day_of_week_mask: 0,
        is_active: false,
      }),
    ]);
    renderWithProviders(<RecurringOrdersPage />);
    await waitFor(() => {
      expect(screen.getByText("Acme weekly drop")).toBeInTheDocument();
    });
    expect(screen.getByText("Daily restock")).toBeInTheDocument();
    // Cadence column shows the human-readable summary.
    expect(screen.getByText(/Weekly · /)).toBeInTheDocument();
    expect(screen.getByText("Every day")).toBeInTheDocument();
    // One template is paused → there should be a "Paused" pill.
    expect(screen.getByText("Paused")).toBeInTheDocument();
  });

  it("opens the create modal with the cadence selector exposed", async () => {
    mockedList.mockResolvedValue([]);
    renderWithProviders(<RecurringOrdersPage />);
    await waitFor(() => screen.getByTestId("recurring-new-btn"));

    fireEvent.click(screen.getByTestId("recurring-new-btn"));
    expect(screen.getByText("New recurring template")).toBeInTheDocument();
    expect(screen.getByTestId("recurring-cadence-select")).toBeInTheDocument();
    // Switching to MONTHLY should reveal the day-of-month input.
    fireEvent.change(screen.getByTestId("recurring-cadence-select"), {
      target: { value: "MONTHLY" },
    });
    expect(screen.getByText(/Day of month/i)).toBeInTheDocument();
  });

  it("blocks save with a toast when required fields are missing", async () => {
    mockedList.mockResolvedValue([]);
    renderWithProviders(<RecurringOrdersPage />);
    await waitFor(() => screen.getByTestId("recurring-new-btn"));

    fireEvent.click(screen.getByTestId("recurring-new-btn"));
    fireEvent.click(screen.getByTestId("recurring-save-btn"));
    // Validation: name is the first guard, before any API call.
    expect(mockedCreate).not.toHaveBeenCalled();
  });

  it("toggles pause / resume by calling updateRecurringTemplate", async () => {
    mockedList.mockResolvedValue([makeTemplate({ is_active: true })]);
    mockedUpdate.mockResolvedValue(makeTemplate({ is_active: false }));
    renderWithProviders(<RecurringOrdersPage />);
    await waitFor(() => screen.getByText("Acme weekly drop"));

    const pauseButton = screen.getByTitle("Pause");
    fireEvent.click(pauseButton);
    await waitFor(() => {
      expect(mockedUpdate).toHaveBeenCalledWith("test-token", "tpl-1", {
        is_active: false,
      });
    });
  });

  it("runs a single template on demand", async () => {
    mockedList.mockResolvedValue([makeTemplate()]);
    mockedRun.mockResolvedValue({
      templates_evaluated: 1,
      occurrences_created: 3,
      occurrences_skipped_duplicate: 0,
      occurrences_skipped_capped: 0,
    });
    renderWithProviders(<RecurringOrdersPage />);
    await waitFor(() => screen.getByText("Acme weekly drop"));

    const runButton = screen.getByTitle("Run now");
    fireEvent.click(runButton);
    await waitFor(() => {
      expect(mockedRun).toHaveBeenCalledWith("test-token", "tpl-1");
    });
  });

  it("opens the preview modal and shows upcoming dates", async () => {
    mockedList.mockResolvedValue([makeTemplate()]);
    mockedPreview.mockResolvedValue({
      template_id: "tpl-1",
      occurrence_dates: ["2026-04-29", "2026-05-01", "2026-05-04"],
    });
    renderWithProviders(<RecurringOrdersPage />);
    await waitFor(() => screen.getByText("Acme weekly drop"));

    fireEvent.click(screen.getByText("Acme weekly drop"));
    await waitFor(() => {
      expect(mockedPreview).toHaveBeenCalledWith("test-token", "tpl-1", 60);
    });
    await waitFor(() => {
      expect(screen.getByText("2026-04-29")).toBeInTheDocument();
      expect(screen.getByText("2026-05-01")).toBeInTheDocument();
      expect(screen.getByText("2026-05-04")).toBeInTheDocument();
    });
  });

  it("deletes a template after confirmation", async () => {
    mockedList.mockResolvedValue([makeTemplate()]);
    mockedDelete.mockResolvedValue(undefined);
    renderWithProviders(<RecurringOrdersPage />);
    await waitFor(() => screen.getByText("Acme weekly drop"));

    fireEvent.click(screen.getByTitle("Delete"));
    // ConfirmDialog renders inline; wait for its "Delete" button to appear.
    await waitFor(() =>
      expect(screen.getByText(/will stop generating orders/i)).toBeInTheDocument(),
    );
    // Two "Delete" elements now exist (row icon + dialog button); take the dialog one.
    const dialogButtons = screen
      .getAllByText("Delete")
      .filter((el) => el.tagName === "BUTTON");
    expect(dialogButtons.length).toBeGreaterThan(0);
    fireEvent.click(dialogButtons[dialogButtons.length - 1]);
    await waitFor(() => {
      expect(mockedDelete).toHaveBeenCalledWith("test-token", "tpl-1");
    });
  });
});
