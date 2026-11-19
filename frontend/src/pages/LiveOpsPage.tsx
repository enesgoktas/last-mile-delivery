import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "../state/AuthContext";
import { useToast } from "../state/ToastContext";
import { usePolling } from "../hooks/usePolling";
import { useCourierLocations } from "../hooks/useCourierLocations";
import { useRouteGeometries } from "../hooks/useRouteGeometries";
import { useLiveOpsExceptions } from "../hooks/useLiveOpsExceptions";
import { useOptimizationRunPreview } from "../hooks/useOptimizationRunPreview";
import { useAssign } from "../state/AssignContext";
import { CoordinateMap } from "../components/CoordinateMap";
import { ExceptionCenter } from "../components/ExceptionCenter";
import { LowBatteryWidget } from "../components/LowBatteryWidget";
import { Modal } from "../components/Modal";
import { StatusBadge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { StatCard } from "../components/ui/StatCard";
import {
  failStop,
  getAnalyticsSummary,
  listCouriers,
  listDepots,
  listRoutes,
  reorderStops,
  setDispatcherNote,
} from "../api/client";
import { RouteEditorPanel } from "../components/RouteEditorPanel";
import { courierAssignmentLabel } from "../utils/courierDisplay";
import type {
  Courier,
  CourierLocation,
  Depot,
  OptimizationPreviewChangedOrder,
  OptimizationPreviewReason,
  OptimizationPreviewRoute,
  OptimizationRun,
  OptimizationRunExplainability,
  OptimizationRunPreview,
  Order,
  Route as RouteModel,
} from "../types";

const POLL = 15_000;

function formatDate(): string {
  return new Date().toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
}

function todayDateStr(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function formatEconomicCost(value: number | undefined): string {
  return (value ?? 0).toLocaleString("en-US");
}

/* ── Reschedule modal ─────────────────────────────────────────────── */
function RescheduleModal({
  order,
  earliest,
  latest,
  saving,
  onEarliestChange,
  onLatestChange,
  onClose,
  onConfirm,
}: {
  order: Order | null;
  earliest: string;
  latest: string;
  saving: boolean;
  onEarliestChange: (v: string) => void;
  onLatestChange: (v: string) => void;
  onClose: () => void;
  onConfirm: () => void;
}) {
  if (!order) return null;
  return (
    <Modal open={!!order} onClose={onClose} title="Reschedule delivery">
      <div className="space-y-4">
        <div className="border-l-2 border-l-amber-400 pl-3 py-2">
          <p className="text-sm font-medium text-slate-900">{order.delivery_address.split(",")[0]}</p>
          <p className="text-xs text-slate-500 mt-0.5">
            {order.customer_email} · {order.attempt_count}/{order.max_attempts} attempts
          </p>
        </div>
        <label className="block text-xs font-medium text-slate-700">
          Earliest delivery
          <input
            type="datetime-local"
            value={earliest}
            onChange={(e) => onEarliestChange(e.target.value)}
            className="mt-1.5 w-full border border-slate-200 rounded-md px-3 py-2 text-sm text-slate-700 focus:outline-none focus:border-accent-500"
          />
        </label>
        <label className="block text-xs font-medium text-slate-700">
          Latest delivery
          <input
            type="datetime-local"
            value={latest}
            onChange={(e) => onLatestChange(e.target.value)}
            className="mt-1.5 w-full border border-slate-200 rounded-md px-3 py-2 text-sm text-slate-700 focus:outline-none focus:border-accent-500"
          />
        </label>
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={saving} onClick={onConfirm}>
            {saving ? "Rescheduling…" : "Confirm"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

/* ── Courier info popup ───────────────────────────────────────────── */
function CourierPopup({
  courier,
  location,
  routes,
  onViewRoute,
  onClose,
}: {
  courier: Courier;
  location: CourierLocation | undefined;
  routes: RouteModel[];
  onViewRoute: (id: string) => void;
  onClose: () => void;
}) {
  const activeRoute = routes.find((r) => r.courier_id === courier.id && r.status === "ACTIVE");
  const deliveryStops = activeRoute?.stops.filter(
    (s) =>
      s.stop_type !== "DEPOT_RETURN" &&
      s.stop_type !== "DEPOT_RELOAD" &&
      s.stop_type !== "CHARGING",
  ) ?? [];
  const completed = deliveryStops.filter((s) => s.status === "COMPLETED").length;
  const next = [...deliveryStops]
    .sort((a, b) => a.sequence_number - b.sequence_number)
    .find((s) => s.status === "PENDING");
  const lastUpdate = location?.updated_at
    ? new Date(location.updated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : null;

  return (
    <div className="absolute top-4 right-4 w-[280px] bg-white border border-slate-200 shadow-lg z-20">
      <div className="px-4 pt-3 pb-2 border-b border-slate-100 flex items-center justify-between">
        <div>
          <p className="text-xs font-semibold text-slate-900 font-mono">{courier.id.slice(0, 10)}</p>
          <p className="text-xs text-slate-400 mt-0.5">{courier.vehicle_type.replace("_", " ")}</p>
        </div>
        <button onClick={onClose} className="p-1 rounded text-slate-400 hover:text-slate-600">
          <span className="text-sm leading-none">×</span>
        </button>
      </div>
      <div className="px-4 py-3 space-y-2.5">
        <div className="flex items-center justify-between">
          <StatusBadge value={courier.availability_status} />
          {lastUpdate && <span className="text-xs text-slate-400 tabular-nums">Updated {lastUpdate}</span>}
        </div>
        {activeRoute ? (
          <div className="space-y-2">
            <div>
              <div className="flex justify-between text-xs text-slate-500 mb-1">
                <span>Progress</span>
                <span className="tabular-nums">{completed}/{deliveryStops.length}</span>
              </div>
              <div className="h-1 bg-slate-100 rounded-full overflow-hidden">
                <div
                  className="h-full bg-accent-500 transition-all duration-300"
                  style={{ width: deliveryStops.length > 0 ? `${(completed / deliveryStops.length) * 100}%` : "0%" }}
                />
              </div>
            </div>
            {next && (
              <div>
                <p className="text-[10px] uppercase font-medium text-slate-400 tracking-wide">Next stop</p>
                <p className="text-xs text-slate-700 mt-0.5 truncate">{next.address}</p>
                {next.planned_arrival && (
                  <p className="text-[10px] text-slate-400 tabular-nums mt-0.5">
                    ETA {new Date(next.planned_arrival).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                  </p>
                )}
              </div>
            )}
            <Button variant="secondary" size="sm" className="w-full justify-center" onClick={() => onViewRoute(activeRoute.id)}>
              Edit route
            </Button>
          </div>
        ) : (
          <p className="text-xs text-slate-400">No active route</p>
        )}
      </div>
    </div>
  );
}

/* ── Explainability panel ─────────────────────────────────────────── */
function ExplainPanel({
  summary,
  changedOrders,
  reasons,
  attentionItems,
}: {
  summary: string;
  changedOrders: OptimizationPreviewChangedOrder[];
  reasons: OptimizationPreviewReason[];
  attentionItems: OptimizationPreviewReason[];
}) {
  return (
    <div className="border-l-2 border-l-slate-300 pl-3 py-2 space-y-3">
      <p className="text-sm text-slate-700">{summary}</p>
      {changedOrders.length > 0 && (
        <div>
          <p className="text-[10px] uppercase tracking-wide font-medium text-slate-400 mb-1.5">Impacted orders</p>
          {changedOrders.map((c) => (
            <div key={`${c.order_id}-${c.change_type}`} className="mb-1.5">
              <p className="text-xs font-medium text-slate-800 truncate">{c.delivery_address}</p>
              <p className="text-[10px] text-slate-500 mt-0.5">{c.change_type.replace("_", " ").toLowerCase()}</p>
            </div>
          ))}
        </div>
      )}
      {reasons.length > 0 && (
        <div>
          <p className="text-[10px] uppercase tracking-wide font-medium text-slate-400 mb-1">Why</p>
          {reasons.map((r) => (
            <p key={r.code} className="text-xs text-slate-600 mb-0.5">· {r.message}</p>
          ))}
        </div>
      )}
      {attentionItems.length > 0 && (
        <div>
          <p className="text-[10px] uppercase tracking-wide font-medium text-amber-600 mb-1">Attention</p>
          {attentionItems.map((r) => (
            <p key={r.code} className="text-xs text-amber-700 mb-0.5">· {r.message}</p>
          ))}
        </div>
      )}
    </div>
  );
}

function toPreviewMapRoute(route: OptimizationPreviewRoute): RouteModel {
  return {
    id: route.route_id,
    courier_id: route.courier_id,
    status: "DRAFT",
    stops: route.stops.map((stop: OptimizationPreviewRoute["stops"][number]) => ({
      id: stop.stop_id,
      order_id: stop.order?.order_id ?? null,
      sequence_number: stop.sequence_number,
      stop_type: stop.stop_type,
      lat: stop.lat,
      lon: stop.lon,
      address: stop.address,
      planned_arrival: stop.planned_arrival,
      actual_arrival: null,
      status: "PENDING",
      failure_reason: null,
      failure_reason_detail: null,
      notes: null,
      dispatcher_note: null,
      customer_phone: null,
    })),
    started_at: null,
    completed_at: null,
    created_at: new Date().toISOString(),
    actual_distance_km: null,
    optimization_run_id: route.route_id,
    planned_distance_km: route.planned_distance_km,
  };
}

/* ── Optimization review overlay ─────────────────────────────────── */
function OptimizationOverlay({
  run,
  preview,
  previewError,
  previewLoading,
  onRetryPreview,
  couriers,
  depots,
  onApprove,
  onReject,
  onClose,
}: {
  run: OptimizationRun;
  preview: OptimizationRunPreview | null;
  previewError: string | null;
  previewLoading: boolean;
  onRetryPreview: () => void;
  couriers: Courier[];
  depots: Depot[];
  onApprove: () => void;
  onReject: () => void;
  onClose: () => void;
}) {
  const [showExplain, setShowExplain] = useState(false);
  const [expandedRouteId, setExpandedRouteId] = useState<string | null>(null);
  const draftRoutes = useMemo(() => preview?.routes ?? [], [preview]);
  const mapRoutes = useMemo(() => draftRoutes.map(toPreviewMapRoute), [draftRoutes]);
  const explainability: OptimizationRunExplainability | null = preview?.explainability ?? null;

  return (
    <div className="fixed inset-0 z-50 bg-black/40 fade-in flex items-stretch">
      <div className="m-4 flex-1 bg-white flex flex-col overflow-hidden shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200">
          <div>
            <h2 className="text-base font-semibold text-slate-900">Review proposed routes</h2>
            <p className="text-xs text-slate-500 mt-0.5 tabular-nums">
              {preview?.summary.total_routes ?? run.routes_created ?? 0} routes ·{" "}
              {preview?.summary.total_unassigned ?? run.unassigned_count ?? 0} unassigned
              {preview?.summary.cost_delta_vs_current && (
                <>
                  {" "}· delta {preview.summary.cost_delta_vs_current.total_generalized_cost >= 0 ? "+" : ""}
                  {formatEconomicCost(preview.summary.cost_delta_vs_current.total_generalized_cost)}
                </>
              )}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {explainability && (
              <Button variant="ghost" size="sm" onClick={() => setShowExplain((v) => !v)}>
                Why?
              </Button>
            )}
            <button onClick={onClose} className="p-1.5 rounded text-slate-400 hover:text-slate-600 hover:bg-slate-100">
              <span className="text-lg leading-none">×</span>
            </button>
          </div>
        </div>

        {showExplain && explainability && (
          <div className="px-6 py-4 border-b border-slate-100 bg-slate-50">
            <ExplainPanel
              summary={explainability.summary}
              changedOrders={explainability.changed_orders ?? []}
              reasons={explainability.reasons ?? []}
              attentionItems={explainability.attention_items ?? []}
            />
          </div>
        )}

        {/* Body */}
        <div className="flex-1 flex overflow-hidden">
          {/* Route list */}
          <div className="w-72 border-r border-slate-200 overflow-y-auto thin-scrollbar">
            {previewLoading ? (
              <div className="p-4 text-sm text-slate-400">Loading preview…</div>
            ) : previewError ? (
              <div className="p-4 space-y-2">
                <p className="text-sm text-red-600">{previewError}</p>
                <Button variant="secondary" size="sm" onClick={onRetryPreview}>Retry</Button>
              </div>
            ) : draftRoutes.length === 0 ? (
              <div className="p-4 text-sm text-slate-400">No draft routes.</div>
            ) : (
              draftRoutes.map((route) => {
                const expanded = expandedRouteId === route.route_id;
                const routeCourier = couriers.find((c) => c.id === route.courier_id);
                return (
                  <div key={route.route_id} className="border-b border-slate-100 px-4 py-3">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-sm font-medium text-slate-800">
                        {route.vehicle_type.replace("_", " ")}
                      </span>
                      <div className="flex items-center gap-2">
                        {route.explainability && (
                          <button
                            onClick={() => setExpandedRouteId(expanded ? null : route.route_id)}
                            className="text-xs text-slate-400 hover:text-slate-700 underline"
                          >
                            {expanded ? "Less" : "Why?"}
                          </button>
                        )}
                        <StatusBadge value="DRAFT" />
                      </div>
                    </div>
                    <p className="text-xs text-slate-600">{courierAssignmentLabel(routeCourier, route.courier_id)}</p>
                    <p className="text-xs text-slate-500 tabular-nums mt-0.5">
                      {route.stop_count} stops · {route.planned_distance_km.toFixed(1)} km · {route.total_weight_kg.toFixed(0)} kg
                    </p>
                    {route.cost_delta_vs_current && (
                      <p className="text-xs text-slate-400 tabular-nums mt-0.5">
                        delta {route.cost_delta_vs_current.total_generalized_cost >= 0 ? "+" : ""}
                        {formatEconomicCost(route.cost_delta_vs_current.total_generalized_cost)}
                      </p>
                    )}
                    {expanded && route.explainability && (
                      <div className="mt-2">
                        <ExplainPanel
                          summary={route.explainability.summary}
                          changedOrders={route.explainability.changed_orders ?? []}
                          reasons={route.explainability.reasons ?? []}
                          attentionItems={route.explainability.attention_items ?? []}
                        />
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>

          {/* Map */}
          <div className="flex-1 relative">
            <CoordinateMap
              couriers={couriers}
              routes={mapRoutes}
              depots={depots}
              liveCourierLocations={[]}
              routeRoadGeometries={new Map()}
            />
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-6 py-3 border-t border-slate-200">
          <Button variant="danger" onClick={onReject}>Reject all</Button>
          <Button variant="primary" onClick={onApprove}>Approve all routes</Button>
        </div>
      </div>
    </div>
  );
}

/* ── Activity item ────────────────────────────────────────────────── */
function ActivityItem({ text, time, type }: { text: string; time: string; type: "success" | "warning" | "error" | "info" }) {
  const color = { success: "bg-emerald-500", warning: "bg-amber-400", error: "bg-red-500", info: "bg-blue-400" }[type];
  return (
    <div className="flex items-start gap-2.5 py-2 border-b border-slate-50 last:border-0">
      <div className={`mt-1.5 w-1.5 h-1.5 rounded-full shrink-0 ${color}`} />
      <div className="flex-1 min-w-0">
        <p className="text-xs text-slate-600 leading-snug">{text}</p>
        <p className="text-[10px] text-slate-400 mt-0.5 tabular-nums">{time}</p>
      </div>
    </div>
  );
}

/* ── Main page ────────────────────────────────────────────────────── */
export function LiveOpsPage() {
  const { token } = useAuth();
  const { addToast } = useToast();

  const fetchSummary = useCallback(() => getAnalyticsSummary(token!), [token]);
  const fetchCouriers = useCallback(() => listCouriers(token!), [token]);
  const fetchRoutes = useCallback(() => {
    return listRoutes(token!, `status=PLANNED,ACTIVE&route_date=${todayDateStr()}`);
  }, [token]);
  const fetchDepots = useCallback(() => listDepots(token!), [token]);

  const { data: summary, isLoading: summaryLoading } = usePolling(fetchSummary, POLL);
  const { data: couriers, error: couriersError } = usePolling(fetchCouriers, POLL);
  const { data: routes, refetch: refetchRoutes, error: routesError } = usePolling(fetchRoutes, POLL);
  const { data: depots } = usePolling(fetchDepots, 60_000);

  const isInitialLoading = summaryLoading && !summary;
  const locations = useCourierLocations(token!, POLL);
  const routeRoadGeometries = useRouteGeometries(routes ?? [], token);
  const [showOverlay, setShowOverlay] = useState(false);
  const [editingRouteId, setEditingRouteId] = useState<string | null>(null);
  const [mapSelectedCourierId, setMapSelectedCourierId] = useState<string | null>(null);

  const {
    counts: exceptionCounts,
    criticalAlerts,
    error: exceptionsError,
    failedOrders,
    hasAttention,
    pendingRun,
    unassignedOrders,
    acknowledgingAlertId,
    assigningOrderId,
    dispatchingOrderId,
    pinningOrderId,
    cancellingOrderId,
    rescheduleTarget,
    rescheduleEarliest,
    rescheduleLatest,
    rescheduling,
    setRescheduleEarliest,
    setRescheduleLatest,
    openRescheduleModal,
    closeRescheduleModal,
    handleApprovePendingRun,
    handleRejectPendingRun,
    handleAcknowledgeAlert,
    handleAssignOrder,
    handleDispatchNow,
    handleSetPreferredCourier,
    handleCancelFailedOrder,
    handleConfirmReschedule,
  } = useLiveOpsExceptions({ token: token!, addToast, refetchRoutes });

  const {
    assign: assignOptimal,
    busy: assigningOptimally,
    phase: assignOptimalState,
    kind: assignOptimalKind,
    result: assignOptimalResult,
    error: assignOptimalError,
  } = useAssign();
  const prevAssignOptimalState = useRef(assignOptimalState);

  useEffect(() => {
    if (prevAssignOptimalState.current === assignOptimalState) return;
    prevAssignOptimalState.current = assignOptimalState;
    if (assignOptimalKind !== "day") return;
    if (assignOptimalState === "done" && assignOptimalResult) {
      const { routes_created, unassigned_count } = assignOptimalResult;
      const assigned = routes_created > 0 ? `${routes_created} route${routes_created !== 1 ? "s" : ""} created` : "No routes created";
      const unassigned = unassigned_count > 0 ? `, ${unassigned_count} order${unassigned_count !== 1 ? "s" : ""} could not be assigned` : "";
      addToast(`${assigned}${unassigned}`, routes_created > 0 ? "success" : "info");
      refetchRoutes();
    } else if (assignOptimalState === "error" && assignOptimalError) {
      addToast(assignOptimalError, "error");
    }
  }, [assignOptimalState, assignOptimalKind, assignOptimalResult, assignOptimalError, addToast, refetchRoutes]);

  const {
    preview: pendingRunPreview,
    isLoading: pendingRunPreviewLoading,
    error: pendingRunPreviewError,
    refetch: refetchPendingRunPreview,
  } = useOptimizationRunPreview({
    token: token!,
    runId: showOverlay && pendingRun ? pendingRun.id : null,
    enabled: showOverlay && !!pendingRun,
  });

  const pollError = routesError ?? couriersError ?? exceptionsError ?? null;

  const mapSelectedCourier = useMemo(
    () => (couriers ?? []).find((c) => c.id === mapSelectedCourierId) ?? null,
    [couriers, mapSelectedCourierId],
  );
  const mapSelectedLocation = useMemo(
    () => locations.find((l) => l.courier_id === mapSelectedCourierId),
    [locations, mapSelectedCourierId],
  );
  const editingRoute = useMemo(
    () => (routes ?? []).find((r) => r.id === editingRouteId) ?? null,
    [routes, editingRouteId],
  );
  const editingRouteCourier = useMemo(
    () =>
      editingRoute ? (couriers ?? []).find((c) => c.id === editingRoute.courier_id) ?? null : null,
    [couriers, editingRoute],
  );

  const activeRoutes = useMemo(() => (routes ?? []).filter((r) => r.status === "ACTIVE").length, [routes]);
  const onlineCouriers = useMemo(() => (couriers ?? []).filter((c) => c.availability_status === "ONLINE").length, [couriers]);
  const unassignedCount = exceptionCounts.unassigned_orders;
  const failedCount = exceptionCounts.failed_orders;

  const activeCriticalAlerts = useMemo(
    () => criticalAlerts.filter((a) => a.status === "ACTIVE"),
    [criticalAlerts],
  );

  const activityItems = useMemo(() => {
    const items: { text: string; time: string; type: "success" | "warning" | "error" | "info"; ts: number }[] = [];
    for (const order of failedOrders) {
      if (order.failed_at) {
        items.push({
          text: `${order.delivery_address.split(",")[0]} — failed`,
          time: new Date(order.failed_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
          type: "error",
          ts: new Date(order.failed_at).getTime(),
        });
      }
    }
    for (const route of routes ?? []) {
      if (route.status === "ACTIVE" && route.started_at) {
        const oc = (couriers ?? []).find((c) => c.id === route.courier_id);
        items.push({
          text: `${courierAssignmentLabel(oc, route.courier_id)} — route ${route.id.slice(0, 8)} · ${route.stops.length} stops`,
          time: new Date(route.started_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
          type: "info",
          ts: new Date(route.started_at).getTime(),
        });
      }
    }
    items.sort((a, b) => b.ts - a.ts);
    return items.slice(0, 30);
  }, [failedOrders, routes, couriers]);

  async function handleReorderStops(routeId: string, stopIds: string[]) {
    try {
      await reorderStops(token!, routeId, stopIds);
      addToast("Stop order updated", "success");
      refetchRoutes();
    } catch (err) {
      addToast(err instanceof Error ? err.message : "Reorder failed", "error");
      throw err;
    }
  }

  async function handleSetDispatcherNote(routeId: string, stopId: string, note: string | null) {
    try {
      await setDispatcherNote(token!, routeId, stopId, note);
      addToast(note ? "Note saved" : "Note cleared", "success");
      refetchRoutes();
    } catch (err) {
      addToast(err instanceof Error ? err.message : "Failed to save note", "error");
      throw err;
    }
  }

  async function handleFailStop(
    routeId: string,
    stopId: string,
    failureReason: string,
    notes: string | null,
  ) {
    try {
      await failStop(token!, routeId, stopId, {
        failure_reason: failureReason,
        notes,
      });
      addToast("Stop marked as failed", "success");
      refetchRoutes();
    } catch (err) {
      addToast(err instanceof Error ? err.message : "Failed to mark stop", "error");
      throw err;
    }
  }

  return (
    <>
      <div className="flex-1 flex overflow-hidden">
        {/* Left panel */}
        <div className="w-[380px] shrink-0 border-r border-slate-200 bg-white flex flex-col overflow-hidden">
          {/* Error banner */}
          {pollError && (
            <div className="px-4 py-2 bg-red-50 border-b border-red-100">
              <p className="text-xs text-red-600">Live data unavailable — showing last known values.</p>
            </div>
          )}

          {/* Stats */}
          <div className="px-4 pt-4 pb-3 border-b border-slate-100">
            <p className="text-xs text-slate-400 mb-3">{formatDate()}</p>
            {isInitialLoading ? (
              <div className="grid grid-cols-2 gap-x-5 gap-y-3">
                {[...Array(4)].map((_, i) => (
                  <div key={i} className="space-y-1.5 min-w-0">
                    <div className="h-6 w-14 bg-slate-100 rounded animate-pulse" />
                    <div className="h-2.5 w-16 bg-slate-100 rounded animate-pulse" />
                  </div>
                ))}
              </div>
            ) : (
              <div data-testid="stats-grid" className="grid grid-cols-2 gap-x-5 gap-y-3">
                <StatCard value={summary?.total_orders ?? "—"} label="Total" emphasis />
                <StatCard value={summary?.delivered_orders ?? "—"} label="Delivered" />
                <StatCard value={summary?.failed_orders ?? "—"} label="Failed" alert={failedCount > 0} />
                <StatCard value={unassignedCount} label="Unassigned" alert={unassignedCount > 0} />
              </div>
            )}
            <p className="text-xs text-slate-400 mt-2.5 tabular-nums">
              {activeRoutes} routes active · {onlineCouriers} couriers online
            </p>
          </div>

          {/* Low-battery alerts (renders only when ev_low_battery_alerts_enabled). */}
          <div className="px-4 pt-3">
            <LowBatteryWidget />
          </div>

          <div className="flex-1 min-h-0 overflow-y-auto thin-scrollbar">
            {/* Exception center */}
            {hasAttention && (
              <ExceptionCenter
                counts={exceptionCounts}
                pendingRun={pendingRun ?? null}
                criticalAlerts={activeCriticalAlerts}
                failedOrders={failedOrders}
                pendingOrders={unassignedOrders}
                couriers={couriers ?? []}
                assigningOptimally={assigningOptimally}
                acknowledgingAlertId={acknowledgingAlertId}
                assigningOrderId={assigningOrderId}
                dispatchingOrderId={dispatchingOrderId}
                pinningOrderId={pinningOrderId}
                cancellingOrderId={cancellingOrderId}
                onReviewPendingRun={() => setShowOverlay(true)}
                onAssignOptimally={() => {
                  void assignOptimal(todayDateStr());
                }}
                onOpenRoute={(routeId) => setEditingRouteId(routeId)}
                onAcknowledgeAlert={handleAcknowledgeAlert}
                onAssignOrder={handleAssignOrder}
                onDispatchNow={handleDispatchNow}
                onSetPreferredCourier={handleSetPreferredCourier}
                onRescheduleOrder={openRescheduleModal}
                onCancelOrder={handleCancelFailedOrder}
              />
            )}

            {/* Routes */}
            {(routes ?? []).filter((r) => r.status === "ACTIVE" || r.status === "PLANNED").length > 0 && (
              <div className="border-b border-slate-100">
                <div className="px-4 pt-3 pb-1">
                  <span className="text-xs font-medium uppercase tracking-wide text-slate-400">Routes</span>
                </div>
                <div className="overflow-y-auto thin-scrollbar max-h-44">
                  {(routes ?? [])
                    .filter((r) => r.status === "ACTIVE" || r.status === "PLANNED")
                    .map((r) => {
                      const nonDepot = r.stops.filter(
                        (s) =>
                          s.stop_type !== "DEPOT_RETURN" &&
                          s.stop_type !== "DEPOT_RELOAD" &&
                          s.stop_type !== "CHARGING",
                      );
                      const completed = nonDepot.filter((s) => s.status === "COMPLETED").length;
                      const isSelected = editingRouteId === r.id;
                      const routeCourier = (couriers ?? []).find((c) => c.id === r.courier_id);
                      return (
                        <button
                          key={r.id}
                          onClick={() => setEditingRouteId(isSelected ? null : r.id)}
                          className={`w-full text-left px-4 py-2.5 border-b border-slate-50 flex items-center justify-between transition-colors duration-100 ${
                            isSelected ? "bg-orange-50" : "hover:bg-slate-50"
                          }`}
                        >
                          <div className="min-w-0 flex-1 pr-2">
                            <span className="text-xs font-mono text-slate-700">{r.id.slice(0, 8)}</span>
                            <p className="text-xs text-slate-600 mt-0.5 leading-5 break-words">
                              {courierAssignmentLabel(routeCourier, r.courier_id)}
                            </p>
                            <p className="text-xs text-slate-400 tabular-nums mt-0.5 leading-5">
                              {completed}/{nonDepot.length} stops · {r.planned_distance_km.toFixed(1)} km
                            </p>
                          </div>
                          <div className="shrink-0">
                            <StatusBadge value={r.status} />
                          </div>
                        </button>
                      );
                    })}
                </div>
              </div>
            )}

            {/* Activity feed */}
            <div className="px-4 py-3">
              <span className="text-xs font-medium uppercase tracking-wide text-slate-400">Activity</span>
              <div className="mt-2">
                {activityItems.length === 0 ? (
                  <p className="text-xs text-slate-400 py-4">No activity yet today.</p>
                ) : (
                  activityItems.map((item, idx) => (
                    <ActivityItem key={idx} text={item.text} time={item.time} type={item.type} />
                  ))
                )}
              </div>
            </div>
          </div>

          {/* Bottom strip */}
          <div className="px-4 py-2.5 border-t border-slate-100 bg-slate-50/50 flex justify-between text-xs text-slate-500 tabular-nums">
            <span>{summary?.delivered_orders ?? 0} delivered</span>
            <span>{activeRoutes} in progress</span>
            <span className={failedCount > 0 ? "text-red-500 font-medium" : ""}>{failedCount} failed</span>
          </div>
        </div>

        {/* Map */}
        <div className="flex-1 relative">
          <CoordinateMap
            couriers={couriers ?? []}
            routes={routes ?? []}
            depots={depots ?? []}
            liveCourierLocations={locations}
            routeRoadGeometries={routeRoadGeometries}
            onCourierClick={(id) => setMapSelectedCourierId(mapSelectedCourierId === id ? null : id)}
          />
          {mapSelectedCourier && (
            <CourierPopup
              courier={mapSelectedCourier}
              location={mapSelectedLocation}
              routes={routes ?? []}
              onViewRoute={(routeId) => {
                setMapSelectedCourierId(null);
                setEditingRouteId(routeId);
              }}
              onClose={() => setMapSelectedCourierId(null)}
            />
          )}
        </div>

        {/* Route editor */}
        {editingRoute && (
          <RouteEditorPanel
            key={editingRoute.id}
            route={editingRoute}
            courier={editingRouteCourier}
            pendingOrders={unassignedOrders}
            onReorder={handleReorderStops}
            onSetDispatcherNote={handleSetDispatcherNote}
            onAssignPendingOrder={handleAssignOrder}
            onSetPreferredCourier={handleSetPreferredCourier}
            onFailStop={handleFailStop}
            onClose={() => setEditingRouteId(null)}
          />
        )}
      </div>

      {/* Optimization overlay */}
      {showOverlay && pendingRun && (
        <OptimizationOverlay
          run={pendingRun}
          preview={pendingRunPreview}
          previewLoading={pendingRunPreviewLoading}
          previewError={pendingRunPreviewError}
          onRetryPreview={() => void refetchPendingRunPreview()}
          couriers={couriers ?? []}
          depots={depots ?? []}
          onApprove={async () => {
            const approved = await handleApprovePendingRun();
            if (approved) setShowOverlay(false);
          }}
          onReject={async () => {
            const rejected = await handleRejectPendingRun();
            if (rejected) setShowOverlay(false);
          }}
          onClose={() => setShowOverlay(false)}
        />
      )}

      <RescheduleModal
        order={rescheduleTarget}
        earliest={rescheduleEarliest}
        latest={rescheduleLatest}
        saving={rescheduling}
        onEarliestChange={setRescheduleEarliest}
        onLatestChange={setRescheduleLatest}
        onClose={closeRescheduleModal}
        onConfirm={handleConfirmReschedule}
      />
    </>
  );
}
