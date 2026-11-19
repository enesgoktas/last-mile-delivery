import type {
  ApiKeyCreateResponse,
  ApiKeyRecord,
  AnalyticsSummary,
  AuditLogEntry,
  Courier,
  CourierCreate,
  CourierUpdate,
  CourierLocation,
  Depot,
  DepotCreate,
  DepotUpdate,
  EPAMenuEntry,
  EPASearchResult,
  ExceptionCenterSummary,
  ImportPreview,
  InventoryEvent,
  OperationsPolicy,
  OptimizationRun,
  OptimizationRunPreview,
  AssignRangeResponse,
  Order,
  OrderCreate,
  OrderLeg,
  OrderPackage,
  OrderReturnCreate,
  OrderStatusHistoryItem,
  RecurringOrderPreviewResponse,
  RecurringOrderRunResponse,
  RecurringOrderTemplate,
  RecurringOrderTemplateCreate,
  RecurringOrderTemplateUpdate,
  RouteScenarioComparison,
  RouteScenarioCreateResponse,
  RouteScenarioSelectResponse,
  PolicyPreset,
  RbacOverride,
  Route,
  RouteGeometryResponse,
  SLAAlert,
  SLAScanResult,
  TenantBranding,
  VehicleSpec,
  VehicleTag,
  VehicleTagCreate,
  WebhookDeliveryLog,
  WebhookEndpoint,
  WebhookEndpointCreatedResponse,
} from "../types";
import {
  createApiClient,
  type ApiRequestOptions,
  type PaginatedResult,
} from "../../../shared/api-client";
import type { TokenResponseContract } from "../../../shared/api-contract";
import {
  clearAuthTokens,
  getStoredAccessToken,
  getStoredRefreshToken,
  saveAuthTokens,
} from "../state/authStorage";

function resolveDefaultApiBaseUrl(): string {
  const locationLike = globalThis.location;
  if (!locationLike) {
    return "http://127.0.0.1:8000";
  }

  const hostname = locationLike.hostname;
  if (!hostname || hostname === "localhost" || hostname === "127.0.0.1") {
    return "http://127.0.0.1:8000";
  }

  return `http://${hostname}:8000`;
}

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? resolveDefaultApiBaseUrl();

type RequestOptions = ApiRequestOptions;

const client = createApiClient({
  baseUrl: API_BASE_URL,
  tokenStore: {
    getAccessToken: getStoredAccessToken,
    getRefreshToken: getStoredRefreshToken,
    saveAuthTokens,
    clearAuthTokens,
  },
  networkErrorMessage: `Cannot reach API at ${API_BASE_URL}. Confirm backend is running and CORS allows your frontend origin.`,
});

const request = client.request;
const requestPaginated = client.requestPaginated;

// ── Auth ──

export async function login(email: string, password: string, tenantSlug?: string) {
  return request<TokenResponseContract>(
    "/api/v1/auth/login",
    {
      method: "POST",
      body: {
        email,
        password,
        tenant_slug: tenantSlug?.trim() ? tenantSlug.trim() : undefined,
      },
      auth: false,
      retryOnUnauthorized: false,
    },
  );
}

export async function signup(payload: {
  company_name: string;
  full_name: string;
  email: string;
  password: string;
}) {
  return request<TokenResponseContract>(
    "/api/v1/auth/signup",
    { method: "POST", body: payload, auth: false, retryOnUnauthorized: false },
  );
}

export async function logout(accessToken?: string | null, refreshToken?: string | null) {
  const token = accessToken ?? getStoredAccessToken();
  if (!token) {
    clearAuthTokens();
    return;
  }
  try {
    await request<{ status: string }>("/api/v1/auth/logout", {
      method: "POST",
      token,
      body: { refresh_token: refreshToken ?? getStoredRefreshToken() ?? undefined },
      retryOnUnauthorized: false,
    });
  } finally {
    clearAuthTokens();
  }
}

export async function listUsers(token: string) {
  return request<Array<{ id: string; tenant_id: string; email: string; role: string; is_active: boolean; created_at: string }>>(
    "/api/v1/auth/users", { token },
  );
}

