import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { App } from "../App";
import {
  fetchTracking,
  rescheduleDelivery,
  TrackingApiError,
  type TrackingResponse,
} from "../api";

vi.mock("../TrackingMap", () => ({
  TrackingMap: () => <div data-testid="tracking-map">Map</div>,
}));

vi.mock("../api", () => {
  class MockTrackingApiError extends Error {
    status: number;

    constructor(message: string, status: number) {
      super(message);
      this.name = "TrackingApiError";
      this.status = status;
    }
  }

  return {
    fetchTracking: vi.fn(),
    rescheduleDelivery: vi.fn(),
    TrackingApiError: MockTrackingApiError,
  };
});

const mockedFetchTracking = vi.mocked(fetchTracking);
const mockedRescheduleDelivery = vi.mocked(rescheduleDelivery);

function buildTrackingResponse(
  overrides: Partial<TrackingResponse> = {},
): TrackingResponse {
  return {
    order_id: "order-1",
    status: "IN_TRANSIT",
    status_label: "On the way",
    delivery_address: "123 Main St",
    delivery_lat: 40.98,
    delivery_lon: 29.01,
    route_id: "route-1",
    courier_lat: 40.91,
    courier_lon: 29.03,
    estimated_arrival: "2026-02-24T12:00:00Z",
    last_updated: "2026-02-24T11:30:00Z",
    eta_source: "DYNAMIC",
    total_stops: 8,
    completed_stops: 5,
    remaining_stops: 3,
    next_stop_sequence: 6,
    target_stop_sequence: 8,
    can_reschedule: false,
    can_update_instructions: false,
    show_live_map: true,
    eta_visibility: "LIVE_MAP",
    customer_instructions: null,
    branding: {},
    order_type: "ON_DEMAND",
    pickup_address: null,
    passenger_name: null,
    flight_info: null,
    ...overrides,
  };
}

