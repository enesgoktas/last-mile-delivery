import type { TrackingResponseContract } from "../../shared/api-contract";
import {
  ApiClientError,
  createApiClient,
} from "../../shared/api-client";

export type TrackingResponse = TrackingResponseContract;

export class TrackingApiError extends ApiClientError {
  constructor(message: string, status: number) {
    super(message, status);
    this.name = "TrackingApiError";
  }
}

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
const FETCH_TIMEOUT_MS = 15_000;
const client = createApiClient({
  baseUrl: API_BASE_URL,
  errorFactory: (message, status) => new TrackingApiError(message, status),
  networkErrorMessage: "Cannot reach the tracking service right now.",
});

export async function fetchTracking(token: string): Promise<TrackingResponse> {
  return client.request<TrackingResponse>(`/api/v1/tracking/${encodeURIComponent(token)}`, {
    auth: false,
    errorFallbackMessage: "Tracking request failed",
    signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
  });
}

export async function rescheduleDelivery(
  token: string,
  earliestDelivery: string,
  latestDelivery: string,
  customerInstructions?: string,
): Promise<TrackingResponse> {
  return client.request<TrackingResponse>(`/api/v1/tracking/${encodeURIComponent(token)}/reschedule`, {
    method: "POST",
    auth: false,
    body: {
      earliest_delivery: earliestDelivery,
      latest_delivery: latestDelivery,
      customer_instructions: customerInstructions,
    },
    errorFallbackMessage: "Reschedule request failed",
    signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
  });
}