export async function registerUser(token: string, payload: { email: string; password: string; role: string }) {
  return request<{ id: string; tenant_id: string; email: string; role: string; is_active: boolean; created_at: string }>(
    "/api/v1/auth/register", { method: "POST", token, body: payload },
  );
}

export async function updateUser(token: string, userId: string, payload: { role?: string; is_active?: boolean }) {
  return request<{ id: string; tenant_id: string; email: string; role: string; is_active: boolean; created_at: string }>(
    `/api/v1/auth/users/${userId}`, { method: "PATCH", token, body: payload },
  );
}

export async function inviteUser(token: string, payload: { email: string; role: string }) {
  return request<{ status: string }>("/api/v1/auth/invite", { method: "POST", token, body: payload });
}

export async function acceptInvite(inviteToken: string, newPassword: string) {
  return request<{ access_token: string; refresh_token: string }>("/api/v1/auth/accept-invite", {
    method: "POST",
    body: { token: inviteToken, new_password: newPassword },
  });
}

export async function forgotPassword(email: string) {
  return request<{ status: string }>("/api/v1/auth/forgot-password", { method: "POST", body: { email } });
}

export async function resetPassword(token: string, newPassword: string) {
  return request<{ status: string }>("/api/v1/auth/reset-password", {
    method: "POST",
    body: { token, new_password: newPassword },
  });
}

// ── Orders ──

export async function listOrders(token: string, query = "") {
  const suffix = query ? `?${query}` : "?limit=100";
  return request<Order[]>(`/api/v1/orders${suffix}`, { token });
}

export async function listOrdersPaginated(token: string, query = "") {
  const suffix = query ? `?${query}` : "?limit=25";
  return requestPaginated<Order>(`/api/v1/orders${suffix}`, { token });
}

export async function getOrder(token: string, orderId: string) {
  return request<Order>(`/api/v1/orders/${orderId}`, { token });
}

export async function createOrder(token: string, payload: OrderCreate) {
  return request<Order>("/api/v1/orders", { method: "POST", token, body: payload });
}

export async function updateOrder(token: string, orderId: string, payload: Partial<OrderCreate>) {
  return request<Order>(`/api/v1/orders/${orderId}`, { method: "PUT", token, body: payload });
}

export async function getOrderHistory(token: string, orderId: string) {
  return request<OrderStatusHistoryItem[]>(`/api/v1/orders/${orderId}/history`, { token });
}

export async function assignOrder(token: string, orderId: string, courierId: string) {
  return request<Order>(`/api/v1/orders/${orderId}/assign`, {
    method: "POST", token, body: { courier_id: courierId },
  });
}

export async function setPreferredCourier(token: string, orderId: string, courierId: string | null) {
  return request<Order>(`/api/v1/orders/${orderId}/preferred-courier`, {
    method: "PUT",
    token,
    body: { courier_id: courierId },
  });
}

export async function cancelOrder(token: string, orderId: string) {
  return request<Order>(`/api/v1/orders/${orderId}/cancel`, { method: "POST", token });
}

export async function rescheduleOrder(
  token: string,
  orderId: string,
  payload: { earliest_delivery: string; latest_delivery: string },
) {
  return request<Order>(`/api/v1/orders/${orderId}/reschedule`, {
    method: "POST", token, body: payload,
  });
}

export async function createOrderReturn(token: string, orderId: string, payload: OrderReturnCreate) {
  return request<Order>(`/api/v1/orders/${orderId}/returns`, {
    method: "POST", token, body: payload,
  });
}

export async function getOrderLegs(token: string, orderId: string) {
  return request<OrderLeg[]>(`/api/v1/orders/${orderId}/legs`, { token });
}

export async function importOrdersPreview(token: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  return request<ImportPreview>("/api/v1/orders/import/preview", {
    method: "POST", token, rawBody: form, skipContentType: true,
  });
}

export async function importOrdersPublish(token: string, importId: string) {
  return request<{ published_count: number }>(`/api/v1/orders/import/${importId}/publish`, {
    method: "POST", token,
  });
}

// ── Couriers ──

export async function listCouriers(token: string) {
  return request<Courier[]>("/api/v1/couriers?limit=100", { token });
}