describe("Tracking App", () => {
  beforeEach(() => {
    vi.useRealTimers();
    mockedFetchTracking.mockReset();
    mockedRescheduleDelivery.mockReset();
    window.history.pushState({}, "", "/tracking?token=test-token");
  });

  it("shows missing token error and skips API requests", async () => {
    window.history.pushState({}, "", "/tracking");

    render(<App />);

    expect(
      await screen.findByText(
        "Tracking token is missing. Please open the link from your delivery notification.",
      ),
    ).toBeInTheDocument();
    expect(mockedFetchTracking).not.toHaveBeenCalled();
  });

  it("shows invalid token error for expired/unauthorized links", async () => {
    mockedFetchTracking.mockRejectedValueOnce(
      new TrackingApiError("expired", 401),
    );

    render(<App />);

    expect(
      await screen.findByText("This tracking link is invalid or expired."),
    ).toBeInTheDocument();
    expect(mockedFetchTracking).toHaveBeenCalledWith("test-token");
  });

  it("renders terminal state and hides reschedule form when delivered", async () => {
    mockedFetchTracking.mockResolvedValueOnce(
      buildTrackingResponse({ status: "DELIVERED" }),
    );

    render(<App />);

    expect(
      await screen.findByText(
        "Live tracking updates are paused because this delivery is in a terminal state.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /submit reschedule request/i }),
    ).not.toBeInTheDocument();
  });

  it("submits reschedule request for failed deliveries", async () => {
    mockedFetchTracking.mockResolvedValueOnce(
      buildTrackingResponse({ status: "FAILED", can_reschedule: true, can_update_instructions: true }),
    );
    mockedRescheduleDelivery.mockResolvedValueOnce(
      buildTrackingResponse({ status: "RESCHEDULED", can_reschedule: false, customer_instructions: "Call on arrival" }),
    );

    render(<App />);
    expect(await screen.findByText("Request Redelivery")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Earliest"), {
      target: { value: "2026-02-25T10:00" },
    });
    fireEvent.change(screen.getByLabelText("Latest"), {
      target: { value: "2026-02-25T12:00" },
    });
    fireEvent.change(screen.getByLabelText("Delivery instructions"), {
      target: { value: "Call on arrival" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: /submit reschedule request/i }),
    );

    await waitFor(() => expect(mockedRescheduleDelivery).toHaveBeenCalledTimes(1));

    const [token, earliestIso, latestIso, customerInstructions] = mockedRescheduleDelivery.mock.calls[0];
    expect(token).toBe("test-token");
    expect(new Date(earliestIso).toISOString()).toBe(earliestIso);
    expect(new Date(latestIso).toISOString()).toBe(latestIso);
    expect(earliestIso < latestIso).toBe(true);
    expect(customerInstructions).toBe("Call on arrival");

    await waitFor(() =>
      expect(screen.queryByText("Request Redelivery")).not.toBeInTheDocument(),
    );
  });

  it("hides the map when sender disables live tracking", async () => {
    mockedFetchTracking.mockResolvedValueOnce(
      buildTrackingResponse({ show_live_map: false, eta_visibility: "ETA_RANGE" }),
    );

    render(<App />);

    expect(await screen.findByText("Live map hidden")).toBeInTheDocument();
    expect(screen.queryByTestId("tracking-map")).not.toBeInTheDocument();
  });

  it("stops polling after a terminal status is reached", async () => {
    const clearIntervalSpy = vi.spyOn(window, "clearInterval");
    mockedFetchTracking.mockResolvedValue(
      buildTrackingResponse({ status: "DELIVERED" }),
    );

    render(<App />);
    await screen.findByText(
      "Live tracking updates are paused because this delivery is in a terminal state.",
    );
    await waitFor(() => expect(clearIntervalSpy).toHaveBeenCalled());

    expect(mockedFetchTracking).toHaveBeenCalledTimes(1);
  });

  it("keeps last tracking data visible when a subsequent poll fails", async () => {
    const intervalCallbacks: Array<() => void | Promise<void>> = [];
    vi.spyOn(window, "setInterval").mockImplementation((callback) => {
      intervalCallbacks.push(callback as () => void | Promise<void>);
      return 1 as unknown as number;
    });
    vi.spyOn(window, "clearInterval").mockImplementation(() => undefined);

    mockedFetchTracking
      .mockResolvedValueOnce(buildTrackingResponse({ delivery_address: "Preserved Address" }))
      .mockRejectedValueOnce(new TrackingApiError("temporary upstream failure", 503));

    render(<App />);
    expect(await screen.findByText("Preserved Address")).toBeInTheDocument();

    await act(async () => {
      await intervalCallbacks[0]();
    });

    expect(await screen.findByText("temporary upstream failure")).toBeInTheDocument();
    expect(screen.getByText("Preserved Address")).toBeInTheDocument();
  });

  it("shows 'Hidden by sender' for Estimated Arrival when eta_visibility is NONE", async () => {
    mockedFetchTracking.mockResolvedValueOnce(
      buildTrackingResponse({ eta_visibility: "NONE", estimated_arrival: "2026-04-01T12:00:00Z" }),
    );
    render(<App />);
    expect(await screen.findByText("Hidden by sender")).toBeInTheDocument();
  });

  it("shows the estimated arrival time when eta_visibility is ETA_RANGE", async () => {
    mockedFetchTracking.mockResolvedValueOnce(
      buildTrackingResponse({ eta_visibility: "ETA_RANGE", estimated_arrival: "2026-04-01T12:00:00Z" }),
    );
    render(<App />);
    // The formatted date should be visible (locale-dependent but not "Hidden by sender")
    await waitFor(() =>
      expect(screen.queryByText("Hidden by sender")).not.toBeInTheDocument(),
    );
    expect(await screen.findByText("Estimated Arrival")).toBeInTheDocument();
  });

  it("shows customer_instructions pre-filled when present", async () => {
    mockedFetchTracking.mockResolvedValueOnce(
      buildTrackingResponse({ can_reschedule: true, can_update_instructions: true, customer_instructions: "Leave at front door" }),
    );
    render(<App />);
    const textarea = await screen.findByLabelText("Delivery instructions") as HTMLTextAreaElement;
    expect(textarea.value).toBe("Leave at front door");
  });

  it("shows 404 error and stops polling on 404 response", async () => {
    const intervalCallbacks: Array<() => void | Promise<void>> = [];
    vi.spyOn(window, "setInterval").mockImplementation((callback) => {
      intervalCallbacks.push(callback as () => void | Promise<void>);
      return 2 as unknown as number;
    });
    const clearIntervalSpy = vi.spyOn(window, "clearInterval").mockImplementation(() => undefined);

    mockedFetchTracking.mockRejectedValueOnce(new TrackingApiError("Not found", 404));

    render(<App />);

    expect(await screen.findByText("Order not found for this tracking link.")).toBeInTheDocument();

    // Trigger another poll — the stopPollingRef should prevent further fetches
    if (intervalCallbacks.length > 0) {
      await act(async () => {
        await intervalCallbacks[0]();
      });
    }

    // fetchTracking called only once (the initial call, not again after 404)
    expect(mockedFetchTracking).toHaveBeenCalledTimes(1);
    void clearIntervalSpy;
  });

  it("shows validation error when earliest >= latest in reschedule form", async () => {
    mockedFetchTracking.mockResolvedValueOnce(
      buildTrackingResponse({ status: "FAILED", can_reschedule: true }),
    );
    render(<App />);
    await screen.findByText("Request Redelivery");

    // Same datetime for earliest and latest — should fail validation
    fireEvent.change(screen.getByLabelText("Earliest"), { target: { value: "2026-04-01T12:00" } });
    fireEvent.change(screen.getByLabelText("Latest"), { target: { value: "2026-04-01T10:00" } });
    fireEvent.click(screen.getByRole("button", { name: /submit reschedule request/i }));

    expect(await screen.findByText("Earliest time must be before latest time.")).toBeInTheDocument();
    expect(mockedRescheduleDelivery).not.toHaveBeenCalled();
  });

  it("shows reschedule API failure error without clearing the form", async () => {
    mockedFetchTracking.mockResolvedValueOnce(
      buildTrackingResponse({ status: "FAILED", can_reschedule: true }),
    );
    mockedRescheduleDelivery.mockRejectedValueOnce(new TrackingApiError("Conflict: order already rescheduled", 409));

    render(<App />);
    await screen.findByText("Request Redelivery");

    fireEvent.change(screen.getByLabelText("Earliest"), { target: { value: "2026-04-01T09:00" } });
    fireEvent.change(screen.getByLabelText("Latest"), { target: { value: "2026-04-01T17:00" } });
    fireEvent.click(screen.getByRole("button", { name: /submit reschedule request/i }));

    expect(
      await screen.findByText("Conflict: order already rescheduled"),
    ).toBeInTheDocument();
    // Form should stay visible so user can retry
    expect(screen.getByLabelText("Earliest")).toBeInTheDocument();
  });
});
