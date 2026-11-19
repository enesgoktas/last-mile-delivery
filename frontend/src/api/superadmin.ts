import type {
  FeatureFlagSchemaEntry,
  FeatureFlags,
  SuperadminInviteUserPayload,
  SuperadminInviteUserResponse,
  SuperadminTenantCreatePayload,
  SuperadminTenantDetail,
  SuperadminTenantSummary,
  SuperadminTenantUpdatePayload,
} from "../types";
import {
  createApiClient,
  type ApiRequestOptions,
} from "../../../shared/api-client";
import {
  clearAuthTokens,
  getStoredAccessToken,
  getStoredRefreshToken,
  saveAuthTokens,
} from "../state/authStorage";

function resolveDefaultApiBaseUrl(): string {
  const locationLike = globalThis.location;
  if (!locationLike) return "http://127.0.0.1:8000";
  const hostname = locationLike.hostname;
  if (!hostname || hostname === "localhost" || hostname === "127.0.0.1") {
    return "http://127.0.0.1:8000";
  }
  return `http://${hostname}:8000`;
}

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? resolveDefaultApiBaseUrl();

const client = createApiClient({
  baseUrl: API_BASE_URL,
  tokenStore: {
    getAccessToken: getStoredAccessToken,
    getRefreshToken: getStoredRefreshToken,
    saveAuthTokens,
    clearAuthTokens,
  },
});

const request = client.request;

type Opts = ApiRequestOptions;

const base = "/api/v1/superadmin";

export async function listTenants(token: string): Promise<SuperadminTenantSummary[]> {
  return request<SuperadminTenantSummary[]>(`${base}/tenants`, { token } satisfies Opts);
}

export async function getTenant(token: string, tenantId: string): Promise<SuperadminTenantDetail> {
  return request<SuperadminTenantDetail>(`${base}/tenants/${tenantId}`, { token });
}

export async function createTenant(
  token: string,
  payload: SuperadminTenantCreatePayload,
): Promise<SuperadminTenantDetail> {
  return request<SuperadminTenantDetail>(`${base}/tenants`, {
    method: "POST",
    token,
    body: payload,
  });
}

export async function updateTenant(
  token: string,
  tenantId: string,
  payload: SuperadminTenantUpdatePayload,
): Promise<SuperadminTenantDetail> {
  return request<SuperadminTenantDetail>(`${base}/tenants/${tenantId}`, {
    method: "PATCH",
    token,
    body: payload,
  });
}

export async function getFeatureFlags(token: string, tenantId: string): Promise<FeatureFlags> {
  return request<FeatureFlags>(`${base}/tenants/${tenantId}/feature-flags`, { token });
}

export async function updateFeatureFlags(
  token: string,
  tenantId: string,
  patch: Partial<FeatureFlags>,
): Promise<FeatureFlags> {
  return request<FeatureFlags>(`${base}/tenants/${tenantId}/feature-flags`, {
    method: "PATCH",
    token,
    body: patch,
  });
}

export async function getOperationsPolicy(
  token: string,
  tenantId: string,
): Promise<Record<string, unknown>> {
  return request<Record<string, unknown>>(
    `${base}/tenants/${tenantId}/operations-policy`,
    { token },
  );
}

export async function updateOperationsPolicy(
  token: string,
  tenantId: string,
  patch: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  return request<Record<string, unknown>>(
    `${base}/tenants/${tenantId}/operations-policy`,
    { method: "PATCH", token, body: patch },
  );
}

export async function inviteTenantUser(
  token: string,
  tenantId: string,
  payload: SuperadminInviteUserPayload,
): Promise<SuperadminInviteUserResponse> {
  return request<SuperadminInviteUserResponse>(
    `${base}/tenants/${tenantId}/users`,
    { method: "POST", token, body: payload },
  );
}

export async function getFeatureFlagsSchema(token: string): Promise<FeatureFlagSchemaEntry[]> {
  return request<FeatureFlagSchemaEntry[]>(`${base}/feature-flags/schema`, { token });
}

// ── Phase 2.3 — Charging stations ──

export interface ChargingStationSyncSummary {
  fetched: number;
  inserted: number;
  updated: number;
  marked_inactive: number;
  error: string | null;
}

export interface ChargingStationProviderStatus {
  provider: string;
  total: number;
  active: number;
  last_synced_at: string | null;
}

export async function triggerChargingStationSync(
  token: string,
): Promise<ChargingStationSyncSummary> {
  return request<ChargingStationSyncSummary>(`${base}/charging-stations/sync`, {
    method: "POST",
    token,
  });
}

export async function getChargingStationSyncStatus(
  token: string,
): Promise<{ by_provider: ChargingStationProviderStatus[] }> {
  return request<{ by_provider: ChargingStationProviderStatus[] }>(
    `${base}/charging-stations/sync-status`,
    { token },
  );
}