export async function listCouriersPaginated(token: string, skip = 0, limit = 25) {
  return requestPaginated<Courier>(`/api/v1/couriers?skip=${skip}&limit=${limit}`, { token });
}

export async function getCourier(token: string, courierId: string) {
  return request<Courier>(`/api/v1/couriers/${courierId}`, { token });
}

export async function createCourier(token: string, payload: CourierCreate) {
  return request<Courier>("/api/v1/couriers", { method: "POST", token, body: payload });
}

export async function updateCourier(token: string, courierId: string, payload: CourierUpdate) {
  return request<Courier>(`/api/v1/couriers/${courierId}`, { method: "PUT", token, body: payload });
}

export async function listCourierLocations(token: string) {
  return request<CourierLocation[]>("/api/v1/couriers/locations", { token });
}

export async function setCourierTags(token: string, courierId: string, tagIds: string[]) {
  return request<VehicleTag[]>(`/api/v1/couriers/${courierId}/tags`, {
    method: "PUT",
    token,
    body: { tag_ids: tagIds },
  });
}

export async function updateCourierBattery(token: string, courierId: string, pct: number) {
  return request<Courier>(`/api/v1/couriers/${courierId}/battery`, {
    method: "POST",
    token,
    body: { pct },
  });
}

// ── Tenant feature flags (read-only, dispatcher+) ──

export async function getMyFeatureFlags(token: string) {
  return request<import("../types").FeatureFlags>("/api/v1/settings/features", { token });
}

// ── Phase 2 ride analytics ──

export async function getRideAnalytics(token: string) {
  return request<import("../types").RideAnalyticsResponse>("/api/v1/analytics/rides", { token });
}

export async function getFleetUtilization(
  token: string,
  params?: { date_from?: string; date_to?: string },
) {
  const qs = params
    ? Object.entries(params)
        .filter(([, v]) => v != null)
        .map(([k, v]) => `${k}=${encodeURIComponent(v!)}`)
        .join("&")
    : "";
  const path = qs
    ? `/api/v1/analytics/fleet-utilization?${qs}`
    : "/api/v1/analytics/fleet-utilization";
  return request<import("../types").FleetUtilizationResponse>(path, { token });
}

export async function getAirportPunctuality(token: string) {
  return request<import("../types").AirportPunctualityResponse>(
    "/api/v1/analytics/airport-punctuality",
    { token },
  );
}

// ── Vehicle Tags ──

export async function listVehicleTags(token: string) {
  return request<VehicleTag[]>("/api/v1/vehicle-tags", { token });
}

export async function createVehicleTag(token: string, payload: VehicleTagCreate) {
  return request<VehicleTag>("/api/v1/vehicle-tags", { method: "POST", token, body: payload });
}

export async function updateVehicleTag(
  token: string,
  tagId: string,
  payload: Partial<VehicleTagCreate>,
) {
  return request<VehicleTag>(`/api/v1/vehicle-tags/${tagId}`, {
    method: "PATCH",
    token,
    body: payload,
  });
}

export async function deleteVehicleTag(token: string, tagId: string) {
  return request<void>(`/api/v1/vehicle-tags/${tagId}`, { method: "DELETE", token });
}

// ── Routes ──

export async function listRoutes(token: string, query?: string) {
  const qs = query ? `${query}&limit=100` : "limit=100";
  return request<Route[]>(`/api/v1/routes?${qs}`, { token });
}

export async function getRouteGeometry(token: string, routeId: string) {
  return request<RouteGeometryResponse>(`/api/v1/routes/${routeId}/geometry`, { token });
}

export async function triggerOptimize(token: string) {
  return request<{ task_id: string; message: string }>("/api/v1/routes/optimize", {
    method: "POST", token,
  });
}

export async function listOptimizationRuns(token: string) {
  return request<OptimizationRun[]>("/api/v1/routes/optimization-runs?limit=20", { token });
}

export async function reorderStops(token: string, routeId: string, stopIds: string[]) {
  return request<unknown>(`/api/v1/routes/${routeId}/stops/reorder`, {
    method: "PUT", token, body: { stop_ids: stopIds },
  });
}

