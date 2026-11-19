import { describe, it, expect, vi, beforeEach } from "vitest";
import { fetchTracking, rescheduleDelivery, TrackingApiError } from "../api";

// ----- fetch mock setup -----

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

const mockFetch = vi.fn<typeof fetch>();

beforeEach(() => {
  vi.stubGlobal("fetch", mockFetch);
  mockFetch.mockReset();
});

// ----- TrackingApiError -----

describe("TrackingApiError", () => {
  it("constructs with message and status", () => {
    const err = new TrackingApiError("Not found", 404);
    expect(err.message).toBe("Not found");
    expect(err.status).toBe(404);
    expect(err.name).toBe("TrackingApiError");
    expect(err).toBeInstanceOf(Error);
  });
});

// ----- fetchTracking -----

describe("fetchTracking", () => {
  it("resolves with the parsed response body on success", async () => {
    const body = {
      order_id: "o1",
      status: "IN_TRANSIT",
      delivery_address: "5 Elm St",
      delivery_lat: 40.0,
      delivery_lon: 28.0,
      can_reschedule: false,
      can_update_instructions: false,
      show_live_map: true,
      eta_visibility: "LIVE_MAP",
    };
    mockFetch.mockResolvedValue(jsonResponse(200, body));

    const result = await fetchTracking("my-token");
    expect(result.order_id).toBe("o1");
    expect(result.status).toBe("IN_TRANSIT");
  });

  it("URL-encodes the token in the request path", async () => {
    const body = { order_id: "o1", status: "IN_TRANSIT", delivery_address: "x", delivery_lat: 0, delivery_lon: 0, can_reschedule: false, can_update_instructions: false, show_live_map: false, eta_visibility: "NONE" };
    mockFetch.mockResolvedValue(jsonResponse(200, body));

    await fetchTracking("token+with/special=chars");

    const calledUrl = mockFetch.mock.calls[0][0] as string;
    expect(calledUrl).toContain(encodeURIComponent("token+with/special=chars"));
    expect(calledUrl).not.toContain("token+with/special=chars");
  });

  it("throws TrackingApiError on 401 with 'detail' from response body", async () => {
    mockFetch.mockResolvedValue(jsonResponse(401, { detail: "Token expired" }));

    await expect(fetchTracking("bad-token")).rejects.toMatchObject({
      status: 401,
      message: "Token expired",
    });
  });

  it("throws TrackingApiError on 404 with fallback message when body has no detail", async () => {
    mockFetch.mockResolvedValue(jsonResponse(404, {}));

    await expect(fetchTracking("missing")).rejects.toMatchObject({
      status: 404,
      message: "Tracking request failed",
    });
  });

  it("throws TrackingApiError on 500", async () => {
    mockFetch.mockResolvedValue(jsonResponse(500, { detail: "Internal error" }));

    await expect(fetchTracking("tok")).rejects.toMatchObject({
      status: 500,
      message: "Internal error",
    });
  });

  it("passes an AbortSignal to fetch for timeout", async () => {
    const body = { order_id: "o1", status: "IN_TRANSIT", delivery_address: "x", delivery_lat: 0, delivery_lon: 0, can_reschedule: false, can_update_instructions: false, show_live_map: false, eta_visibility: "NONE" };
    mockFetch.mockResolvedValue(jsonResponse(200, body));

    await fetchTracking("tok");

    const options = mockFetch.mock.calls[0][1] as RequestInit;
    expect(options?.signal).toBeDefined();
  });

  it("throws TrackingApiError when the tracking service is unreachable", async () => {
    mockFetch.mockRejectedValueOnce(new TypeError("network down"));

    await expect(fetchTracking("tok")).rejects.toMatchObject({
      status: 0,
      message: "Cannot reach the tracking service right now.",
    });
  });
});

// ----- rescheduleDelivery -----

describe("rescheduleDelivery", () => {
  it("POSTs to the reschedule endpoint with the correct payload", async () => {
    const body = { order_id: "o1", status: "RESCHEDULED", delivery_address: "x", delivery_lat: 0, delivery_lon: 0, can_reschedule: false, can_update_instructions: false, show_live_map: false, eta_visibility: "NONE" };
    mockFetch.mockResolvedValue(jsonResponse(200, body));

    await rescheduleDelivery("my-tok", "2026-04-01T09:00:00Z", "2026-04-01T17:00:00Z", "Leave at door");

    const [calledUrl, calledOptions] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(calledUrl).toContain(encodeURIComponent("my-tok"));
    expect(calledUrl).toContain("/reschedule");
    expect(calledOptions.method).toBe("POST");

    const parsedBody = JSON.parse(calledOptions.body as string);
    expect(parsedBody.earliest_delivery).toBe("2026-04-01T09:00:00Z");
    expect(parsedBody.latest_delivery).toBe("2026-04-01T17:00:00Z");
    expect(parsedBody.customer_instructions).toBe("Leave at door");
  });

  it("URL-encodes the token in the reschedule path", async () => {
    const body = { order_id: "o1", status: "RESCHEDULED", delivery_address: "x", delivery_lat: 0, delivery_lon: 0, can_reschedule: false, can_update_instructions: false, show_live_map: false, eta_visibility: "NONE" };
    mockFetch.mockResolvedValue(jsonResponse(200, body));

    await rescheduleDelivery("tok/special+chars", "2026-04-01T09:00:00Z", "2026-04-01T17:00:00Z");

    const calledUrl = mockFetch.mock.calls[0][0] as string;
    expect(calledUrl).toContain(encodeURIComponent("tok/special+chars"));
  });

  it("omits customer_instructions from body when not provided", async () => {
    const body = { order_id: "o1", status: "RESCHEDULED", delivery_address: "x", delivery_lat: 0, delivery_lon: 0, can_reschedule: false, can_update_instructions: false, show_live_map: false, eta_visibility: "NONE" };
    mockFetch.mockResolvedValue(jsonResponse(200, body));

    await rescheduleDelivery("tok", "2026-04-01T09:00:00Z", "2026-04-01T17:00:00Z");

    const parsedBody = JSON.parse(mockFetch.mock.calls[0][1]?.body as string);
    expect(parsedBody.customer_instructions).toBeUndefined();
  });

  it("throws TrackingApiError with detail message on failure", async () => {
    mockFetch.mockResolvedValue(jsonResponse(409, { detail: "Cannot reschedule: already rescheduled" }));

    await expect(
      rescheduleDelivery("tok", "2026-04-01T09:00:00Z", "2026-04-01T17:00:00Z"),
    ).rejects.toMatchObject({
      status: 409,
      message: "Cannot reschedule: already rescheduled",
    });
  });

  it("passes an AbortSignal to fetch for timeout", async () => {
    const body = { order_id: "o1", status: "RESCHEDULED", delivery_address: "x", delivery_lat: 0, delivery_lon: 0, can_reschedule: false, can_update_instructions: false, show_live_map: false, eta_visibility: "NONE" };
    mockFetch.mockResolvedValue(jsonResponse(200, body));

    await rescheduleDelivery("tok", "2026-04-01T09:00:00Z", "2026-04-01T17:00:00Z");

    const options = mockFetch.mock.calls[0][1] as RequestInit;
    expect(options?.signal).toBeDefined();
  });
});
