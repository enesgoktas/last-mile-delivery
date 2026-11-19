import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, ChevronLeft, ChevronRight, Download, GitCompare, Loader2, Search, Upload, Plus, Zap, RotateCcw, CalendarRange, X } from "lucide-react";
import { useAssign } from "../state/AssignContext";
import { useAuth } from "../state/AuthContext";
import { useToast } from "../state/ToastContext";
import { usePolling } from "../hooks/usePolling";
import { useRouteGeometries } from "../hooks/useRouteGeometries";
import {
  downloadOrdersCsv,
  listOrdersPaginated,
  createOrder,
  createOrderReturn,
  getOrderLegs,
  importOrdersPreview,
  importOrdersPublish,
  getOrder,
  getOrderHistory,
  assignOrder,
  cancelOrder,
  rescheduleOrder,
  listCouriers,
  listOrders,
  listDepots,
  listOrderPackages,
  getPackageEvents,
  getOperationsPolicy,
  triggerUnassign,
  dispatchOrderNow,
  listVehicleTags,
  createRouteScenarioComparison,
  getRouteScenarioComparison,
  getRouteScenarioPreview,
  selectRouteScenario,
  deleteRouteScenarioComparison,
} from "../api/client";
import { canRunDispatcherActions } from "../config/roles";
import { courierAssignmentLabel } from "../utils/courierDisplay";
import { CoordinateMap } from "../components/CoordinateMap";
import { StatusBadge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Tabs } from "../components/ui/Tabs";
import { OrderForensicsDrawer } from "../components/OrderForensicsDrawer";
import { SlideOver } from "../components/ui/SlideOver";
import { Modal } from "../components/Modal";
import { Pagination } from "../components/Pagination";
import { AddressAutocomplete } from "../components/AddressAutocomplete";
import { ConfirmDialog } from "../components/ConfirmDialog";
import type {
  CustomOrderFieldDefinition,
  Courier,
  Depot,
  InventoryEvent,
  OptimizationPreviewRoute,
  OptimizationRunPreview,
  OperationsPolicy,
  Order,
  OrderCreate,
  OrderLeg,
  OrderPackage,
  OrderPriority,
  OrderStatusHistoryItem,
  OrderType,
  Route as RouteModel,
  RouteScenario,
  RouteScenarioComparison,
  UserRole,
  VisibilityMode,
} from "../types";
import type { GeocodeResult } from "../api/client";

const POLL = 30_000;
const PAGE_SIZE = 25;
const HUMAN_DISPATCHER_BASELINE_KEY = "human_dispatcher_baseline";

const STATUS_TABS = [
  { id: "",            label: "All" },
  { id: "PENDING",     label: "Pending" },
  { id: "ASSIGNED",    label: "Assigned" },
  { id: "IN_TRANSIT",  label: "In Transit" },
  { id: "DELIVERED",   label: "Delivered" },
  { id: "FAILED",      label: "Failed" },
  { id: "EXPIRED",     label: "Expired" },
  { id: "CANCELLED",   label: "Cancelled" },
];

type CargoPreset = { id: string; label: string; description: string; length_cm: number; width_cm: number; height_cm: number; weight_kg: number };
const CARGO_PRESETS: CargoPreset[] = [
  { id: "envelope",     label: "Envelope",     description: "Documents",                length_cm: 35,  width_cm: 25, height_cm: 3,   weight_kg: 0.5 },
  { id: "small_parcel", label: "Small Parcel", description: "Books, clothing",          length_cm: 30,  width_cm: 20, height_cm: 15,  weight_kg: 2 },
  { id: "medium_box",   label: "Medium Box",   description: "Standard box",             length_cm: 40,  width_cm: 30, height_cm: 25,  weight_kg: 5 },
  { id: "large_box",    label: "Large Box",    description: "Appliances",               length_cm: 60,  width_cm: 40, height_cm: 30,  weight_kg: 15 },
  { id: "xl_crate",     label: "XL Crate",     description: "Furniture",                length_cm: 80,  width_cm: 60, height_cm: 50,  weight_kg: 30 },
  { id: "pallet",       label: "Pallet",       description: "EUR 120×80×144",           length_cm: 120, width_cm: 80, height_cm: 144, weight_kg: 500 },
];

const emptyForm: OrderCreate = {
  type: "ON_DEMAND", priority: "NORMAL", pickup_address: "", pickup_lat: 0, pickup_lon: 0,
  delivery_address: "", delivery_lat: 0, delivery_lon: 0, earliest_delivery: "", latest_delivery: "",
  customer_email: "", custom_fields: {},
};

type StandardFieldName = keyof OperationsPolicy["order_form"]["standard_fields"];
const STANDARD_FIELD_DEFAULTS: Record<StandardFieldName, VisibilityMode> = {
  customer_phone: "OPTIONAL", notes: "OPTIONAL", external_ref: "OPTIONAL",
  depot_id: "OPTIONAL", is_fragile: "OPTIONAL", is_temp_sensitive: "OPTIONAL",
};

function getStandardFieldMode(policy: OperationsPolicy | null, fieldName: StandardFieldName): VisibilityMode {
  return policy?.order_form.standard_fields[fieldName] ?? STANDARD_FIELD_DEFAULTS[fieldName];
}
function isFieldVisible(mode: VisibilityMode) { return mode !== "HIDDEN"; }
function isFieldRequired(mode: VisibilityMode) { return mode === "REQUIRED"; }
function getApplicableCustomFields(policy: OperationsPolicy | null, orderType: OrderType): CustomOrderFieldDefinition[] {
  if (!policy) return [];
  return policy.order_form.custom_fields.filter(
    // The contract OrderType (custom-fields side) is a narrower union and
    // doesn't yet include RIDE; cast keeps the runtime check intact while
    // satisfying the type checker.
    (f: CustomOrderFieldDefinition) =>
      f.applies_to_order_types.length === 0 ||
      (f.applies_to_order_types as unknown as OrderType[]).includes(orderType),
  );
}
function formatCustomFieldValue(value: unknown): string {
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (value == null) return "—";
  return String(value);
}
function getRolePermissions(policy: OperationsPolicy | null, role: UserRole | null) {
  if (!policy || !role) return null;
  // SUPERADMIN is a platform role and has no per-tenant permission row.
  if (role === "SUPERADMIN") return null;
  return policy.team_permissions[role];
}
function nowLocal(): string {
  const d = new Date(), pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function localStringToDate(s: string): Date {
  const [dp, tp] = s.split("T");
  const [y, m, day] = dp.split("-").map(Number);
  const [h, min] = (tp ?? "00:00").split(":").map(Number);
  return new Date(y, m - 1, day, h, min);
}
function isScheduledFuture(s: string) { return localStringToDate(s).getTime() - Date.now() > 36 * 3600 * 1000; }
function daysUntil(s: string) {
  const ms = localStringToDate(s).getTime() - Date.now();
  const d = Math.floor(ms / 86400000), h = Math.floor((ms % 86400000) / 3600000);
  if (d === 0) return `${h}h`; if (h === 0) return `${d}d`; return `${d}d ${h}h`;
}
function todayDateStr(): string {
  const d = new Date(), pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
function getErrorStatus(error: unknown): number | null {
  if (typeof error === "object" && error !== null && "status" in error) {
    const status = (error as { status?: unknown }).status;
    return typeof status === "number" ? status : null;
  }
  return null;
}

function FieldRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between py-2 border-b border-slate-50 last:border-0">
      <span className="text-xs text-slate-500">{label}</span>
      <span className="text-xs font-medium text-slate-900 text-right max-w-[60%]">{value}</span>
    </div>
  );
}