export async function setDispatcherNote(token: string, routeId: string, stopId: string, dispatcherNote: string | null) {
  return request<unknown>(`/api/v1/routes/${routeId}/stops/${stopId}/dispatcher-note`, {
    method: "PATCH",
    token,
    body: { dispatcher_note: dispatcherNote },
  });
}

export async function failStop(
  token: string,
  routeId: string,
  stopId: string,
  body: { failure_reason: string; custom_reason_label?: string | null; notes?: string | null },
) {
  return request<unknown>(`/api/v1/routes/${routeId}/stops/${stopId}/fail`, {
    method: "POST",
    token,
    body,
  });
}

export async function triggerOptimizePreview(token: string) {
  return request<{ task_id: string; message: string }>("/api/v1/routes/optimize/preview", {
    method: "POST", token,
  });
}

export async function triggerAssignOptimal(token: string, targetDate: string) {
  return request<{ task_id: string; message: string }>(
    `/api/v1/dispatch/assign-optimal?target_date=${targetDate}`,
    { method: "POST", token },
  );
}

export async function createRouteScenarioComparison(token: string, targetDate: string) {
  return request<RouteScenarioCreateResponse>(
    `/api/v1/dispatch/scenario-comparisons?target_date=${targetDate}`,
    { method: "POST", token },
  );
}

export async function getRouteScenarioComparison(token: string, comparisonId: string) {
  return request<RouteScenarioComparison>(
    `/api/v1/dispatch/scenario-comparisons/${comparisonId}`,
    { token },
  );
}

export async function getRouteScenarioPreview(token: string, comparisonId: string, scenarioId: string) {
  return request<OptimizationRunPreview>(
    `/api/v1/dispatch/scenario-comparisons/${comparisonId}/scenarios/${scenarioId}/preview`,
    { token },
  );
}

export async function selectRouteScenario(token: string, comparisonId: string, scenarioId: string) {
  return request<RouteScenarioSelectResponse>(
    `/api/v1/dispatch/scenario-comparisons/${comparisonId}/select`,
    { method: "POST", token, body: { scenario_id: scenarioId } },
  );
}

export async function deleteRouteScenarioComparison(token: string, comparisonId: string) {
  return request<{ deleted_draft_routes: number; message: string }>(
    `/api/v1/dispatch/scenario-comparisons/${comparisonId}`,
    { method: "DELETE", token },
  );
}

export async function getOptimizationTaskStatus(token: string, taskId: string) {
  return request<{ task_id: string; status: string; result: Record<string, unknown> | null }>(
    `/api/v1/dispatch/optimize/${taskId}/status`,
    { token },
  );
}

export async function triggerUnassign(token: string, targetDate: string) {
  return request<{ unassigned_count: number; message: string }>(
    `/api/v1/dispatch/unassign?target_date=${targetDate}`,
    { method: "POST", token },
  );
}

export async function triggerAssignRange(
  token: string,
  dateFrom: string,
  dateTo: string,
  options: { draft?: boolean } = {},
) {
  // Use the new canonical /assign-range URL. The backend keeps
  // /assign-week as a deprecated alias for now; we prefer the new name
  // so the API contract reads honestly.
  const draftParam = options.draft ? "&draft=true" : "";
  return request<AssignRangeResponse>(
    `/api/v1/dispatch/assign-range?date_from=${dateFrom}&date_to=${dateTo}${draftParam}`,
    { method: "POST", token },
  );
}

// ── Recurring Orders ──

export async function listRecurringTemplates(
  token: string,
  isActive?: boolean,
) {
  const qs = isActive === undefined ? "" : `?is_active=${isActive}`;
  return request<RecurringOrderTemplate[]>(
    `/api/v1/recurring-orders${qs}`,
    { token },
  );
}

export async function getRecurringTemplate(token: string, id: string) {
  return request<RecurringOrderTemplate>(
    `/api/v1/recurring-orders/${id}`,
    { token },
  );
}

export async function createRecurringTemplate(
  token: string,
  payload: RecurringOrderTemplateCreate,
) {
  return request<RecurringOrderTemplate>("/api/v1/recurring-orders", {
    method: "POST",
    token,
    body: payload,
  });
}

