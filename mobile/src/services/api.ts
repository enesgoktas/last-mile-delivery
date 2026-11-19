import {
  clearAuthTokens,
  getAccessToken,
  getRefreshToken,
  saveAuthTokens,
} from "./auth";
import type {
  CourierProfile,
  CourierRoute,
  RouteStop,
  CourierCurrentLoad,
  OrderPackage,
  PushProvider,
} from "../types";
import {
  ApiClientError,
  createApiClient,
  type ApiRequestOptions,
} from "../../../shared/api-client";
import type {
  PushTokenUpdateContract,
  TokenResponseContract,
} from "../../../shared/api-contract";

const API_BASE_URL = process.env.EXPO_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends ApiClientError {
  constructor(message: string, status: number) {
    super(message, status);
    this.name = "ApiError";
  }
}

type RequestOptions = ApiRequestOptions;

export type StopCompletionPayload = {
  photo_urls?: string[];
  signature_url?: string;
  receiver_name?: string;
  otp_code?: string;
  notes?: string;
};

export type PodUploadUrlResponse = {
  upload_url: string;
  file_url: string;
  object_key: string;
  expires_at: string;
};

const client = createApiClient({
  baseUrl: API_BASE_URL,
  defaultAuth: true,
  tokenStore: {
    getAccessToken,
    getRefreshToken,
    saveAuthTokens,
    clearAuthTokens,
  },
  errorFactory: (message, status) => new ApiError(message, status),
  networkErrorMessage: `Cannot reach API at ${API_BASE_URL}. Confirm the courier backend is reachable from this device.`,
});

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  return client.request<T>(path, options);
}

export async function login(email: string, password: string, tenantSlug?: string) {
  const payload = await request<TokenResponseContract>("/api/v1/auth/login", {
    method: "POST",
    auth: false,
    body: {
      email,
      password,
      tenant_slug: tenantSlug?.trim() ? tenantSlug.trim() : undefined,
    },
  });
  await saveAuthTokens({
    accessToken: payload.access_token,
    refreshToken: payload.refresh_token,
  });
}

export async function logout() {
  const refreshToken = await getRefreshToken();
  const accessToken = await getAccessToken();
  try {
    if (accessToken) {
      await request<{ status: string }>("/api/v1/auth/logout", {
        method: "POST",
        body: { refresh_token: refreshToken ?? undefined },
        retryOnUnauthorized: false,
      });
    }
  } finally {
    await clearAuthTokens();
  }
}

export async function getMyCourierProfile() {
  return request<CourierProfile>("/api/v1/couriers/me");
}

export async function updateCourierStatus(
  courierId: string,
  status: "ONLINE" | "OFFLINE" | "ON_BREAK",
) {
  return request<CourierProfile>(`/api/v1/couriers/${courierId}/status`, {
    method: "PUT",
    body: { status },
  });
}

export async function updateCourierLocation(
  courierId: string,
  payload: {
    lat: number;
    lon: number;
    heading?: number;
    speed_kmh?: number;
    accuracy_m: number;
  },
) {
  return request<{ status: string; geofence_status?: string }>(
    `/api/v1/couriers/${courierId}/location`,
    {
      method: "PUT",
      body: payload,
    },
  );
}

export async function updateCourierPushToken(
  courierId: string,
  payload: PushTokenUpdateContract | null,
) {
  return request<CourierProfile>(`/api/v1/couriers/${courierId}/push-token`, {
    method: "PUT",
    body:
      payload
        ? payload
        : {
            push_provider: null,
            push_token: null,
          },
  });
}

export async function getActiveRoute(courierId: string) {
  return request<CourierRoute | null>(`/api/v1/couriers/${courierId}/active-route`);
}

export async function getNextStop(courierId: string) {
  return request<RouteStop | null>(`/api/v1/couriers/${courierId}/next-stop`);
}

export async function startRoute(routeId: string) {
  return request<CourierRoute>(`/api/v1/routes/${routeId}/start`, {
    method: "POST",
  });
}

export async function arriveStop(routeId: string, stopId: string) {
  return request<RouteStop>(`/api/v1/routes/${routeId}/stops/${stopId}/arrive`, {
    method: "POST",
    body: {},
  });
}

export async function completeStop(
  routeId: string,
  stopId: string,
  payload?: StopCompletionPayload,
) {
  return request<RouteStop>(`/api/v1/routes/${routeId}/stops/${stopId}/complete`, {
    method: "POST",
    body: payload ?? {},
  });
}

export async function requestPodUploadUrl(
  routeId: string,
  stopId: string,
  fileType: "photo" | "signature",
  contentType: string,
) {
  return request<PodUploadUrlResponse>(
    `/api/v1/routes/${routeId}/stops/${stopId}/pod/upload-url`,
    {
      method: "POST",
      body: {
        file_type: fileType,
        content_type: contentType,
      },
    },
  );
}

export async function failStop(
  routeId: string,
  stopId: string,
  failureReason: string,
  notes?: string,
  customReasonLabel?: string,
) {
  return request<RouteStop>(`/api/v1/routes/${routeId}/stops/${stopId}/fail`, {
    method: "POST",
    body: {
      failure_reason: failureReason,
      notes,
      custom_reason_label: customReasonLabel,
    },
  });
}

export async function getCourierCurrentLoad(courierId: string) {
  return request<CourierCurrentLoad>(`/api/v1/couriers/${courierId}/current-load`);
}

export async function getOrderPackages(orderId: string) {
  return request<OrderPackage[]>(`/api/v1/orders/${orderId}/packages`);
}