function formatMetric(value: number | undefined | null, suffix = ""): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value.toLocaleString(undefined, { maximumFractionDigits: value >= 100 ? 0 : 1 })}${suffix}`;
}

function formatDelta(value: number | undefined | null, suffix = ""): string {
  if (value == null || Number.isNaN(value) || value === 0) return "0" + suffix;
  return `${value > 0 ? "+" : ""}${formatMetric(value, suffix)}`;
}

function baselineScenario(comparison: RouteScenarioComparison | null): RouteScenario | null {
  if (!comparison) return null;
  return comparison.scenarios.find(
    (scenario) => scenario.key === HUMAN_DISPATCHER_BASELINE_KEY,
  ) ?? comparison.scenarios[0] ?? null;
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

function ScenarioStatusIcon({ status }: { status: string }) {
  if (status === "SUCCEEDED" || status === "SELECTED") return <CheckCircle2 size={14} className="text-emerald-500" />;
  if (status === "FAILED") return <AlertTriangle size={14} className="text-red-500" />;
  return <Loader2 size={14} className="text-slate-400 animate-spin" />;
}

function RouteScenarioComparisonOverlay({
  comparison,
  loading,
  error,
  selectedScenarioId,
  preview,
  previewLoading,
  previewError,
  token,
  couriers,
  depots,
  selecting,
  discarding,
  onClose,
  onDiscard,
  onRetry,
  onSelectScenario,
  onUsePlan,
}: {
  comparison: RouteScenarioComparison | null;
  loading: boolean;
  error: string | null;
  selectedScenarioId: string | null;
  preview: OptimizationRunPreview | null;
  previewLoading: boolean;
  previewError: string | null;
  token: string | null;
  couriers: Courier[];
  depots: Depot[];
  selecting: boolean;
  discarding: boolean;
  onClose: () => void;
  onDiscard: () => void;
  onRetry: () => void;
  onSelectScenario: (scenarioId: string) => void;
  onUsePlan: () => void;
}) {
  const selectedScenario = comparison?.scenarios.find((scenario) => scenario.id === selectedScenarioId) ?? null;
  const baseline = baselineScenario(comparison);
  const mapRoutes = useMemo(() => (preview?.routes ?? []).map(toPreviewMapRoute), [preview]);
  const routeRoadGeometries = useRouteGeometries(mapRoutes, token);
  const canUsePlan = !!selectedScenario && selectedScenario.status === "SUCCEEDED" && !selecting;

  return (
    <div className="fixed inset-0 z-40 bg-slate-950/45 backdrop-blur-sm p-4">
      <div className="h-full bg-white shadow-2xl border border-slate-200 flex flex-col overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-200 flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-slate-900">Route comparison</h2>
            <p className="text-xs text-slate-500 tabular-nums mt-0.5">
              {comparison?.target_date ?? "Selected day"} · {comparison?.status ?? "STARTING"}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="secondary" size="sm" onClick={onDiscard} disabled={!comparison || discarding || selecting}>
              {discarding ? "Discarding…" : "Discard"}
            </Button>
            <Button variant="primary" size="sm" onClick={onUsePlan} disabled={!canUsePlan}>
              {selecting ? "Applying…" : "Use this plan"}
            </Button>
            <button onClick={onClose} className="p-1.5 rounded text-slate-400 hover:text-slate-700 hover:bg-slate-100">
              <X size={17} />
            </button>
          </div>
        </div>

        {error ? (
          <div className="mx-5 mt-4 border-l-2 border-l-red-500 pl-3 py-2 text-sm text-red-700">
            {error}
            <button onClick={onRetry} className="ml-3 text-xs text-red-600 underline">Retry</button>
          </div>
        ) : null}

        <div className="px-5 py-4 border-b border-slate-100 grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-5 gap-3">
          {(comparison?.scenarios ?? []).map((scenario) => {
            const summary = scenario.summary;
            const active = scenario.id === selectedScenarioId;
            const baseSummary = baseline?.summary;
            return (
              <button
                key={scenario.id}
                type="button"
                onClick={() => onSelectScenario(scenario.id)}
                className={[
                  "text-left border px-4 py-3 transition-colors min-h-[156px]",
                  active ? "border-accent-400 bg-accent-50/50" : "border-slate-200 bg-white hover:bg-slate-50",
                ].join(" ")}
              >
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="text-sm font-semibold text-slate-900">{scenario.label}</p>
                    <p className="text-[10px] uppercase tracking-wide text-slate-400 mt-0.5">{scenario.status}</p>
                    <span className="font-mono text-[9px] uppercase tracking-wider text-slate-300 mt-0.5 block">{scenario.solver_kind}</span>
                  </div>
                  <ScenarioStatusIcon status={scenario.status} />
                </div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 mt-3 text-xs">
                  <span className="text-slate-400">Cost</span>
                  <span className="text-right font-medium text-slate-800">{formatMetric(summary?.realized_energy_cost_try, " TRY")}</span>
                  <span className="text-slate-400">Distance</span>
                  <span className="text-right font-medium text-slate-800">{formatMetric(summary?.distance_km, " km")}</span>
                  <span className="text-slate-400">Time</span>
                  <span className="text-right font-medium text-slate-800">{formatMetric(summary?.duration_minutes, " min")}</span>
                  <span className="text-slate-400">Vehicles</span>
                  <span className="text-right font-medium text-slate-800">{summary?.vehicles_used ?? "—"}</span>
                </div>
                {summary && baseSummary && scenario.id !== baseline?.id ? (
                  <p className="mt-2 text-[10px] text-slate-500 tabular-nums">
                    vs baseline {formatDelta(summary.realized_energy_cost_try - baseSummary.realized_energy_cost_try, " TRY")} ·{" "}
                    {formatDelta(summary.duration_minutes - baseSummary.duration_minutes, " min")}
                  </p>
                ) : null}
              </button>
            );
          })}
          {loading && !comparison ? (
            <div className="col-span-full py-8 text-sm text-slate-400 flex items-center justify-center gap-2">
              <Loader2 size={15} className="animate-spin" />
              Starting comparison…
            </div>
          ) : null}
        </div>

        <div className="flex-1 min-h-0 flex overflow-hidden">
          <div className="w-[360px] border-r border-slate-200 overflow-y-auto thin-scrollbar">
            {selectedScenario ? (
              <div className="p-4 space-y-4">
                <div>
                  <p className="text-sm font-semibold text-slate-900">{selectedScenario.label}</p>
                  <p className="text-xs text-slate-500 mt-1">
                    {selectedScenario.summary?.assigned_count ?? 0} assigned · {selectedScenario.summary?.unassigned_count ?? 0} unassigned ·{" "}
                    {formatMetric((selectedScenario.summary?.on_time_rate ?? 0) * 100, "%")} on time
                  </p>
                </div>

                <div className="grid grid-cols-2 gap-2">
                  <FieldRow label="Fuel" value={`${formatMetric(selectedScenario.summary?.fuel_liters, " L")}`} />
                  <FieldRow label="Electric" value={`${formatMetric(selectedScenario.summary?.electric_kwh, " kWh")}`} />
                  <FieldRow label="Late" value={`${selectedScenario.summary?.late_minutes ?? 0} min`} />
                  <FieldRow label="CO2" value={selectedScenario.summary?.co2_kg == null ? "—" : `${selectedScenario.summary.co2_kg} kg`} />
                </div>

                {(selectedScenario.summary?.energy_price_sources ?? []).length > 0 && (
                  <div>
                    <p className="text-[10px] uppercase tracking-wide font-medium text-slate-400 mb-2">Energy prices</p>
                    <div className="space-y-1.5">
                      {selectedScenario.summary!.energy_price_sources.slice(0, 6).map((source) => (
                        <a
                          key={`${source.fuel_type}-${source.source_name}-${source.province_code ?? "TR"}`}
                          href={source.source_url || undefined}
                          target="_blank"
                          rel="noreferrer"
                          className="block rounded-md border border-slate-200 px-2.5 py-2 text-xs text-slate-600 hover:bg-slate-50"
                        >
                          <span className="font-medium text-slate-800">{source.fuel_type}</span>{" "}
                          {formatMetric(source.price_try, ` TRY/${source.unit.toLowerCase()}`)}
                          <span className="block text-[10px] text-slate-400 mt-0.5">
                            {source.source_name} · {source.freshness_status}
                          </span>
                        </a>
                      ))}
                    </div>
                  </div>
                )}

                {selectedScenario.summary?.alns_telemetry && (
                  <div>
                    <p className="text-[10px] uppercase tracking-wide font-medium text-slate-400 mb-2">Solver telemetry</p>
                    <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 space-y-1">
                      {(["total_iterations","accepted_count","improving_count","feasibility_ratio","route_pool_size"] as const).map((key) => {
                        const val = selectedScenario.summary!.alns_telemetry![key];
                        if (val == null) return null;
                        const label = key.replace(/_/g, " ");
                        const display = typeof val === "number" && key === "feasibility_ratio"
                          ? `${(val as number * 100).toFixed(1)}%`
                          : String(val);
                        return (
                          <div key={key} className="flex justify-between text-xs">
                            <span className="text-slate-500 capitalize">{label}</span>
                            <span className="font-mono text-slate-800 tabular-nums">{display}</span>
                          </div>
                        );
                      })}
                      {selectedScenario.summary.alns_telemetry.fallback_invoked != null && (
                        <div className="flex justify-between text-xs">
                          <span className="text-slate-500">fallback invoked</span>
                          <span className={`font-mono tabular-nums ${selectedScenario.summary.alns_telemetry.fallback_invoked ? "text-amber-600" : "text-emerald-600"}`}>
                            {String(selectedScenario.summary.alns_telemetry.fallback_invoked)}
                          </span>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {previewLoading ? (
                  <div className="text-sm text-slate-400 flex items-center gap-2">
                    <Loader2 size={14} className="animate-spin" />
                    Loading routes…
                  </div>
                ) : previewError ? (
                  <div className="border-l-2 border-l-red-500 pl-3 py-2 text-sm text-red-700">{previewError}</div>
                ) : (
                  <div>
                    <p className="text-[10px] uppercase tracking-wide font-medium text-slate-400 mb-2">Routes</p>
                    {(preview?.routes ?? []).length === 0 ? (
                      <p className="text-sm text-slate-400">No draft routes.</p>
                    ) : (
                      <div className="space-y-2">
                        {preview!.routes.map((route) => {
                          const courier = couriers.find((item) => item.id === route.courier_id);
                          return (
                            <div key={route.route_id} className="rounded-md border border-slate-200 px-3 py-2">
                              <div className="flex items-center justify-between">
                                <p className="text-xs font-semibold text-slate-800">{courierAssignmentLabel(courier, route.courier_id)}</p>
                                <StatusBadge value="DRAFT" />
                              </div>
                              <p className="text-xs text-slate-500 tabular-nums mt-1">
                                {route.stop_count} stops · {route.planned_distance_km.toFixed(1)} km · {route.order_count} orders
                              </p>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ) : (
              <div className="p-4 text-sm text-slate-400">Select a scenario.</div>
            )}
          </div>
          <div className="flex-1 relative bg-slate-50">
            <CoordinateMap
              couriers={couriers}
              depots={depots}
              routes={mapRoutes}
              liveCourierLocations={[]}
              routeRoadGeometries={routeRoadGeometries}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

const inputCls = "w-full border border-slate-300 rounded-md px-3 py-2 text-sm text-slate-800 focus:outline-none focus:border-accent-500 focus:ring-1 focus:ring-accent-500/20";
const labelCls = "block text-xs font-medium text-slate-700 mb-1";

export function OrdersPage() {
  const { token, role } = useAuth();
  const { addToast } = useToast();

  const [statusFilter, setStatusFilter] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedDate, setSelectedDate] = useState(todayDateStr());
  const [page, setPage] = useState(0);

  const {
    assign: assignOptimal,
    assignRange,
    previewRange,
    busy: assignBusy,
    phase: assignState,
    kind: assignKind,
    result: assignResult,
    progress: assignProgress,
    error: assignError,
    draft: assignDraft,
  } = useAssign();
  const prevAssignState = useRef(assignState);

  const [unassigning, setUnassigning] = useState(false);
  const [exporting, setExporting] = useState(false);

  // Server-side filter for /analytics/export only supports date range today;
  // we send the day picker so users at least scope the download to the same
  // day they're looking at. Status/search filters are not propagated yet —
  // surfaced in the toast message so the operator isn't surprised.
  const handleExportCsv = useCallback(async () => {
    if (!token || exporting) return;
    try {
      setExporting(true);
      const dayStart = `${selectedDate}T00:00:00`;
      const dayEnd = `${selectedDate}T23:59:59`;
      const { blob, filename } = await downloadOrdersCsv(token, {
        date_from: dayStart,
        date_to: dayEnd,
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      addToast(
        `Exported orders for ${selectedDate}. Status and search filters are ignored — the CSV contains every order created that day.`,
        "success",
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      addToast(`Export failed: ${msg}`, "error");
    } finally {
      setExporting(false);
    }
  }, [token, exporting, selectedDate, addToast]);

  useEffect(() => {
    const t = setTimeout(() => { setSearchTerm(searchInput); setPage(0); }, 350);
    return () => clearTimeout(t);
  }, [searchInput]);

  const isSearching = searchTerm.length > 0;

  const query = useMemo(() => {
    const parts: string[] = [];
    if (statusFilter) parts.push(`status=${statusFilter}`);
    if (searchTerm) { parts.push(`search=${encodeURIComponent(searchTerm)}`); }
    else if (selectedDate) {
      parts.push(`service_date=${encodeURIComponent(selectedDate)}`);
    }
    parts.push(`skip=${page * PAGE_SIZE}`, `limit=${PAGE_SIZE}`);
    return parts.join("&");
  }, [statusFilter, searchTerm, selectedDate, page]);

  const fetchOrders = useCallback(() => listOrdersPaginated(token!, query), [token, query]);
  const { data: result, isLoading, error: ordersError, refetch } = usePolling(fetchOrders, POLL);
  const orders = result?.data ?? null;
  const total = result?.total ?? 0;

  useEffect(() => {
    if (prevAssignState.current === assignState) return;
    prevAssignState.current = assignState;
    if (assignState === "done" && assignResult) {
      const { routes_created, unassigned_count } = assignResult;
      const assigned = routes_created > 0
        ? `${routes_created} route${routes_created !== 1 ? "s" : ""} created`
        : "No routes created";
      const unassigned = unassigned_count > 0
        ? `, ${unassigned_count} order${unassigned_count !== 1 ? "s" : ""} could not be assigned`
        : "";
      if (assignKind === "range") {
        addToast(
          `Range plan ready: ${assignResult.assigned_count ?? 0} assigned across ${assignResult.span_days ?? "selected"} day${
            assignResult.span_days === 1 ? "" : "s"
          }${unassigned}`,
          routes_created > 0 ? "success" : "info",
        );
      } else {
        addToast(`${assigned}${unassigned}`, routes_created > 0 ? "success" : "info");
      }
      refetch();
    } else if (assignState === "error" && assignError) {
      addToast(assignError, "error");
    }
  }, [assignState, assignKind, assignResult, assignError, addToast, refetch]);

  const fetchFailedCount = useCallback(() => listOrders(token!, "status=FAILED&limit=1"), [token]);
  const { data: failedForCount } = usePolling(fetchFailedCount, POLL);
  const failedCount = failedForCount?.length ?? 0;

  const isToday = selectedDate === todayDateStr();
  const displayDate = new Date(selectedDate + "T12:00:00").toLocaleDateString("en-US", {
    weekday: "short", month: "short", day: "numeric", year: isToday ? undefined : "numeric",
  });

  const handleUnassign = useCallback(async () => {
    setUnassigning(true);
    try {
      const res = await triggerUnassign(token!, selectedDate);
      if (res.unassigned_count > 0) {
        addToast(`${res.unassigned_count} order${res.unassigned_count !== 1 ? "s" : ""} unassigned for ${displayDate}`, "success");
      } else {
        addToast(`No assigned orders found for ${displayDate}`, "info");
      }
      refetch();
    } catch {
      addToast("Failed to unassign orders — please try again", "error");
    } finally {
      setUnassigning(false);
    }
  }, [token, selectedDate, displayDate, addToast, refetch]);

  function shiftDate(delta: number) {
    const d = new Date(selectedDate + "T12:00:00");
    d.setDate(d.getDate() + delta);
    const pad = (n: number) => String(n).padStart(2, "0");
    setSelectedDate(`${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`);
    setPage(0);
  }

  // Create modal
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState<OrderCreate>({ ...emptyForm });
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [cargoSize, setCargoSize] = useState("medium_box");
  const [useCustomAddresses, setUseCustomAddresses] = useState(false);

  // Import modal
  const [showImport, setShowImport] = useState(false);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importPreview, setImportPreview] = useState<{ import_id: string; total_rows: number; valid_rows: number; invalid_rows: number } | null>(null);
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [importSuccess, setImportSuccess] = useState<string | null>(null);

  // Date-range optimize modal
  const [showOptimizeRange, setShowOptimizeRange] = useState(false);
  const [rangeFrom, setRangeFrom] = useState<string>(() => {
    const d = new Date();
    return d.toISOString().slice(0, 10);
  });
  const [rangeTo, setRangeTo] = useState<string>(() => {
    const d = new Date();
    d.setDate(d.getDate() + 6);
    return d.toISOString().slice(0, 10);
  });
  const [rangeError, setRangeError] = useState<string | null>(null);

  const _rangeSpanDays = useCallback(
    () => (Date.parse(rangeTo) - Date.parse(rangeFrom)) / 86_400_000 + 1,
    [rangeFrom, rangeTo],
  );

  const _validateRange = useCallback(() => {
    if (rangeTo < rangeFrom) {
      setRangeError("End date must be on or after start date");
      return null;
    }
    const span = _rangeSpanDays();
    if (span > 180) {
      // Mirrors the backend's RANGE_MAX_SPAN_DAYS safety net.
      setRangeError(
        "Range too long — the safety net is 180 days. Split into multiple requests.",
      );
      return null;
    }
    setRangeError(null);
    return span;
  }, [rangeFrom, rangeTo, _rangeSpanDays]);

  const runRangeOptimization = useCallback(async () => {
    if (!token) return;
    const span = _validateRange();
    if (span === null) return;
    const started = await assignRange(rangeFrom, rangeTo);
    if (started) {
      setShowOptimizeRange(false);
      const warning =
        span > 30
          ? " — large ranges may take several minutes (chunked solve)"
          : "";
      addToast(
        `Range optimization started for ${span} day${span === 1 ? "" : "s"}${warning}`,
        "info",
      );
    } else {
      setRangeError("Failed to start range optimization");
    }
  }, [token, rangeFrom, rangeTo, assignRange, addToast, _validateRange]);

  const previewRangeOptimization = useCallback(async () => {
    if (!token) return;
    const span = _validateRange();
    if (span === null) return;
    const started = await previewRange(rangeFrom, rangeTo);
    if (started) {
      setShowOptimizeRange(false);
      const warning =
        span > 30
          ? " — large ranges may take several minutes (chunked solve)"
          : "";
      addToast(
        `Range preview running for ${span} day${span === 1 ? "" : "s"}${warning}. Routes will be in DRAFT until you approve.`,
        "info",
      );
    } else {
      setRangeError("Failed to start range preview");
    }
  }, [token, rangeFrom, rangeTo, previewRange, addToast, _validateRange]);

  // Route scenario comparison
  const [showRouteComparison, setShowRouteComparison] = useState(false);
  const [comparison, setComparison] = useState<RouteScenarioComparison | null>(null);
  const [comparisonLoading, setComparisonLoading] = useState(false);
  const [comparisonError, setComparisonError] = useState<string | null>(null);
  const [selectedScenarioId, setSelectedScenarioId] = useState<string | null>(null);
  const [scenarioPreview, setScenarioPreview] = useState<OptimizationRunPreview | null>(null);
  const [scenarioPreviewLoading, setScenarioPreviewLoading] = useState(false);
  const [scenarioPreviewError, setScenarioPreviewError] = useState<string | null>(null);
  const [applyingScenario, setApplyingScenario] = useState(false);
  const [discardingComparison, setDiscardingComparison] = useState(false);
  const [showUseScenarioConfirm, setShowUseScenarioConfirm] = useState(false);

  const startRouteComparison = useCallback(async () => {
    if (!token) return;
    setShowRouteComparison(true);
    setComparisonLoading(true);
    setComparisonError(null);
    setScenarioPreview(null);
    setScenarioPreviewError(null);
    try {
      const response = await createRouteScenarioComparison(token, selectedDate);
      setComparison(response.comparison);
      setSelectedScenarioId(response.comparison.scenarios[0]?.id ?? null);
      addToast("Route comparison started", "info");
    } catch (err) {
      setComparisonError(err instanceof Error ? err.message : "Failed to start route comparison");
    } finally {
      setComparisonLoading(false);
    }
  }, [token, selectedDate, addToast]);

  const refreshRouteComparison = useCallback(async () => {
    if (!token || !comparison) return;
    try {
      const next = await getRouteScenarioComparison(token, comparison.id);
      setComparison(next);
      setComparisonError(null);
    } catch (err) {
      setComparisonError(err instanceof Error ? err.message : "Failed to load route comparison");
    }
  }, [token, comparison]);

  useEffect(() => {
    if (!showRouteComparison || !comparison) return;
    if (["READY", "PARTIAL", "FAILED", "APPLIED"].includes(comparison.status)) return;
    const handle = window.setInterval(() => {
      void refreshRouteComparison();
    }, 2500);
    return () => window.clearInterval(handle);
  }, [showRouteComparison, comparison, refreshRouteComparison]);

  useEffect(() => {
    if (!comparison?.scenarios.length) return;
    if (selectedScenarioId && comparison.scenarios.some((scenario) => scenario.id === selectedScenarioId)) return;
    const firstReady = comparison.scenarios.find((scenario) => scenario.status === "SUCCEEDED");
    setSelectedScenarioId((firstReady ?? comparison.scenarios[0]).id);
  }, [comparison, selectedScenarioId]);

  const selectedScenario = useMemo(
    () => comparison?.scenarios.find((scenario) => scenario.id === selectedScenarioId) ?? null,
    [comparison, selectedScenarioId],
  );

  const selectedScenarioPreviewKey = selectedScenario?.optimization_run_id ?? null;
  const selectedScenarioStatus = selectedScenario?.status ?? null;
  const selectedScenarioIdForPreview = selectedScenario?.id ?? null;
  const comparisonIdForPreview = comparison?.id ?? null;

  useEffect(() => {
    if (!token || !comparisonIdForPreview || !selectedScenarioPreviewKey || selectedScenarioStatus !== "SUCCEEDED" || !selectedScenarioIdForPreview) {
      setScenarioPreview(null);
      setScenarioPreviewError(null);
      setScenarioPreviewLoading(false);
      return;
    }
    let cancelled = false;
    setScenarioPreviewLoading(true);
    setScenarioPreviewError(null);
    void getRouteScenarioPreview(token, comparisonIdForPreview, selectedScenarioIdForPreview)
      .then((preview) => {
        if (!cancelled) setScenarioPreview(preview);
      })
      .catch((err) => {
        if (!cancelled) {
          setScenarioPreview(null);
          setScenarioPreviewError(err instanceof Error ? err.message : "Failed to load route preview");
        }
      })
      .finally(() => {
        if (!cancelled) setScenarioPreviewLoading(false);
      });
    return () => { cancelled = true; };
  }, [token, comparisonIdForPreview, selectedScenarioPreviewKey, selectedScenarioStatus, selectedScenarioIdForPreview]);

  const discardRouteComparison = useCallback(async () => {
    if (!token || !comparison) {
      setShowRouteComparison(false);
      return;
    }
    setDiscardingComparison(true);
    try {
      await deleteRouteScenarioComparison(token, comparison.id);
      setShowRouteComparison(false);
      setComparison(null);
      setSelectedScenarioId(null);
      addToast("Route comparison discarded", "info");
    } catch (err) {
      addToast(err instanceof Error ? err.message : "Failed to discard route comparison", "error");
    } finally {
      setDiscardingComparison(false);
    }
  }, [token, comparison, addToast]);

  const applySelectedScenario = useCallback(async () => {
    if (!token || !comparison || !selectedScenario) return;
    setApplyingScenario(true);
    try {
      const response = await selectRouteScenario(token, comparison.id, selectedScenario.id);
      setComparison(response.comparison);
      setShowUseScenarioConfirm(false);
      setShowRouteComparison(false);
      addToast(`${response.approved_routes} route${response.approved_routes === 1 ? "" : "s"} applied`, "success");
      refetch();
    } catch (err) {
      addToast(err instanceof Error ? err.message : "Failed to apply scenario", "error");
    } finally {
      setApplyingScenario(false);
    }
  }, [token, comparison, selectedScenario, addToast, refetch]);

  // Detail slide-over
  const [selectedOrderId, setSelectedOrderId] = useState<string | null>(null);
  const [forensicsOrderId, setForensicsOrderId] = useState<string | null>(null);
  const [detailOrder, setDetailOrder] = useState<Order | null>(null);
  const [detailHistory, setDetailHistory] = useState<OrderStatusHistoryItem[] | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailPackages, setDetailPackages] = useState<OrderPackage[] | null>(null);
  const [expandedPkg, setExpandedPkg] = useState<string | null>(null);
  const [pkgEvents, setPkgEvents] = useState<Record<string, InventoryEvent[]>>({});

  // Order legs (multi-depot journey)
  const [detailLegs, setDetailLegs] = useState<OrderLeg[] | null>(null);

  // Request return form
  const [showReturnForm, setShowReturnForm] = useState(false);
  const [returnPickupFrom, setReturnPickupFrom] = useState("");
  const [returnPickupTo, setReturnPickupTo] = useState("");
  const [returnReasonCode, setReturnReasonCode] = useState("");
  const [returnLoading, setReturnLoading] = useState(false);
  const [returnSuccess, setReturnSuccess] = useState<{ id: string } | null>(null);

  // Actions
  const [actionLoading, setActionLoading] = useState(false);
  const [showCancelConfirm, setShowCancelConfirm] = useState(false);
  const [assignCourierId, setAssignCourierId] = useState("");
  const [rescheduleFrom, setRescheduleFrom] = useState("");
  const [rescheduleTo, setRescheduleTo] = useState("");

  const fetchCouriers = useCallback(() => listCouriers(token!), [token]);
  const { data: couriersList } = usePolling(fetchCouriers, 60_000, !!selectedOrderId || showRouteComparison);
  const fetchDepots = useCallback(() => listDepots(token!), [token]);
  const { data: depotsList } = usePolling(fetchDepots, 120_000);
  const fetchVehicleTags = useCallback(() => listVehicleTags(token!), [token]);
  const { data: vehicleTagsList } = usePolling(fetchVehicleTags, 300_000);
  const [operationsPolicy, setOperationsPolicy] = useState<OperationsPolicy | null>(null);

  useEffect(() => {
    if (!token) { setOperationsPolicy(null); return; }
    let cancelled = false;
    void getOperationsPolicy(token).then((p) => { if (!cancelled) setOperationsPolicy(p); })
      .catch((err) => { if (!cancelled && getErrorStatus(err) !== 403) addToast("Failed to load operations settings", "error"); });
    return () => { cancelled = true; };
  }, [token, addToast]);

  async function openDetail(orderId: string) {
    setSelectedOrderId(orderId);
    setDetailLoading(true);
    setAssignCourierId(""); setRescheduleFrom(""); setRescheduleTo("");
    setDetailPackages(null); setExpandedPkg(null); setPkgEvents({});
    setDetailLegs(null);
    setShowReturnForm(false); setReturnPickupFrom(""); setReturnPickupTo(""); setReturnReasonCode(""); setReturnSuccess(null);
    try {
      const [order, history, packages, legs] = await Promise.all([
        getOrder(token!, orderId),
        getOrderHistory(token!, orderId),
        listOrderPackages(token!, orderId).catch(() => [] as OrderPackage[]),
        getOrderLegs(token!, orderId).catch(() => [] as OrderLeg[]),
      ]);
      setDetailOrder(order); setDetailHistory(history); setDetailPackages(packages);
      setDetailLegs(legs.length > 0 ? legs : null);
    } catch { addToast("Failed to load order details", "error"); }
    finally { setDetailLoading(false); }
  }

  async function togglePkgEvents(pkgId: string) {
    if (expandedPkg === pkgId) { setExpandedPkg(null); return; }
    setExpandedPkg(pkgId);
    if (!pkgEvents[pkgId]) {
      try {
        const events = await getPackageEvents(token!, pkgId);
        setPkgEvents((prev) => ({ ...prev, [pkgId]: events }));
      } catch { /* ignore */ }
    }
  }

  function closeDetail() {
    setSelectedOrderId(null); setDetailOrder(null); setDetailHistory(null);
    setDetailPackages(null); setExpandedPkg(null); setPkgEvents({});
    setDetailLegs(null);
    setShowReturnForm(false); setReturnPickupFrom(""); setReturnPickupTo(""); setReturnReasonCode(""); setReturnSuccess(null);
  }

  async function handleAssign() {
    if (!assignCourierId || !selectedOrderId) return;
    setActionLoading(true);
    try {
      await assignOrder(token!, selectedOrderId, assignCourierId);
      addToast("Order assigned", "success");
      void openDetail(selectedOrderId); refetch();
    } catch (err) { addToast(err instanceof Error ? err.message : "Assign failed", "error"); }
    finally { setActionLoading(false); }
  }

  async function handleCancel() {
    if (!selectedOrderId) return;
    setActionLoading(true);
    try {
      await cancelOrder(token!, selectedOrderId);
      addToast("Order cancelled", "success");
      void openDetail(selectedOrderId); refetch();
    } catch (err) { addToast(err instanceof Error ? err.message : "Cancel failed", "error"); }
    finally { setActionLoading(false); }
  }

  const [dispatchNowLoading, setDispatchNowLoading] = useState(false);

  async function handleDispatchNow() {
    if (!selectedOrderId) return;
    setDispatchNowLoading(true);
    try {
      const res = await dispatchOrderNow(token!, selectedOrderId);
      if (res.assigned) {
        addToast(res.message, "success");
        void openDetail(selectedOrderId);
        refetch();
      } else {
        addToast(res.message, "error");
      }
    } catch (err) {
      addToast(err instanceof Error ? err.message : "Dispatch failed", "error");
    } finally {
      setDispatchNowLoading(false);
    }
  }

  async function handleReschedule() {
    if (!selectedOrderId || !rescheduleFrom || !rescheduleTo) return;
    setActionLoading(true);
    try {
      await rescheduleOrder(token!, selectedOrderId, {
        earliest_delivery: new Date(rescheduleFrom).toISOString(),
        latest_delivery: new Date(rescheduleTo).toISOString(),
      });
      addToast("Order rescheduled", "success");
      void openDetail(selectedOrderId); refetch();
    } catch (err) { addToast(err instanceof Error ? err.message : "Reschedule failed", "error"); }
    finally { setActionLoading(false); }
  }

  async function handleRequestReturn() {
    if (!selectedOrderId || !returnPickupFrom || !returnPickupTo) return;
    if (returnPickupFrom >= returnPickupTo) { addToast("Pickup window end must be after start", "error"); return; }
    setReturnLoading(true);
    try {
      const returned = await createOrderReturn(token!, selectedOrderId, {
        earliest_pickup: new Date(returnPickupFrom).toISOString(),
        latest_pickup: new Date(returnPickupTo).toISOString(),
        reason_code: returnReasonCode || undefined,
      });
      setReturnSuccess({ id: returned.id });
      setShowReturnForm(false);
      addToast("Return order created", "success");
      refetch();
    } catch (err) { addToast(err instanceof Error ? err.message : "Failed to create return order", "error"); }
    finally { setReturnLoading(false); }
  }

  function updateForm<K extends keyof OrderCreate>(key: K, value: OrderCreate[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }
  function updateCustomField(key: string, value: unknown) {
    setForm((prev) => {
      const next = { ...(prev.custom_fields ?? {}) };
      if (value === "" || value === undefined || value === null) delete next[key];
      else next[key] = value;
      return { ...prev, custom_fields: next };
    });
  }
  function resetCreateForm(orderType: OrderType = "ON_DEMAND") {
    setShowCreate(true); setCreateError(null); setCargoSize("medium_box");
    setUseCustomAddresses(false);
    setForm({ ...emptyForm, type: orderType, custom_fields: {} });
  }
  function selectCargoSize(presetId: string) {
    setCargoSize(presetId);
    if (presetId === "custom") return;
    const p = CARGO_PRESETS.find((x) => x.id === presetId);
    if (!p) return;
    setForm((prev) => ({ ...prev, weight_kg: p.weight_kg, length_cm: p.length_cm, width_cm: p.width_cm, height_cm: p.height_cm, volume_cm3: p.length_cm * p.width_cm * p.height_cm }));
  }
  function handlePickupSelect(r: GeocodeResult) { setForm((prev) => ({ ...prev, pickup_address: r.display_name, pickup_lat: r.lat, pickup_lon: r.lon })); }
  function handleDeliverySelect(r: GeocodeResult) { setForm((prev) => ({ ...prev, delivery_address: r.display_name, delivery_lat: r.lat, delivery_lon: r.lon })); }

  const selectedDepot = useMemo(() => depotsList?.find((d) => d.id === form.depot_id) ?? null, [depotsList, form.depot_id]);

  function handleDepotSelect(depotId: string) {
    if (useCustomAddresses) {
      setForm((prev) => ({ ...prev, depot_id: depotId || undefined }));
      return;
    }
    const depot = depotsList?.find((d) => d.id === depotId) ?? null;
    setForm((prev) => {
      const next = { ...prev, depot_id: depotId || undefined };
      if (depot) {
        if (prev.type !== "REVERSE") {
          next.pickup_address = depot.address;
          next.pickup_lat = depot.lat;
          next.pickup_lon = depot.lon;
        } else {
          next.delivery_address = depot.address;
          next.delivery_lat = depot.lat;
          next.delivery_lon = depot.lon;
        }
      } else {
        if (prev.type !== "REVERSE") {
          next.pickup_address = "";
          next.pickup_lat = 0;
          next.pickup_lon = 0;
        } else {
          next.delivery_address = "";
          next.delivery_lat = 0;
          next.delivery_lon = 0;
        }
      }
      return next;
    });
  }

  function handleTypeChange(newType: OrderType) {
    setForm((prev) => {
      const next = { ...prev, type: newType };
      // Clear pickup window when switching away from PICKUP_AND_DELIVERY
      if (newType !== "PICKUP_AND_DELIVERY") { next.earliest_pickup = undefined; next.latest_pickup = undefined; }
      if (useCustomAddresses) return next;
      const depot = depotsList?.find((d) => d.id === prev.depot_id) ?? null;
      if (!depot) return next;
      if (newType !== "REVERSE") {
        next.pickup_address = depot.address;
        next.pickup_lat = depot.lat;
        next.pickup_lon = depot.lon;
        if (prev.type === "REVERSE") {
          next.delivery_address = "";
          next.delivery_lat = 0;
          next.delivery_lon = 0;
        }
      } else {
        next.delivery_address = depot.address;
        next.delivery_lat = depot.lat;
        next.delivery_lon = depot.lon;
        if (prev.type !== "REVERSE") {
          next.pickup_address = "";
          next.pickup_lat = 0;
          next.pickup_lon = 0;
        }
      }
      return next;
    });
  }

  const activeCustomFields = useMemo(() => getApplicableCustomFields(operationsPolicy, form.type), [operationsPolicy, form.type]);
  const detailCustomFieldDefs = useMemo(() => (detailOrder ? getApplicableCustomFields(operationsPolicy, detailOrder.type as OrderType) : []), [operationsPolicy, detailOrder]);
  const detailCustomFieldEntries = useMemo(() => {
    if (!detailOrder?.custom_fields) return [];
    const labelMap = new Map(detailCustomFieldDefs.map((f) => [f.key, f.label]));
    return Object.entries(detailOrder.custom_fields).map(([key, value]) => ({ key, label: labelMap.get(key) ?? key, value }));
  }, [detailCustomFieldDefs, detailOrder]);
  const rolePermissions = useMemo(() => getRolePermissions(operationsPolicy, role), [operationsPolicy, role]);

  const customerPhoneMode = getStandardFieldMode(operationsPolicy, "customer_phone");
  const notesMode = getStandardFieldMode(operationsPolicy, "notes");
  const externalRefMode = getStandardFieldMode(operationsPolicy, "external_ref");
  const depotMode = getStandardFieldMode(operationsPolicy, "depot_id");
  const fragileMode = getStandardFieldMode(operationsPolicy, "is_fragile");
  const tempSensitiveMode = getStandardFieldMode(operationsPolicy, "is_temp_sensitive");

  const canAssign = detailOrder?.status === "PENDING" && (rolePermissions?.reassign_order ?? true);
  const canCancel = !!detailOrder && ["PENDING", "ASSIGNED", "FAILED"].includes(detailOrder.status) && (rolePermissions?.cancel_order ?? true);
  const canReschedule = !!detailOrder && ["FAILED", "EXPIRED"].includes(detailOrder.status) && (rolePermissions?.edit_order ?? true);
  const canRequestReturn = !!detailOrder && detailOrder.status === "DELIVERED" && !detailOrder.return_of_order_id && !returnSuccess;

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!useCustomAddresses && !form.depot_id) { setCreateError("Select a depot for this order."); return; }
    if (!form.pickup_lat || !form.pickup_lon) { setCreateError("Select a pickup address from suggestions."); return; }
    if (!form.delivery_lat || !form.delivery_lon) { setCreateError("Select a delivery address from suggestions."); return; }
    if (!form.weight_kg || form.weight_kg <= 0) { setCreateError("Select a cargo size or enter a valid weight."); return; }
    if (!form.earliest_delivery || !form.latest_delivery) { setCreateError("Set the delivery window."); return; }
    if (isFieldRequired(customerPhoneMode) && !form.customer_phone?.trim()) { setCreateError("Customer phone is required."); return; }
    if (isFieldRequired(externalRefMode) && !form.external_ref?.trim()) { setCreateError("Reference number is required."); return; }
    if (isFieldRequired(notesMode) && !form.notes?.trim()) { setCreateError("Notes are required."); return; }
    if (form.latest_delivery <= form.earliest_delivery) { setCreateError("Latest delivery must be after earliest."); return; }
    if (form.latest_delivery < nowLocal()) { setCreateError("Delivery deadline is in the past."); return; }
    if (form.type === "PICKUP_AND_DELIVERY") {
      if (!form.earliest_pickup || !form.latest_pickup) { setCreateError("Pickup window is required for Pickup & Delivery orders."); return; }
      if (form.earliest_pickup >= form.latest_pickup) { setCreateError("Pickup window end must be after start."); return; }
    }
    setCreating(true); setCreateError(null);
    try {
      const filteredCustomFields = Object.fromEntries(
        activeCustomFields.map((f) => [f.key, form.custom_fields?.[f.key]]).filter(([, v]) => v !== undefined && v !== "" && v !== null),
      );
      const missingField = activeCustomFields.find((f) => f.required && filteredCustomFields[f.key] === undefined);
      if (missingField) { setCreateError(`${missingField.label} is required.`); return; }
      const payload: OrderCreate = { ...form, custom_fields: filteredCustomFields };
      if (cargoSize === "custom" && payload.length_cm && payload.width_cm && payload.height_cm) payload.volume_cm3 = payload.length_cm * payload.width_cm * payload.height_cm;
      if (!isFieldVisible(customerPhoneMode)) payload.customer_phone = undefined;
      if (!isFieldVisible(notesMode)) payload.notes = undefined;
      if (!isFieldVisible(externalRefMode)) payload.external_ref = undefined;
      if (!isFieldVisible(depotMode) || useCustomAddresses) payload.depot_id = undefined;
      if (!isFieldVisible(fragileMode)) payload.is_fragile = undefined;
      if (!isFieldVisible(tempSensitiveMode)) payload.is_temp_sensitive = undefined;
      await createOrder(token!, payload);
      setShowCreate(false); setForm({ ...emptyForm, custom_fields: {} }); setCargoSize("medium_box"); setUseCustomAddresses(false);
      addToast("Order created", "success"); refetch();
    } catch (err) { setCreateError(err instanceof Error ? err.message : "Failed to create order"); }
    finally { setCreating(false); }
  }

  async function handleImportPreview() {
    if (!importFile) return;
    setImporting(true); setImportError(null);
    try { setImportPreview(await importOrdersPreview(token!, importFile)); }
    catch (err) { setImportError(err instanceof Error ? err.message : "Preview failed"); }
    finally { setImporting(false); }
  }

  async function handleImportPublish() {
    if (!importPreview) return;
    setImporting(true); setImportError(null);
    try {
      const r = await importOrdersPublish(token!, importPreview.import_id);
      setImportSuccess(`Imported ${r.published_count} orders`); setImportPreview(null); setImportFile(null); refetch();
    } catch (err) { setImportError(err instanceof Error ? err.message : "Import failed"); }
    finally { setImporting(false); }
  }

  const tabsWithCounts = STATUS_TABS.map((t) =>
    t.id === "FAILED" ? { ...t, count: failedCount } : t,
  );

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Toolbar */}
      <div className="px-6 pt-5 pb-0 shrink-0 border-b border-slate-200">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-3">
            <h1 className="text-base font-semibold text-slate-900">Orders</h1>
            {/* Date nav */}
            <div className="flex items-center gap-1">
              <button onClick={() => shiftDate(-1)} className="p-1 rounded text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors duration-150">
                <ChevronLeft size={15} />
              </button>
              <input
                type="date"
                value={selectedDate}
                onChange={(e) => { setSelectedDate(e.target.value); setPage(0); }}
                className="border border-slate-200 rounded-md px-2 py-1 text-xs text-slate-600 bg-white focus:outline-none focus:border-accent-500"
              />
              <button onClick={() => shiftDate(1)} className="p-1 rounded text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors duration-150">
                <ChevronRight size={15} />
              </button>
              {!isToday && (
                <button onClick={() => { setSelectedDate(todayDateStr()); setPage(0); }} className="text-xs text-accent-600 hover:text-accent-700 font-medium px-1.5">
                  Today
                </button>
              )}
            </div>
            <span className="text-xs text-slate-400 tabular-nums">
              {isSearching ? "All dates" : `${displayDate} · ${total} order${total !== 1 ? "s" : ""}`}
              {failedCount > 0 && <span className="text-red-500"> · {failedCount} failed</span>}
            </span>
          </div>
          <div className="flex items-center gap-2">
            {/* Search */}
            <div className="relative">
              <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
              <input
                type="text"
                placeholder="Search orders…"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                className="pl-8 pr-3 py-1.5 text-xs border border-slate-200 rounded-md bg-white text-slate-700 focus:outline-none focus:border-accent-500 w-44"
              />
            </div>
            {canRunDispatcherActions(role) && !isSearching && (
              <>
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => void assignOptimal(selectedDate)}
                  disabled={assignBusy || unassigning || comparisonLoading}
                  title={`Optimally assign all pending orders for ${displayDate}`}
                >
                  <Zap size={13} />
                  {assignBusy
                    ? assignKind === "range"
                      ? assignProgress
                        ? `Optimizing… (chunk ${assignProgress.chunks_completed}/${assignProgress.chunks_total})`
                        : "Optimizing…"
                      : "Assigning…"
                    : "Assign Optimally"}
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => void startRouteComparison()}
                  disabled={assignBusy || unassigning || comparisonLoading}
                  data-testid="compare-routes-btn"
                  title={`Compare route plans for ${displayDate}`}
                >
                  <GitCompare size={13} />
                  {comparisonLoading ? "Comparing…" : "Compare Routes"}
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => void handleUnassign()}
                  disabled={unassigning || assignBusy}
                  title={`Unassign all assigned orders for ${displayDate} (excludes in-transit & delivered)`}
                >
                  <RotateCcw size={13} />
                  {unassigning ? "Unassigning…" : "Unassign Day"}
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => {
                    setRangeError(null);
                    setShowOptimizeRange(true);
                  }}
                  disabled={assignBusy || unassigning}
                  data-testid="optimize-range-btn"
                  title="Plan any 1–14 day range at once — the solver may shuffle delivery dates within each order's window"
                >
                  <CalendarRange size={13} />
                  Optimize Range
                </Button>
              </>
            )}
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void handleExportCsv()}
              disabled={exporting}
              title={`Download every order created on ${selectedDate}`}
            >
              <Download size={13} />
              {exporting ? "Exporting…" : "Export CSV"}
            </Button>
            <Button variant="secondary" size="sm" onClick={() => setShowImport(true)}>
              <Upload size={13} />
              Import CSV
            </Button>
            <Button variant="primary" size="sm" onClick={() => resetCreateForm()} data-testid="create-order-btn">
              <Plus size={13} />
              New Order
            </Button>
          </div>
        </div>

        <Tabs
          tabs={tabsWithCounts}
          active={statusFilter}
          onChange={(id) => { setStatusFilter(id); setPage(0); }}
        />
      </div>

      {/* Table */}
      <div className="flex-1 overflow-y-auto px-6 py-4 thin-scrollbar">
        {isLoading && !orders ? (
          <div className="py-16 text-center text-sm text-slate-400">Loading…</div>
        ) : ordersError && !orders ? (
          <div className="py-16 text-center">
            <p className="text-sm text-red-600">Failed to load orders</p>
            <button onClick={refetch} className="mt-2 text-xs text-accent-600 hover:underline">Try again</button>
          </div>
        ) : !orders?.length ? (
          <div className="py-16 text-center text-sm text-slate-400">
            {statusFilter === "FAILED" ? "No failed orders — all deliveries on track." : "No orders found."}
          </div>
        ) : (
          <table data-testid="orders-table" className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b border-slate-200">
                <th className="py-2.5 px-3 text-left text-xs font-medium uppercase tracking-wide text-slate-400">ID</th>
                <th className="py-2.5 px-3 text-left text-xs font-medium uppercase tracking-wide text-slate-400">Route</th>
                <th className="py-2.5 px-3 text-left text-xs font-medium uppercase tracking-wide text-slate-400">Status</th>
                <th className="py-2.5 px-3 text-left text-xs font-medium uppercase tracking-wide text-slate-400">Customer</th>
                <th className="py-2.5 px-3 text-left text-xs font-medium uppercase tracking-wide text-slate-400">Window</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((order) => (
                <tr
                  key={order.id}
                  data-testid="order-row"
                  onClick={() => void openDetail(order.id)}
                  className={`border-b border-slate-100 cursor-pointer transition-colors duration-100 ${
                    selectedOrderId === order.id ? "bg-orange-50" : "hover:bg-slate-50"
                  }`}
                >
                  <td className="py-2.5 px-3">
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono text-xs text-slate-700">{order.id.slice(0, 8)}</span>
                      {order.return_of_order_id && (
                        <span className="text-[10px] text-slate-400" title="Return order">↩</span>
                      )}
                    </div>
                    <p className="text-[10px] text-slate-400 truncate max-w-[180px] mt-0.5">
                      {order.delivery_address.split(",")[0]}
                    </p>
                  </td>
                  <td className="py-2.5 px-3">
                    <span className="text-xs text-slate-500">{order.type.replace(/_/g, " ")}</span>
                  </td>
                  <td className="py-2.5 px-3"><StatusBadge value={order.status} /></td>
                  <td className="py-2.5 px-3 text-xs text-slate-600 truncate max-w-[160px]">{order.customer_email}</td>
                  <td className="py-2.5 px-3 text-xs text-slate-400 tabular-nums whitespace-nowrap">
                    {order.scheduled_for_date
                      ? `Scheduled ${new Date(order.scheduled_for_date + "T12:00:00").toLocaleDateString()}`
                      : new Date(order.latest_delivery).toLocaleDateString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />
      </div>

      {/* Detail slide-over */}
      <SlideOver
        open={!!selectedOrderId}
        onClose={closeDetail}
        title={detailOrder ? `#${detailOrder.id.slice(0, 8)}` : "Order"}
        subtitle={detailOrder?.delivery_address.split(",")[0]}
      >
        {detailLoading ? (
          <div className="p-8 text-center text-sm text-slate-400">Loading…</div>
        ) : detailOrder ? (
          <div className="px-5 py-4 space-y-5">
            <div className="flex items-center gap-2 flex-wrap">
              <StatusBadge value={detailOrder.status} />
              <StatusBadge value={detailOrder.priority} />
              <span className="text-xs text-slate-400">{detailOrder.type.replace("_", " ")}</span>
              <button
                type="button"
                onClick={() => setForensicsOrderId(detailOrder.id)}
                className="ml-auto inline-flex items-center gap-1 text-[11px] font-medium text-slate-600 hover:text-slate-900 px-2 py-0.5 rounded border border-slate-200 hover:bg-slate-50 transition-colors"
                title="Open the forensic timeline (status transitions, dispatch decisions, ETA accuracy)"
              >
                Forensics →
              </button>
            </div>

            <div>
              {detailOrder.return_of_order_id && (
                <FieldRow
                  label="Return for"
                  value={
                    <button
                      onClick={() => void openDetail(detailOrder.return_of_order_id!)}
                      className="font-mono text-accent-600 hover:underline"
                    >
                      #{detailOrder.return_of_order_id.slice(0, 8)}
                    </button>
                  }
                />
              )}
              <FieldRow label="Pickup" value={detailOrder.pickup_address} />
              <FieldRow label="Delivery" value={detailOrder.delivery_address} />
              {detailOrder.scheduled_for_date && (
                <FieldRow
                  label="Scheduled day"
                  value={new Date(detailOrder.scheduled_for_date + "T12:00:00").toLocaleDateString()}
                />
              )}
              {detailOrder.earliest_pickup && detailOrder.latest_pickup && (
                <FieldRow label="Pickup window" value={`${new Date(detailOrder.earliest_pickup).toLocaleString()} — ${new Date(detailOrder.latest_pickup).toLocaleString()}`} />
              )}
              <FieldRow label="Window" value={`${new Date(detailOrder.earliest_delivery).toLocaleString()} — ${new Date(detailOrder.latest_delivery).toLocaleString()}`} />
              <FieldRow label="Customer" value={detailOrder.customer_email} />
              {detailOrder.customer_phone && <FieldRow label="Phone" value={detailOrder.customer_phone} />}
              {detailOrder.external_ref && <FieldRow label="Ref" value={detailOrder.external_ref} />}
              {detailOrder.weight_kg != null && <FieldRow label="Weight" value={`${detailOrder.weight_kg} kg`} />}
              <FieldRow label="Attempts" value={`${detailOrder.attempt_count} / ${detailOrder.max_attempts}`} />
              {detailOrder.assigned_courier_id && (
                <FieldRow
                  label="Courier"
                  value={
                    <span className="text-xs text-slate-800">
                      {courierAssignmentLabel(
                        (couriersList ?? []).find((c) => c.id === detailOrder.assigned_courier_id),
                        detailOrder.assigned_courier_id,
                      )}
                    </span>
                  }
                />
              )}
              {detailOrder.delivery_otp_code && <FieldRow label="OTP" value={detailOrder.delivery_otp_code} />}
              {detailOrder.notes && <FieldRow label="Notes" value={detailOrder.notes} />}
              <FieldRow label="Created" value={new Date(detailOrder.created_at).toLocaleString()} />
            </div>

            {detailCustomFieldEntries.length > 0 && (
              <div>
                <p className="text-[10px] uppercase tracking-wide font-medium text-slate-400 mb-2">Custom fields</p>
                {detailCustomFieldEntries.map((f) => (
                  <FieldRow key={f.key} label={f.label} value={formatCustomFieldValue(f.value)} />
                ))}
              </div>
            )}

            {/* Dispatch Now — smart insertion into existing routes */}
            {canAssign && (
              <div className="pt-1 space-y-3">
                <div>
                  <p className="text-[10px] uppercase tracking-wide font-medium text-slate-400 mb-1">Urgent dispatch</p>
                  <p className="text-xs text-slate-500 mb-2">
                    Slots this order into the best available courier route without disturbing any existing assignments.
                  </p>
                  <Button
                    variant="primary"
                    size="sm"
                    className="w-full justify-center"
                    disabled={dispatchNowLoading}
                    onClick={() => void handleDispatchNow()}
                  >
                    {dispatchNowLoading ? "Finding best route…" : "Dispatch Now"}
                  </Button>
                </div>

                <div className="border-t border-slate-100 pt-3">
                  <p className="text-[10px] uppercase tracking-wide font-medium text-slate-400 mb-2">Assign manually</p>
                  <select
                    value={assignCourierId}
                    onChange={(e) => setAssignCourierId(e.target.value)}
                    className={inputCls}
                  >
                    <option value="">Select courier…</option>
                    {(couriersList ?? []).filter((c) => c.availability_status === "ONLINE").map((c) => (
                      <option key={c.id} value={c.id}>{c.id.slice(0, 8)} — {c.vehicle_type.replace("_", " ")}</option>
                    ))}
                  </select>
                  <Button variant="secondary" size="sm" className="w-full justify-center mt-2" disabled={!assignCourierId || actionLoading} onClick={() => void handleAssign()}>
                    Assign
                  </Button>
                </div>
              </div>
            )}

            {/* Reschedule */}
            {canReschedule && (
              <div className="space-y-2 pt-1">
                <p className="text-[10px] uppercase tracking-wide font-medium text-slate-400">Reschedule</p>
                <input type="datetime-local" value={rescheduleFrom} onChange={(e) => setRescheduleFrom(e.target.value)} className={inputCls} />
                <input type="datetime-local" value={rescheduleTo} onChange={(e) => setRescheduleTo(e.target.value)} className={inputCls} />
                <Button variant="secondary" size="sm" className="w-full justify-center" disabled={!rescheduleFrom || !rescheduleTo || actionLoading} onClick={() => void handleReschedule()}>
                  Reschedule
                </Button>
              </div>
            )}

            {/* Request return */}
            {canRequestReturn && (
              <div className="space-y-2 pt-1">
                <p className="text-[10px] uppercase tracking-wide font-medium text-slate-400">Request return</p>
                {!showReturnForm ? (
                  <>
                    <p className="text-xs text-slate-500">Schedule a courier to collect this package back from the customer.</p>
                    <Button variant="secondary" size="sm" className="w-full justify-center" onClick={() => setShowReturnForm(true)}>
                      Request return
                    </Button>
                  </>
                ) : (
                  <>
                    <input type="datetime-local" value={returnPickupFrom} min={nowLocal()}
                      onChange={(e) => { setReturnPickupFrom(e.target.value); if (returnPickupTo && e.target.value > returnPickupTo) setReturnPickupTo(""); }}
                      className={inputCls} placeholder="Pickup opens at" />
                    <input type="datetime-local" value={returnPickupTo} min={returnPickupFrom || nowLocal()}
                      onChange={(e) => setReturnPickupTo(e.target.value)} className={inputCls} placeholder="Pickup closes at" />
                    <label htmlFor="return-reason" className={labelCls}>Reason (optional)</label>
                    <select id="return-reason" value={returnReasonCode} onChange={(e) => setReturnReasonCode(e.target.value)} className={inputCls}>
                      <option value="">Select a reason…</option>
                      <option value="REFUSED">Customer refused</option>
                      <option value="DAMAGED">Item damaged</option>
                      <option value="WRONG_ITEM">Wrong item delivered</option>
                      <option value="OTHER">Other</option>
                    </select>
                    <div className="flex gap-2">
                      <Button variant="primary" size="sm" className="flex-1 justify-center"
                        disabled={!returnPickupFrom || !returnPickupTo || returnLoading}
                        onClick={() => void handleRequestReturn()}>
                        {returnLoading ? "Submitting…" : "Submit return request"}
                      </Button>
                      <Button variant="secondary" size="sm" onClick={() => { setShowReturnForm(false); setReturnPickupFrom(""); setReturnPickupTo(""); setReturnReasonCode(""); }}>
                        Cancel
                      </Button>
                    </div>
                  </>
                )}
              </div>
            )}

            {/* Return created confirmation */}
            {returnSuccess && (
              <div className="border-l-2 border-l-emerald-500 pl-3 py-2 text-xs text-emerald-700">
                Return order created ·{" "}
                <button onClick={() => void openDetail(returnSuccess.id)} className="font-mono underline">
                  #{returnSuccess.id.slice(0, 8)}
                </button>
              </div>
            )}

            {/* Cancel */}
            {canCancel && (
              <Button variant="danger" size="sm" className="w-full justify-center" disabled={actionLoading} onClick={() => setShowCancelConfirm(true)}>
                Cancel order
              </Button>
            )}

            {/* Journey — visible only for multi-depot orders with legs */}
            {detailLegs && detailLegs.length > 0 && (
              <div>
                <p className="text-[10px] uppercase tracking-wide font-medium text-slate-400 mb-2">Journey</p>
                {detailLegs.map((leg, idx) => {
                  const fromName = leg.from_depot_id
                    ? (depotsList?.find((d) => d.id === leg.from_depot_id)?.name ?? leg.from_depot_id.slice(0, 8))
                    : "Origin";
                  const toName = leg.to_depot_id
                    ? (depotsList?.find((d) => d.id === leg.to_depot_id)?.name ?? leg.to_depot_id.slice(0, 8))
                    : "Customer";
                  const kindLabel = leg.leg_kind === "FIRST_MILE" ? "First mile"
                    : leg.leg_kind === "LINEHAUL" ? "Linehaul"
                    : "Last mile";
                  const eta = leg.planned_delivery_at
                    ? new Date(leg.planned_delivery_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
                    : null;
                  return (
                    <div key={leg.id} className="flex items-start gap-2 py-1.5 border-b border-slate-50 last:border-0">
                      <div className={`mt-1.5 w-1.5 h-1.5 rounded-full shrink-0 ${
                        leg.status === "COMPLETED" ? "bg-emerald-500"
                          : leg.status === "IN_TRANSIT" ? "bg-blue-500"
                          : leg.status === "CANCELLED" ? "bg-slate-300"
                          : "bg-amber-400"
                      }`} />
                      <div>
                        <p className="text-xs font-medium text-slate-700">
                          {kindLabel} · {fromName} → {toName}
                        </p>
                        <p className="text-[10px] text-slate-400">
                          <StatusBadge value={leg.status} />
                          {eta && <span className="ml-1.5">· ETA {eta}</span>}
                          <span className="ml-1.5 tabular-nums">#{idx + 1}</span>
                        </p>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            {/* Packages */}
            {detailPackages && detailPackages.length > 0 && (
              <div>
                <p className="text-[10px] uppercase tracking-wide font-medium text-slate-400 mb-2">Packages</p>
                {detailPackages.map((pkg) => (
                  <div key={pkg.id} className="border-b border-slate-100">
                    <button
                      onClick={() => void togglePkgEvents(pkg.id)}
                      className="w-full flex items-center justify-between py-2 text-left hover:bg-slate-50 transition-colors duration-100"
                    >
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-mono text-slate-700">{pkg.label}</span>
                        <StatusBadge value={pkg.status} />
                      </div>
                      <span className="text-[10px] text-slate-400 tabular-nums">{pkg.weight_kg}kg</span>
                    </button>
                    {expandedPkg === pkg.id && (
                      <div className="pb-2 pl-2">
                        {!pkgEvents[pkg.id] ? (
                          <p className="text-[10px] text-slate-400">Loading…</p>
                        ) : pkgEvents[pkg.id].length === 0 ? (
                          <p className="text-[10px] text-slate-400">No events</p>
                        ) : (
                          pkgEvents[pkg.id].map((ev) => (
                            <div key={ev.id} className="flex items-start gap-2 py-1">
                              <div className="mt-1 w-1.5 h-1.5 rounded-full bg-accent-400 shrink-0" />
                              <div>
                                <p className="text-[10px] font-medium text-slate-700">{ev.event_type.replace(/_/g, " ")}</p>
                                {ev.notes && <p className="text-[10px] text-slate-500">{ev.notes}</p>}
                                <p className="text-[10px] text-slate-400 tabular-nums">{new Date(ev.created_at).toLocaleString()}</p>
                              </div>
                            </div>
                          ))
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* History */}
            {detailHistory && detailHistory.length > 0 && (
              <div>
                <p className="text-[10px] uppercase tracking-wide font-medium text-slate-400 mb-2">History</p>
                {detailHistory.map((h) => (
                  <div key={h.id} className="flex items-start gap-2 py-1.5 border-b border-slate-50 last:border-0">
                    <div className="mt-1.5 w-1.5 h-1.5 rounded-full bg-slate-300 shrink-0" />
                    <div>
                      <div className="flex items-center gap-1.5">
                        {h.from_status && <><StatusBadge value={h.from_status} /><span className="text-slate-300 text-[10px]">→</span></>}
                        <StatusBadge value={h.to_status} />
                      </div>
                      {h.reason && <p className="text-[10px] text-slate-500 mt-0.5">{h.reason}</p>}
                      <p className="text-[10px] text-slate-400 tabular-nums mt-0.5">{new Date(h.created_at).toLocaleString()}</p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : null}
      </SlideOver>

      {/* Forensic timeline drawer — layered above the detail SlideOver
          so a dispatcher can pop forensics open from the detail view
          without losing their place. */}
      <OrderForensicsDrawer
        open={!!forensicsOrderId}
        orderId={forensicsOrderId}
        onClose={() => setForensicsOrderId(null)}
      />

      {/* Create Order Modal */}
      <Modal open={showCreate} onClose={() => setShowCreate(false)} title="New Order" wide>
        <form onSubmit={(e) => void handleCreate(e)} className="space-y-5">
          {createError && <div className="border-l-2 border-l-red-500 pl-3 py-2 text-sm text-red-700">{createError}</div>}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label htmlFor="order-type" className={labelCls}>Type</label>
              <select id="order-type" value={form.type} onChange={(e) => handleTypeChange(e.target.value as OrderType)} className={inputCls}>
                <option value="ON_DEMAND">On Demand</option>
                <option value="SCHEDULED">Scheduled</option>
                <option value="BULK">Bulk</option>
                <option value="PICKUP_AND_DELIVERY">Pickup &amp; Delivery</option>
                <option value="REVERSE">Return (customer → depot)</option>
              </select>
            </div>
            <div>
              <label htmlFor="order-priority" className={labelCls}>Priority</label>
              <select id="order-priority" value={form.priority} onChange={(e) => updateForm("priority", e.target.value as OrderPriority)} className={inputCls}>
                <option value="LOW">Low</option>
                <option value="NORMAL">Normal</option>
                <option value="HIGH">High</option>
                <option value="URGENT">Urgent</option>
              </select>
            </div>
          </div>

          {/* Origin/destination mode toggle — hidden when depot_id field is policy-hidden */}
          {isFieldVisible(depotMode) && (
            <div className="flex items-center gap-1 p-0.5 bg-slate-100 rounded-lg w-fit">
              <button
                type="button"
                onClick={() => {
                  setUseCustomAddresses(false);
                  setForm((prev) => ({ ...prev, pickup_address: "", pickup_lat: 0, pickup_lon: 0, delivery_address: "", delivery_lat: 0, delivery_lon: 0 }));
                }}
                className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${!useCustomAddresses ? "bg-white shadow-sm text-slate-900" : "text-slate-500 hover:text-slate-700"}`}
              >
                From warehouse
              </button>
              <button
                type="button"
                onClick={() => {
                  setUseCustomAddresses(true);
                  setForm((prev) => ({ ...prev, depot_id: undefined, pickup_address: "", pickup_lat: 0, pickup_lon: 0, delivery_address: "", delivery_lat: 0, delivery_lon: 0 }));
                }}
                className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${useCustomAddresses ? "bg-white shadow-sm text-slate-900" : "text-slate-500 hover:text-slate-700"}`}
              >
                Custom addresses
              </button>
            </div>
          )}

          {!useCustomAddresses ? (
            <>
              <div>
                <label className={labelCls}>
                  {form.type === "REVERSE" ? "Return to depot" : "Warehouse (pickup)"} <span className="text-red-500">*</span>
                </label>
                {depotsList && depotsList.length === 0 ? (
                  <p className="mt-1 text-xs text-amber-600">
                    No depots configured. Go to <a href="/settings" className="underline">Settings → Depots</a> to add one first.
                  </p>
                ) : (
                  <select
                    value={form.depot_id ?? ""}
                    onChange={(e) => handleDepotSelect(e.target.value)}
                    className={inputCls}
                    required
                  >
                    <option value="">— Select warehouse —</option>
                    {depotsList?.filter((d) => d.is_active).map((d) => (
                      <option key={d.id} value={d.id}>{d.name}</option>
                    ))}
                  </select>
                )}
                {selectedDepot && (
                  <p className="mt-1 text-[10px] text-slate-400 truncate">{selectedDepot.address}</p>
                )}
              </div>

              <div>
                <label className={labelCls}>
                  {form.type === "REVERSE" ? "Pickup (customer address)" : "Pickup"}
                </label>
                {form.type !== "REVERSE" && selectedDepot ? (
                  <div className="flex items-center gap-2 px-3 py-2 rounded-lg border border-slate-200 bg-slate-50 text-sm">
                    <span className="text-slate-400 text-xs">WAREHOUSE</span>
                    <span className="font-medium text-slate-700">{selectedDepot.name}</span>
                  </div>
                ) : (
                  <>
                    <AddressAutocomplete label="" placeholder="Search pickup…" value={form.pickup_address} onSelect={handlePickupSelect} required />
                    {form.pickup_lat !== 0 && <p className="mt-1 text-[10px] text-slate-400 tabular-nums">{form.pickup_lat.toFixed(5)}, {form.pickup_lon.toFixed(5)}</p>}
                  </>
                )}
              </div>
              <div>
                <label className={labelCls}>
                  {form.type === "REVERSE" ? "Delivery (return to warehouse)" : "Delivery address"}
                </label>
                {form.type === "REVERSE" && selectedDepot ? (
                  <div className="flex items-center gap-2 px-3 py-2 rounded-lg border border-slate-200 bg-slate-50 text-sm">
                    <span className="text-slate-400 text-xs">WAREHOUSE</span>
                    <span className="font-medium text-slate-700">{selectedDepot.name}</span>
                  </div>
                ) : (
                  <>
                    <AddressAutocomplete label="" placeholder="Search delivery…" value={form.delivery_address} onSelect={handleDeliverySelect} required />
                    {form.delivery_lat !== 0 && <p className="mt-1 text-[10px] text-slate-400 tabular-nums">{form.delivery_lat.toFixed(5)}, {form.delivery_lon.toFixed(5)}</p>}
                  </>
                )}
              </div>
            </>
          ) : (
            <>
              <div>
                <label className={labelCls}>Pickup address</label>
                <AddressAutocomplete label="" placeholder="Search pickup…" value={form.pickup_address} onSelect={handlePickupSelect} required />
                {form.pickup_lat !== 0 && <p className="mt-1 text-[10px] text-slate-400 tabular-nums">{form.pickup_lat.toFixed(5)}, {form.pickup_lon.toFixed(5)}</p>}
              </div>
              <div>
                <label className={labelCls}>Delivery address</label>
                <AddressAutocomplete label="" placeholder="Search delivery…" value={form.delivery_address} onSelect={handleDeliverySelect} required />
                {form.delivery_lat !== 0 && <p className="mt-1 text-[10px] text-slate-400 tabular-nums">{form.delivery_lat.toFixed(5)}, {form.delivery_lon.toFixed(5)}</p>}
              </div>
            </>
          )}

          {/* Cargo */}
          <div>
            <label className={labelCls}>Package size</label>
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
              {CARGO_PRESETS.map((p) => (
                <button
                  key={p.id} type="button" onClick={() => selectCargoSize(p.id)}
                  className={`flex flex-col items-start border px-3 py-2.5 text-left transition-colors duration-150 rounded-md text-xs ${
                    cargoSize === p.id ? "border-accent-500 bg-orange-50" : "border-slate-200 hover:border-slate-300 hover:bg-slate-50"
                  }`}
                >
                  <span className="font-medium text-slate-900">{p.label}</span>
                  <span className="text-[10px] text-slate-400 mt-0.5">{p.weight_kg} kg</span>
                </button>
              ))}
              <button
                type="button" onClick={() => selectCargoSize("custom")}
                className={`flex flex-col items-start border px-3 py-2.5 text-left transition-colors duration-150 rounded-md text-xs ${
                  cargoSize === "custom" ? "border-accent-500 bg-orange-50" : "border-slate-200 hover:border-slate-300 hover:bg-slate-50"
                }`}
              >
                <span className="font-medium text-slate-900">Custom</span>
                <span className="text-[10px] text-slate-400 mt-0.5">Manual</span>
              </button>
            </div>
            {cargoSize !== "custom" && form.weight_kg != null && (
              <p className="mt-2 text-xs text-slate-500 tabular-nums">
                {form.weight_kg} kg · {form.length_cm}×{form.width_cm}×{form.height_cm} cm · {form.volume_cm3?.toLocaleString()} cm³
              </p>
            )}
            {cargoSize === "custom" && (
              <div className="grid grid-cols-4 gap-3 mt-3">
                {[
                  { label: "Weight (kg)", key: "weight_kg" as const, step: "0.1", min: "0.01" },
                  { label: "Length (cm)", key: "length_cm" as const, step: "0.1", min: "0" },
                  { label: "Width (cm)",  key: "width_cm"  as const, step: "0.1", min: "0" },
                  { label: "Height (cm)", key: "height_cm" as const, step: "0.1", min: "0" },
                ].map(({ label, key, step, min }) => (
                  <div key={key}>
                    <label className={labelCls}>{label}</label>
                    <input type="number" step={step} min={min} value={form[key] ?? ""} onChange={(e) => updateForm(key, e.target.value ? Number(e.target.value) : undefined)} className={inputCls} required={key === "weight_kg"} />
                  </div>
                ))}
              </div>
            )}
            <div className="flex items-center gap-5 mt-2">
              {isFieldVisible(fragileMode) && (
                <label className="flex items-center gap-2 text-xs text-slate-600">
                  <input type="checkbox" checked={form.is_fragile ?? false} onChange={(e) => updateForm("is_fragile", e.target.checked)} />
                  Fragile{isFieldRequired(fragileMode) ? " *" : ""}
                </label>
              )}
              {isFieldVisible(tempSensitiveMode) && (
                <label className="flex items-center gap-2 text-xs text-slate-600">
                  <input type="checkbox" checked={form.is_temp_sensitive ?? false} onChange={(e) => updateForm("is_temp_sensitive", e.target.checked)} />
                  Temp sensitive{isFieldRequired(tempSensitiveMode) ? " *" : ""}
                </label>
              )}
            </div>
            {vehicleTagsList && vehicleTagsList.length > 0 && (
              <div className="mt-3">
                <p className="text-xs font-medium text-slate-600 mb-1.5">Required vehicle tags</p>
                <div className="flex flex-wrap gap-2">
                  {vehicleTagsList.map((tag) => {
                    const selected = (form.required_vehicle_tag_ids ?? []).includes(tag.id);
                    return (
                      <button
                        key={tag.id}
                        type="button"
                        onClick={() => {
                          const current = form.required_vehicle_tag_ids ?? [];
                          updateForm(
                            "required_vehicle_tag_ids",
                            selected ? current.filter((id) => id !== tag.id) : [...current, tag.id],
                          );
                        }}
                        className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border transition-all"
                        style={
                          selected
                            ? { backgroundColor: tag.color, borderColor: tag.color, color: "#fff" }
                            : { borderColor: tag.color, color: tag.color }
                        }
                      >
                        {tag.name}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
          </div>

          {/* Pickup window — only for Pickup & Delivery */}
          {form.type === "PICKUP_AND_DELIVERY" && (
            <div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label htmlFor="earliest-pickup" className={labelCls}>Pickup opens at *</label>
                  <input id="earliest-pickup" type="datetime-local" value={form.earliest_pickup ?? ""} min={nowLocal()}
                    onChange={(e) => { updateForm("earliest_pickup", e.target.value); if (form.latest_pickup && e.target.value > form.latest_pickup) updateForm("latest_pickup", ""); }}
                    className={inputCls} required />
                </div>
                <div>
                  <label htmlFor="latest-pickup" className={labelCls}>Pickup closes at *</label>
                  <input id="latest-pickup" type="datetime-local" value={form.latest_pickup ?? ""} min={form.earliest_pickup || nowLocal()}
                    onChange={(e) => updateForm("latest_pickup", e.target.value)} className={inputCls} required />
                </div>
              </div>
              <p className="mt-1.5 text-[11px] text-slate-400">When can the courier collect the package from the sender?</p>
            </div>
          )}

          {/* Delivery window */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className={labelCls}>Earliest delivery *</label>
              <input type="datetime-local" value={form.earliest_delivery} min={nowLocal()}
                onChange={(e) => { updateForm("earliest_delivery", e.target.value); if (form.latest_delivery && e.target.value > form.latest_delivery) updateForm("latest_delivery", ""); }}
                className={inputCls} required />
            </div>
            <div>
              <label className={labelCls}>Latest deadline *</label>
              <input type="datetime-local" value={form.latest_delivery} min={form.earliest_delivery || nowLocal()}
                onChange={(e) => updateForm("latest_delivery", e.target.value)} className={inputCls} required />
            </div>
          </div>
          {form.earliest_delivery && isScheduledFuture(form.earliest_delivery) && (
            <p className="text-xs text-blue-600 border-l-2 border-l-blue-300 pl-3 py-1">
              Scheduled in {daysUntil(form.earliest_delivery)} — will be held until delivery window opens.
            </p>
          )}

          {/* Customer */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className={labelCls}>Customer email *</label>
              <input type="email" placeholder="customer@example.com" value={form.customer_email} onChange={(e) => updateForm("customer_email", e.target.value)} className={inputCls} required />
            </div>
            {isFieldVisible(customerPhoneMode) && (
              <div>
                <label htmlFor="customer-phone" className={labelCls}>Customer Phone{isFieldRequired(customerPhoneMode) ? " *" : ""}</label>
                <input id="customer-phone" type="tel" placeholder="+90 555 0123" value={form.customer_phone ?? ""} onChange={(e) => updateForm("customer_phone", e.target.value || undefined)} className={inputCls} required={isFieldRequired(customerPhoneMode)} />
              </div>
            )}
            {isFieldVisible(externalRefMode) && (
              <div>
                <label htmlFor="external-ref" className={labelCls}>Reference Number{isFieldRequired(externalRefMode) ? " *" : ""}</label>
                <input id="external-ref" type="text" placeholder="PO-1024" value={form.external_ref ?? ""} onChange={(e) => updateForm("external_ref", e.target.value || undefined)} className={inputCls} required={isFieldRequired(externalRefMode)} />
              </div>
            )}
          </div>

          {isFieldVisible(notesMode) && (
            <div>
              <label className={labelCls}>Notes{isFieldRequired(notesMode) ? " *" : ""}</label>
              <textarea value={form.notes ?? ""} onChange={(e) => updateForm("notes", e.target.value || undefined)} className={inputCls} rows={2} required={isFieldRequired(notesMode)} />
            </div>
          )}

          {activeCustomFields.length > 0 && (
            <div>
              <p className="text-[10px] uppercase tracking-wide font-medium text-slate-400 mb-3">Custom fields</p>
              <div className="grid grid-cols-2 gap-4">
                {activeCustomFields.map((field) => {
                  const value = form.custom_fields?.[field.key];
                  const lbl = <>{field.label}{field.required ? " *" : ""}</>;
                  const fieldId = `custom-field-${field.key}`;
                  if (field.type === "BOOLEAN") return (
                    <label key={field.key} className="flex items-center gap-2 text-xs text-slate-700">
                      <input type="checkbox" checked={Boolean(value)} onChange={(e) => updateCustomField(field.key, e.target.checked)} />
                      {lbl}
                    </label>
                  );
                  if (field.type === "SELECT") return (
                    <div key={field.key}>
                      <label htmlFor={fieldId} className={labelCls}>{lbl}</label>
                      <select id={fieldId} value={typeof value === "string" ? value : ""} onChange={(e) => updateCustomField(field.key, e.target.value || undefined)} className={inputCls} required={field.required}>
                        <option value="">Select…</option>
                        {field.options.map((o: string) => <option key={o} value={o}>{o}</option>)}
                      </select>
                    </div>
                  );
                  if (field.type === "DATE") return (
                    <div key={field.key}>
                      <label htmlFor={fieldId} className={labelCls}>{lbl}</label>
                      <input id={fieldId} type="date" value={typeof value === "string" ? value : ""} onChange={(e) => updateCustomField(field.key, e.target.value || undefined)} className={inputCls} required={field.required} />
                    </div>
                  );
                  if (field.type === "NUMBER") return (
                    <div key={field.key}>
                      <label htmlFor={fieldId} className={labelCls}>{lbl}</label>
                      <input id={fieldId} type="number" value={typeof value === "number" ? value : ""} onChange={(e) => updateCustomField(field.key, e.target.value ? Number(e.target.value) : undefined)} className={inputCls} required={field.required} />
                    </div>
                  );
                  return (
                    <div key={field.key}>
                      <label htmlFor={fieldId} className={labelCls}>{lbl}</label>
                      <input id={fieldId} type="text" value={typeof value === "string" ? value : ""} onChange={(e) => updateCustomField(field.key, e.target.value || undefined)} className={inputCls} required={field.required} />
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2 border-t border-slate-100">
            <Button type="button" variant="secondary" onClick={() => setShowCreate(false)}>Cancel</Button>
            <Button type="submit" variant="primary" disabled={creating}>{creating ? "Creating…" : "Create order"}</Button>
          </div>
        </form>
      </Modal>

      {/* Optimize Range Modal */}
      <Modal
        open={showOptimizeRange}
        onClose={() => {
          if (!assignBusy) setShowOptimizeRange(false);
        }}
        title="Optimize a date range"
      >
        <div className="space-y-4">
          <p className="text-sm text-slate-600">
            The solver will plan eligible orders whose delivery window
            overlaps the selected range as <strong>one</strong> problem. It can
            move orders to earlier or later days within their{" "}
            <code className="text-xs bg-slate-100 px-1 rounded">earliest_delivery</code>
            …<code className="text-xs bg-slate-100 px-1 rounded">latest_delivery</code>{" "}
            window when consolidating with nearby orders saves time.
          </p>
          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="text-xs font-medium text-slate-600">From (inclusive)</span>
              <input
                type="date"
                value={rangeFrom}
                onChange={(e) => setRangeFrom(e.target.value)}
                disabled={assignBusy}
                className="mt-1 w-full text-sm border border-slate-200 rounded-md px-2 py-1.5"
                data-testid="optimize-range-from"
              />
            </label>
            <label className="block">
              <span className="text-xs font-medium text-slate-600">To (inclusive)</span>
              <input
                type="date"
                value={rangeTo}
                onChange={(e) => setRangeTo(e.target.value)}
                disabled={assignBusy}
                className="mt-1 w-full text-sm border border-slate-200 rounded-md px-2 py-1.5"
                data-testid="optimize-range-to"
              />
            </label>
          </div>
          {rangeError && (
            <div className="border-l-2 border-l-red-500 pl-3 py-1 text-sm text-red-700">
              {rangeError}
            </div>
          )}
          {assignBusy && assignKind === "range" && assignProgress && (
            <div
              className="border-l-2 border-l-blue-500 pl-3 py-1 text-sm text-slate-700"
              data-testid="optimize-range-chunk-progress"
            >
              Solving chunk {assignProgress.chunks_completed} of{" "}
              {assignProgress.chunks_total} — long ranges are split into
              shorter windows for tractability.
            </div>
          )}
          <div className="flex justify-end gap-2 pt-3 border-t border-slate-100">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setShowOptimizeRange(false)}
              disabled={assignBusy}
            >
              Cancel
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void previewRangeOptimization()}
              disabled={assignBusy}
              data-testid="optimize-range-preview"
              title="Solve and create DRAFT routes you can inspect before committing"
            >
              {assignBusy && assignDraft
                ? assignProgress
                  ? `Previewing… (${assignProgress.chunks_completed}/${assignProgress.chunks_total})`
                  : "Previewing…"
                : "Preview plan"}
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={() => void runRangeOptimization()}
              disabled={assignBusy}
              data-testid="optimize-range-run"
            >
              {assignBusy && !assignDraft
                ? assignProgress
                  ? `Solving… (${assignProgress.chunks_completed}/${assignProgress.chunks_total})`
                  : "Starting…"
                : "Run plan"}
            </Button>
          </div>
        </div>
      </Modal>

      {/* Import CSV Modal */}
      <Modal open={showImport} onClose={() => { setShowImport(false); setImportPreview(null); setImportFile(null); setImportError(null); setImportSuccess(null); }} title="Import Orders from CSV">
        <div className="space-y-4">
          {importError && <div className="border-l-2 border-l-red-500 pl-3 py-1 text-sm text-red-700">{importError}</div>}
          {importSuccess && <div className="border-l-2 border-l-emerald-500 pl-3 py-1 text-sm text-emerald-700">{importSuccess}</div>}
          {!importPreview ? (
            <>
              <div className="border border-dashed border-slate-300 rounded-md p-8 text-center">
                <p className="text-sm text-slate-500 mb-3">Choose a CSV file</p>
                <input type="file" accept=".csv" onChange={(e) => setImportFile(e.target.files?.[0] ?? null)} className="text-xs text-slate-500" />
              </div>
              <div className="flex justify-end">
                <Button variant="primary" disabled={!importFile || importing} onClick={() => void handleImportPreview()}>
                  {importing ? "Processing…" : "Preview import"}
                </Button>
              </div>
            </>
          ) : (
            <>
              <div className="space-y-2">
                <FieldRow label="Total rows" value={importPreview.total_rows} />
                <FieldRow label="Valid" value={<span className="text-emerald-600">{importPreview.valid_rows}</span>} />
                <FieldRow label="Invalid" value={<span className="text-red-600">{importPreview.invalid_rows}</span>} />
              </div>
              <div className="flex justify-end gap-2">
                <Button variant="secondary" onClick={() => { setImportPreview(null); setImportFile(null); }}>Back</Button>
                <Button variant="primary" disabled={importing || importPreview.valid_rows === 0} onClick={() => void handleImportPublish()}>
                  {importing ? "Importing…" : `Import ${importPreview.valid_rows} orders`}
                </Button>
              </div>
            </>
          )}
        </div>
      </Modal>

      {showRouteComparison && (
        <RouteScenarioComparisonOverlay
          comparison={comparison}
          loading={comparisonLoading}
          error={comparisonError}
          selectedScenarioId={selectedScenarioId}
          preview={scenarioPreview}
          previewLoading={scenarioPreviewLoading}
          previewError={scenarioPreviewError}
          token={token}
          couriers={couriersList ?? []}
          depots={depotsList ?? []}
          selecting={applyingScenario}
          discarding={discardingComparison}
          onClose={() => setShowRouteComparison(false)}
          onDiscard={() => void discardRouteComparison()}
          onRetry={() => {
            if (comparison) void refreshRouteComparison();
            else void startRouteComparison();
          }}
          onSelectScenario={(scenarioId) => setSelectedScenarioId(scenarioId)}
          onUsePlan={() => setShowUseScenarioConfirm(true)}
        />
      )}

      <ConfirmDialog
        open={showUseScenarioConfirm}
        onCancel={() => setShowUseScenarioConfirm(false)}
        onConfirm={() => void applySelectedScenario()}
        title="Use this route plan?"
        message="This promotes the selected draft routes and deletes the other scenario drafts."
        confirmLabel="Use this plan"
        loading={applyingScenario}
      />

      <ConfirmDialog
        open={showCancelConfirm}
        onCancel={() => setShowCancelConfirm(false)}
        onConfirm={() => { setShowCancelConfirm(false); void handleCancel(); }}
        title="Cancel this order?"
        message="This will permanently cancel the order."
        confirmLabel="Cancel order"
        danger
        loading={actionLoading}
      />
    </div>
  );
}