export async function updateRecurringTemplate(
  token: string,
  id: string,
  payload: RecurringOrderTemplateUpdate,
) {
  return request<RecurringOrderTemplate>(`/api/v1/recurring-orders/${id}`, {
    method: "PUT",
    token,
    body: payload,
  });
}

export async function deleteRecurringTemplate(token: string, id: string) {
  return request<void>(`/api/v1/recurring-orders/${id}`, {
    method: "DELETE",
    token,
  });
}

export async function previewRecurringTemplate(
  token: string,
  id: string,
  horizonDays = 60,
) {
  return request<RecurringOrderPreviewResponse>(
    `/api/v1/recurring-orders/${id}/preview?horizon_days=${horizonDays}`,
    { token },
  );
}

export async function runRecurringTemplate(token: string, id: string) {
  return request<RecurringOrderRunResponse>(
    `/api/v1/recurring-orders/${id}/run`,
    { method: "POST", token },
  );
}

export async function runAllRecurringTemplates(token: string) {
  return request<RecurringOrderRunResponse>(
    "/api/v1/recurring-orders/run",
    { method: "POST", token },
  );
}

export async function dispatchOrderNow(token: string, orderId: string) {
  return request<{ assigned: boolean; result: string; courier_id: string | null; message: string }>(
    `/api/v1/dispatch/dispatch-now/${orderId}`,
    { method: "POST", token },
  );
}

export async function approveOptimizationRun(token: string, runId: string) {
  return request<{ approved_routes: number }>(`/api/v1/routes/optimization-runs/${runId}/approve`, {
    method: "POST", token,
  });
}

export async function rejectOptimizationRun(token: string, runId: string) {
  return request<{ deleted_routes: number }>(`/api/v1/routes/optimization-runs/${runId}/reject`, {
    method: "POST", token,
  });
}

export async function getOptimizationRunPreview(token: string, runId: string) {
  return request<OptimizationRunPreview>(`/api/v1/routes/optimization-runs/${runId}/preview`, {
    token,
  });
}

// ── Analytics ──

export async function getAnalyticsSummary(
  token: string,
  params?: { date_from?: string; date_to?: string },
) {
  const qs = params
    ? Object.entries(params)
        .filter(([, v]) => v != null)
        .map(([k, v]) => `${k}=${encodeURIComponent(v!)}`)
        .join("&")
    : "";
  const path = qs ? `/api/v1/analytics/summary?${qs}` : "/api/v1/analytics/summary";
  return request<AnalyticsSummary>(path, { token });
}

export async function getCourierAnalytics(
  token: string,
  params?: { date_from?: string; date_to?: string },
) {
  const qs = params
    ? Object.entries(params)
        .filter(([, v]) => v != null)
        .map(([k, v]) => `${k}=${encodeURIComponent(v!)}`)
        .join("&")
    : "";
  const path = qs ? `/api/v1/analytics/couriers?${qs}` : "/api/v1/analytics/couriers";
  return request<{ rows: Array<{ courier_id: string; assigned_orders: number; delivered_orders: number; failed_orders: number }> }>(
    path, { token },
  );
}

export async function getExceptionCenterSummary(token: string) {
  return request<ExceptionCenterSummary>("/api/v1/analytics/exception-center", { token });
}

export async function getOptimizationSavings(
  token: string,
  params?: { date_from?: string; date_to?: string },
) {
  const qs = params
    ? Object.entries(params)
        .filter(([, v]) => v != null)
        .map(([k, v]) => `${k}=${encodeURIComponent(v!)}`)
        .join("&")
    : "";
  const path = qs
    ? `/api/v1/analytics/optimization-savings?${qs}`
    : "/api/v1/analytics/optimization-savings";
  return request<import("../types").OptimizationSavingsResponse>(path, { token });
}

export async function getCostSavings(
  token: string,
  params?: { date_from?: string; date_to?: string },
) {
  const qs = params
    ? Object.entries(params)
        .filter(([, v]) => v != null)
        .map(([k, v]) => `${k}=${encodeURIComponent(v!)}`)
        .join("&")
    : "";
  const path = qs
    ? `/api/v1/analytics/cost-savings?${qs}`
    : "/api/v1/analytics/cost-savings";
  return request<import("../types").CostSavingsResponse>(path, { token });
}

export async function getOrderForensics(token: string, orderId: string) {
  return request<import("../types").OrderForensicsResponse>(
    `/api/v1/orders/${orderId}/forensics`,
    { token },
  );
}

export async function getSolverQuality(
  token: string,
  params?: { date_from?: string; date_to?: string },
) {
  const qs = params
    ? Object.entries(params)
        .filter(([, v]) => v != null)
        .map(([k, v]) => `${k}=${encodeURIComponent(v!)}`)
        .join("&")
    : "";
  const path = qs
    ? `/api/v1/analytics/solver-quality?${qs}`
    : "/api/v1/analytics/solver-quality";
  return request<import("../types").SolverQualityResponse>(path, { token });
}

export async function downloadOrdersCsv(
  token: string,
  params?: { date_from?: string; date_to?: string },
): Promise<{ blob: Blob; filename: string }> {
  const qs = params
    ? Object.entries(params)
        .filter(([, v]) => v != null)
        .map(([k, v]) => `${k}=${encodeURIComponent(v!)}`)
        .join("&")
    : "";
  const path = qs ? `/api/v1/analytics/export?${qs}` : "/api/v1/analytics/export";
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) {
    throw new Error(`CSV export failed: ${response.status} ${response.statusText}`);
  }
  // Server sends `Content-Disposition: attachment; filename=...`; fall back if it's missing.
  const disposition = response.headers.get("content-disposition") ?? "";
  const match = /filename=\"?([^\";]+)\"?/.exec(disposition);
  const filename = match?.[1] ?? "orders_export.csv";
  const blob = await response.blob();
  return { blob, filename };
}

// ── Integrations ──

export async function listApiKeys(token: string) {
  return request<ApiKeyRecord[]>("/api/v1/api-keys", { token });
}

export async function createApiKey(
  token: string,
  payload: { name: string; permissions: string[]; rate_limit_per_min: number },
) {
  return request<ApiKeyCreateResponse>("/api/v1/api-keys", { method: "POST", token, body: payload });
}

export async function revokeApiKey(token: string, apiKeyId: string) {
  return request<void>(`/api/v1/api-keys/${apiKeyId}`, { method: "DELETE", token });
}

export async function listWebhooks(token: string) {
  return request<WebhookEndpoint[]>("/api/v1/webhooks", { token });
}

export async function createWebhook(
  token: string,
  payload: { client_id: string; url: string; events: string[]; secret?: string },
) {
  return request<WebhookEndpointCreatedResponse>("/api/v1/webhooks", { method: "POST", token, body: payload });
}

export async function deleteWebhook(token: string, webhookId: string) {
  return request<void>(`/api/v1/webhooks/${webhookId}`, { method: "DELETE", token });
}

export async function getWebhookLogs(token: string, webhookId: string) {
  return request<WebhookDeliveryLog[]>(`/api/v1/webhooks/${webhookId}/logs?limit=25`, { token });
}

// ── Depots ──

export async function listDepots(token: string) {
  return request<Depot[]>("/api/v1/depots?limit=100", { token });
}

export async function getDepot(token: string, depotId: string) {
  return request<Depot>(`/api/v1/depots/${depotId}`, { token });
}

export async function createDepot(token: string, payload: DepotCreate) {
  return request<Depot>("/api/v1/depots", { method: "POST", token, body: payload });
}

export async function updateDepot(token: string, depotId: string, payload: DepotUpdate) {
  return request<Depot>(`/api/v1/depots/${depotId}`, { method: "PUT", token, body: payload });
}

export async function deleteDepot(token: string, depotId: string) {
  return request<void>(`/api/v1/depots/${depotId}`, { method: "DELETE", token });
}

// ── Packages / Inventory ──

export async function listOrderPackages(token: string, orderId: string) {
  return request<OrderPackage[]>(`/api/v1/orders/${orderId}/packages`, { token });
}

export async function getPackageEvents(token: string, packageId: string) {
  return request<InventoryEvent[]>(`/api/v1/packages/${packageId}/events`, { token });
}

// ── Geocoding ──

export type GeocodeResult = { display_name: string; lat: number; lon: number };

export async function geocodeSearch(token: string, query: string, limit = 5): Promise<GeocodeResult[]> {
  return request<GeocodeResult[]>(
    `/api/v1/geocode?q=${encodeURIComponent(query)}&limit=${limit}`,
    { token },
  );
}

// ── SLA Alerts ──

export async function listSLAAlerts(token: string, severity?: string, limit = 100) {
  const params = new URLSearchParams();
  if (severity) params.set("severity", severity);
  params.set("limit", String(limit));
  return request<SLAAlert[]>(`/api/v1/sla-alerts?${params}`, { token });
}

export async function acknowledgeSLAAlert(token: string, alertId: string) {
  return request<SLAAlert>(`/api/v1/sla-alerts/${alertId}/acknowledge`, { method: "POST", token });
}

export async function triggerSLAScan(token: string) {
  return request<SLAScanResult>("/api/v1/sla-alerts/scan", { method: "POST", token });
}

// ── Constraints ──

export type ConstraintSet = {
  max_route_distance_km?: number | null;
  max_stops_per_route?: number | null;
  max_weight_kg?: number | null;
  max_volume_cm3?: number | null;
  max_packages?: number | null;
  max_trips_per_day?: number | null;
  depot_reload_time_minutes?: number | null;
  max_shift_hours?: number | null;
  break_duration_minutes?: number | null;
  break_earliest_offset_hours?: number | null;
  break_latest_offset_hours?: number | null;
  break_required?: boolean | null;
  service_time_minutes?: number | null;
  allow_fragile?: boolean | null;
  allow_temp_sensitive?: boolean | null;
};

export async function getGlobalConstraints(token: string) {
  return request<ConstraintSet>("/api/v1/constraints/global", { token });
}

export async function setGlobalConstraints(token: string, constraints: ConstraintSet) {
  return request<ConstraintSet>("/api/v1/constraints/global", { method: "PUT", token, body: constraints });
}

export async function getCourierConstraints(token: string, courierId: string) {
  return request<ConstraintSet>(`/api/v1/constraints/couriers/${courierId}`, { token });
}

export async function getCourierOverrides(token: string, courierId: string) {
  return request<ConstraintSet>(`/api/v1/constraints/couriers/${courierId}/overrides`, { token });
}

export async function setCourierConstraints(token: string, courierId: string, constraints: ConstraintSet) {
  return request<ConstraintSet>(`/api/v1/constraints/couriers/${courierId}`, { method: "PUT", token, body: constraints });
}

export async function clearCourierConstraints(token: string, courierId: string) {
  return request<void>(`/api/v1/constraints/couriers/${courierId}`, { method: "DELETE", token });
}

// ── Operations Policy ──

export async function getOperationsPolicy(token: string) {
  return request<OperationsPolicy>("/api/v1/settings/operations-policy", { token });
}

export async function saveOperationsPolicy(token: string, policy: OperationsPolicy) {
  return request<OperationsPolicy>("/api/v1/settings/operations-policy", {
    method: "PUT",
    token,
    body: policy,
  });
}

export async function listOperationsPolicyPresets(token: string) {
  return request<PolicyPreset[]>("/api/v1/settings/operations-policy/presets", { token });
}

export async function applyOperationsPolicyPreset(token: string, presetKey: string) {
  return request<OperationsPolicy>(`/api/v1/settings/operations-policy/presets/${presetKey}`, {
    method: "POST",
    token,
  });
}

// ── Audit Logs ──

export async function listAuditLogs(
  token: string,
  params: { action?: string; resource_type?: string; limit?: number; offset?: number } = {},
) {
  const qs = new URLSearchParams();
  if (params.action) qs.set("action", params.action);
  if (params.resource_type) qs.set("resource_type", params.resource_type);
  if (params.limit != null) qs.set("limit", String(params.limit));
  if (params.offset != null) qs.set("offset", String(params.offset));
  const query = qs.toString() ? `?${qs.toString()}` : "";
  return request<AuditLogEntry[]>(`/api/v1/audit-logs${query}`, { token });
}

// ── Branding ──

export async function getBranding(token: string) {
  return request<TenantBranding>("/api/v1/branding", { token });
}

export async function updateBranding(token: string, payload: Partial<TenantBranding>) {
  return request<TenantBranding>("/api/v1/branding", { method: "PATCH", token, body: payload });
}

// ── RBAC ──

export async function listRbacOverrides(token: string) {
  return request<RbacOverride[]>("/api/v1/rbac/overrides", { token });
}

export async function setRbacOverride(
  token: string,
  payload: { role: string; permission: string; granted: boolean },
) {
  return request<RbacOverride>("/api/v1/rbac/overrides", { method: "PUT", token, body: payload });
}

export async function deleteRbacOverride(token: string, role: string, permission: string) {
  return request<void>(`/api/v1/rbac/overrides?role=${encodeURIComponent(role)}&permission=${encodeURIComponent(permission)}`, {
    method: "DELETE",
    token,
  });
}

export async function listAllPermissions(token: string) {
  return request<string[]>("/api/v1/rbac/permissions/all", { token });
}

export async function getMyPermissions(token: string) {
  return request<{ role: string; permissions: Record<string, boolean> }>("/api/v1/rbac/permissions", { token });
}

// ── Vehicle Specs ──

export async function listVehicleSpecs(
  token: string,
  params: { vehicle_type?: string; fuel_type?: string } = {},
) {
  const qs = new URLSearchParams();
  if (params.vehicle_type) qs.set("vehicle_type", params.vehicle_type);
  if (params.fuel_type) qs.set("fuel_type", params.fuel_type);
  const query = qs.toString() ? `?${qs.toString()}` : "";
  return request<VehicleSpec[]>(`/api/v1/vehicle-specs${query}`, { token });
}

export async function getVehicleSpec(token: string, specId: string) {
  return request<VehicleSpec>(`/api/v1/vehicle-specs/${specId}`, { token });
}

export interface VehicleSpecCreate {
  make: string;
  model: string;
  year?: number;
  vehicle_type: string;
  fuel_type: string;
  fuel_efficiency_km_per_liter: number;
  co2_emissions_g_per_km?: number;
}

export async function createVehicleSpec(token: string, payload: VehicleSpecCreate) {
  return request<VehicleSpec>("/api/v1/vehicle-specs", { method: "POST", token, body: payload });
}

export async function updateVehicleSpec(
  token: string,
  specId: string,
  payload: Partial<VehicleSpecCreate>,
) {
  return request<VehicleSpec>(`/api/v1/vehicle-specs/${specId}`, { method: "PUT", token, body: payload });
}

export async function deleteVehicleSpec(token: string, specId: string) {
  return request<void>(`/api/v1/vehicle-specs/${specId}`, { method: "DELETE", token });
}

export async function searchEPAYears(token: string) {
  return request<EPAMenuEntry[]>("/api/v1/vehicle-specs/search/epa/years", { token });
}

export async function searchEPAMakes(token: string, year: number) {
  return request<EPAMenuEntry[]>(`/api/v1/vehicle-specs/search/epa/makes?year=${year}`, { token });
}

export async function searchEPAModels(token: string, year: number, make: string) {
  return request<EPAMenuEntry[]>(
    `/api/v1/vehicle-specs/search/epa/models?year=${year}&make=${encodeURIComponent(make)}`,
    { token },
  );
}

export async function searchEPA(
  token: string,
  params: { year?: number; make?: string; model?: string },
) {
  const qs = new URLSearchParams();
  if (params.year != null) qs.set("year", String(params.year));
  if (params.make) qs.set("make", params.make);
  if (params.model) qs.set("model", params.model);
  return request<EPASearchResult[]>(`/api/v1/vehicle-specs/search/epa?${qs.toString()}`, { token });
}

export async function importEPASpec(token: string, epaVehicleId: string) {
  return request<VehicleSpec>("/api/v1/vehicle-specs/import/epa", {
    method: "POST",
    token,
    body: { epa_vehicle_id: epaVehicleId },
  });
}
