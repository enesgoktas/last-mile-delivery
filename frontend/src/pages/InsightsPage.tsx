import { useCallback, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Download, Fuel, Leaf, MapPin, RefreshCw, TrendingDown } from "lucide-react";
import { useAuth } from "../state/AuthContext";
import { usePolling } from "../hooks/usePolling";
import {
  getAnalyticsSummary,
  getCostSavings,
  getCourierAnalytics,
  getExceptionCenterSummary,
  getFleetUtilization,
  getOptimizationSavings,
  getSolverQuality,
} from "../api/client";
import type {
  AnalyticsSummary,
  CostSavingsResponse,
  FleetUtilizationResponse,
  OptimizationSavingsResponse,
  SolverQualityResponse,
} from "../types";
import { Tabs } from "../components/ui/Tabs";

// Friendly labels for solver scenario keys (matches backend/SCENARIO_DEFINITIONS).
const SCENARIO_LABEL: Record<string, string> = {
  human_dispatcher_baseline: "Human dispatcher",
  rich_alns_service_first: "ALNS · service-first",
  rich_alns_fuel_aware: "ALNS · fuel-aware",
  rich_alns_efficient: "ALNS · efficient fleet",
  fuel_aware_full_fleet: "OR-Tools · fuel-aware",
};
function scenarioLabel(key: string): string {
  return SCENARIO_LABEL[key] ?? key;
}

// Distinct, accessible colors for the solver mix. Cycled by index so adding
// new solver keys downstream doesn't require touching this file.
const SOLVER_MIX_COLORS = [
  "bg-indigo-500",
  "bg-emerald-500",
  "bg-amber-500",
  "bg-sky-500",
  "bg-fuchsia-500",
  "bg-rose-500",
];

const POLL = 60_000;

const TIME_RANGES = [
  { id: "today", label: "Today" },
  { id: "week",  label: "This Week" },
  { id: "month", label: "This Month" },
  { id: "all",   label: "All Time" },
] as const;
type TimeRange = (typeof TIME_RANGES)[number]["id"];

const INSIGHT_SECTION_TABS = [
  { id: "operations", label: "Operations" },
  { id: "cost", label: "Cost savings" },
  { id: "optimization", label: "Optimization gains" },
] as const;
type InsightSectionTab = (typeof INSIGHT_SECTION_TABS)[number]["id"];

// Order in which we render statuses in the breakdown — lifecycle ordered, so
// the chart reads like a funnel (early → late states).
const STATUS_DISPLAY: Array<{ key: string; label: string; barColor: string }> = [
  { key: "DRAFT",       label: "Draft",        barColor: "bg-slate-300" },
  { key: "PENDING",     label: "Pending",      barColor: "bg-amber-400" },
  { key: "ASSIGNED",    label: "Assigned",     barColor: "bg-sky-400" },
  { key: "PICKED_UP",   label: "Picked up",    barColor: "bg-sky-500" },
  { key: "IN_TRANSIT",  label: "In transit",   barColor: "bg-blue-500" },
  { key: "DELIVERED",   label: "Delivered",    barColor: "bg-emerald-500" },
  { key: "FAILED",      label: "Failed",       barColor: "bg-red-500" },
  { key: "RESCHEDULED", label: "Rescheduled",  barColor: "bg-amber-500" },
  { key: "CANCELLED",   label: "Cancelled",    barColor: "bg-slate-400" },
  { key: "EXPIRED",     label: "Expired",      barColor: "bg-slate-500" },
];

function toDateParams(range: TimeRange): { date_from?: string; date_to?: string } {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  const iso = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  if (range === "today") { const s = new Date(now); s.setHours(0, 0, 0, 0); return { date_from: iso(s) }; }
  if (range === "week")  { const s = new Date(now); s.setDate(now.getDate() - 6); s.setHours(0, 0, 0, 0); return { date_from: iso(s) }; }
  if (range === "month") { const s = new Date(now); s.setDate(now.getDate() - 29); s.setHours(0, 0, 0, 0); return { date_from: iso(s) }; }
  return {};
}

function pct(val: number | undefined) { return val !== undefined ? `${(val * 100).toFixed(1)}%` : "—"; }
function fmtInt(val: number | undefined) { return val !== undefined ? val.toLocaleString() : "—"; }
function fmtKm(val: number | undefined) { return val !== undefined ? `${val.toFixed(1)} km` : "—"; }
function fmtHours(val: number | undefined) { return val !== undefined ? `${val.toFixed(1)} h` : "—"; }

export function InsightsPage() {
  const { token } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [timeRange, setTimeRange] = useState<TimeRange>("week");
  const [exporting, setExporting] = useState(false);

  const tabParam = searchParams.get("tab");
  const sectionTab: InsightSectionTab =
    tabParam === "optimization"
      ? "optimization"
      : tabParam === "cost"
      ? "cost"
      : "operations";

  const setSectionTab = useCallback(
    (id: string) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (id === "operations") next.delete("tab");
          else next.set("tab", id);
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const dateParams = useMemo(() => toDateParams(timeRange), [timeRange]);
  const fetchSummary    = useCallback(() => getAnalyticsSummary(token!, dateParams), [token, dateParams]);
  const fetchCouriers   = useCallback(() => getCourierAnalytics(token!, dateParams), [token, dateParams]);
  const fetchFleet      = useCallback(() => getFleetUtilization(token!, dateParams), [token, dateParams]);
  const fetchSavings    = useCallback(() => getOptimizationSavings(token!, dateParams), [token, dateParams]);
  const fetchCost       = useCallback(() => getCostSavings(token!, dateParams), [token, dateParams]);
  const fetchSolverQuality = useCallback(() => getSolverQuality(token!, dateParams), [token, dateParams]);
  // Exception center is "right now" state — no date params.
  const fetchExceptions = useCallback(() => getExceptionCenterSummary(token!), [token]);

  const { data: summary,      isLoading: sl } = usePolling(fetchSummary, POLL);
  const { data: courierData,  isLoading: cl } = usePolling(fetchCouriers, POLL);
  const { data: fleet,        isLoading: fl } = usePolling(fetchFleet, POLL);
  const {
    data: savings,
    error: savingsError,
    isLoading: savingsLoading,
    refetch: refetchSavings,
  } = usePolling(fetchSavings, POLL, Boolean(token));
  const {
    data: costSavings,
    error: costError,
    isLoading: costLoading,
    refetch: refetchCost,
  } = usePolling(fetchCost, POLL, Boolean(token));
  const { data: solverQuality } = usePolling(fetchSolverQuality, POLL, Boolean(token));
  const { data: exceptions } = usePolling(fetchExceptions, POLL);

  const opsLoading = (sl && !summary) || (cl && !courierData) || (fl && !fleet);
  const rows = useMemo(
    () => [...(courierData?.rows ?? [])].sort((a, b) => b.delivered_orders - a.delivered_orders),
    [courierData],
  );

  const breakdown = summary?.status_breakdown;
  const visibleStatuses = useMemo(() => {
    if (!breakdown) return [];
    return STATUS_DISPLAY.filter((s) => (breakdown[s.key as keyof typeof breakdown] ?? 0) > 0);
  }, [breakdown]);

  const exceptionCounts = exceptions?.counts;

  function handleExport() {
    if (!summary || exporting) return;
    try {
      setExporting(true);
      const rangeLabel = TIME_RANGES.find((r) => r.id === timeRange)?.label ?? timeRange;
      const csv = buildInsightsReportCsv({
        rangeLabel,
        summary,
        fleet,
        exceptionCounts,
        courierRows: rows,
        savings,
      });
      const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
      a.download = `insights-report-${timeRange}-${stamp}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error("Insights report export failed", err);
    } finally {
      setExporting(false);
    }
  }

  return (
    <div className="flex-1 overflow-y-auto thin-scrollbar">
      {/* Header */}
      <div className="px-6 pt-5 pb-0 border-b border-slate-200">
        <div className="flex items-center justify-between mb-3">
          <h1 className="text-base font-semibold text-slate-900">Insights</h1>
          <button
            type="button"
            onClick={handleExport}
            disabled={exporting || !summary || (summary?.total_orders ?? 0) === 0}
            className="inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1.5 rounded-md border border-slate-200 text-slate-700 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            title="Download a combined report (Operations + Optimization gains) for the selected time range"
          >
            <Download size={13} />
            {exporting ? "Exporting…" : "Export report"}
          </button>
        </div>
        <Tabs
          tabs={INSIGHT_SECTION_TABS as unknown as { id: string; label: string }[]}
          active={sectionTab}
          onChange={setSectionTab}
        />
        <div className="mt-1">
          <p className="text-[10px] font-medium uppercase tracking-wide text-slate-400 mb-1">Time range</p>
          <Tabs tabs={TIME_RANGES as unknown as { id: string; label: string }[]} active={timeRange} onChange={(v) => setTimeRange(v as TimeRange)} />
        </div>
      </div>

      {sectionTab === "cost" ? (
        <div className="px-6 py-6 space-y-4">
          <p className="text-sm text-slate-600 max-w-2xl">
            Total fuel cost (TRY) burned by your fleet over the selected range,
            and how much the solver saved versus a hand-built baseline.
            CO₂ is derived from fuel volume × the vehicle's emissions factor.
          </p>
          <CostSavingsPanel
            cost={costSavings}
            loading={costLoading}
            error={costError}
            onRetry={() => void refetchCost()}
          />
        </div>
      ) : sectionTab === "optimization" ? (
        <div className="px-6 py-6 space-y-6">
          <p className="text-sm text-slate-600 max-w-2xl">
            Solver-quality lenses that apply to every real run — ETA accuracy,
            failure causes, plan stability. The legacy scenario-comparison
            panel below covers manual A/B runs you triggered from Compare
            routes.
          </p>
          {solverQuality && <SolverQualityPanel data={solverQuality} />}
          <div className="space-y-2">
            <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">
              Manual scenario comparisons
            </h3>
            <OptimizationGainsPanel
              savings={savings}
              loading={savingsLoading}
              error={savingsError}
              onRetry={() => void refetchSavings()}
            />
          </div>
        </div>
      ) : opsLoading ? (
        <div className="px-6 py-6 space-y-6">
          {/* KPI skeleton */}
          <div className="flex gap-8">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="space-y-2">
                <div className="h-7 w-14 bg-slate-100 rounded animate-pulse" />
                <div className="h-3 w-10 bg-slate-100 rounded animate-pulse" />
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="px-6 py-6 space-y-8">
          {/* KPI row */}
          <div className="flex items-end gap-10 border-b border-slate-100 pb-6">
            <div>
              <span className="text-3xl font-bold tabular-nums text-slate-900 tracking-tight">{summary?.total_orders ?? "—"}</span>
              <p className="mt-1 text-xs font-medium uppercase tracking-wide text-slate-400">Total orders</p>
            </div>
            <div>
              <span className="text-2xl font-bold tabular-nums text-emerald-600 tracking-tight">{summary?.delivered_orders ?? "—"}</span>
              <p className="mt-1 text-xs font-medium uppercase tracking-wide text-slate-400">Delivered</p>
              {summary && <p className="text-[10px] text-slate-400 mt-0.5">{pct(summary.completion_rate)}</p>}
            </div>
            <div>
              <span className={`text-2xl font-bold tabular-nums tracking-tight ${summary && summary.failed_orders > 0 ? "text-red-600" : "text-slate-600"}`}>{summary?.failed_orders ?? "—"}</span>
              <p className="mt-1 text-xs font-medium uppercase tracking-wide text-slate-400">Failed</p>
              {summary && <p className="text-[10px] text-slate-400 mt-0.5">{pct(summary.failed_rate)}</p>}
            </div>
            <div>
              <span className="text-2xl font-bold tabular-nums text-slate-700 tracking-tight">{summary ? pct(summary.on_time_rate) : "—"}</span>
              <p className="mt-1 text-xs font-medium uppercase tracking-wide text-slate-400">On-time rate</p>
            </div>
            <div>
              <span className="text-2xl font-bold tabular-nums text-slate-500 tracking-tight">{summary?.pending_orders ?? "—"}</span>
              <p className="mt-1 text-xs font-medium uppercase tracking-wide text-slate-400">Pending</p>
            </div>
          </div>

          {/* Fleet health strip — derived from /fleet-utilization */}
          {fleet && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-6 border-b border-slate-100 pb-6">
              <div>
                <span className="text-xl font-semibold tabular-nums text-slate-900">{fmtInt(fleet.rows.length)}</span>
                <p className="mt-1 text-xs font-medium uppercase tracking-wide text-slate-400">Active couriers</p>
              </div>
              <div>
                <span className="text-xl font-semibold tabular-nums text-slate-900">{fmtInt(fleet.total_route_count)}</span>
                <p className="mt-1 text-xs font-medium uppercase tracking-wide text-slate-400">Routes</p>
              </div>
              <div>
                <span className="text-xl font-semibold tabular-nums text-slate-900">{fmtKm(fleet.total_distance_km)}</span>
                <p className="mt-1 text-xs font-medium uppercase tracking-wide text-slate-400">Distance driven</p>
              </div>
              <div>
                <span className="text-xl font-semibold tabular-nums text-slate-900">{fmtHours(fleet.total_hours)}</span>
                <p className="mt-1 text-xs font-medium uppercase tracking-wide text-slate-400">Hours logged</p>
              </div>
            </div>
          )}

          {/* Operations summary card — exception center counts */}
          {exceptionCounts && (
            <div className="border border-slate-200 rounded-md p-4">
              <div className="flex items-center justify-between mb-3">
                <p className="text-xs font-medium uppercase tracking-wide text-slate-400">Operations · right now</p>
                {exceptionCounts.total_open_items > 0 && (
                  <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-amber-50 text-amber-700">
                    {exceptionCounts.total_open_items} open
                  </span>
                )}
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                <OpItem
                  label="Pending approvals"
                  value={exceptionCounts.pending_approval_runs}
                  tone={exceptionCounts.pending_approval_runs > 0 ? "amber" : "neutral"}
                />
                <OpItem
                  label="Critical alerts"
                  value={exceptionCounts.critical_alerts}
                  tone={exceptionCounts.critical_alerts > 0 ? "red" : "neutral"}
                />
                <OpItem
                  label="Unassigned orders"
                  value={exceptionCounts.unassigned_orders}
                  tone={exceptionCounts.unassigned_orders > 0 ? "amber" : "neutral"}
                />
                <OpItem
                  label="Failed orders"
                  value={exceptionCounts.failed_orders}
                  tone={exceptionCounts.failed_orders > 0 ? "red" : "neutral"}
                />
              </div>
            </div>
          )}

          {/* Order breakdown — full status lifecycle */}
          {summary && visibleStatuses.length > 0 && (
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-slate-400 mb-4">Order breakdown</p>
              <div className="space-y-3">
                {visibleStatuses.map(({ key, label, barColor }) => {
                  const value = breakdown?.[key as keyof typeof breakdown] ?? 0;
                  const p = summary.total_orders > 0 ? (value / summary.total_orders) * 100 : 0;
                  return (
                    <div key={key}>
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-sm text-slate-700">{label}</span>
                        <span className="text-sm font-medium text-slate-900 tabular-nums">
                          {fmtInt(value)} <span className="text-slate-400 text-xs">({p.toFixed(1)}%)</span>
                        </span>
                      </div>
                      <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                        <div className={`h-full ${barColor} transition-all duration-500`} style={{ width: `${Math.min(p, 100)}%` }} />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Performance rates */}
          {summary && (
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-slate-400 mb-4">Performance rates</p>
              <div className="space-y-5">
                {[
                  { label: "Completion rate", value: summary.completion_rate, target: 0.95, barColor: "bg-emerald-500", invert: false },
                  { label: "On-time rate",    value: summary.on_time_rate,    target: 0.95, barColor: "bg-blue-500",    invert: false },
                  { label: "Failure rate",    value: summary.failed_rate,     target: 0.05, barColor: "bg-red-500",     invert: true },
                ].map(({ label, value, target, barColor, invert }) => {
                  const onTarget = invert ? value <= target : value >= target;
                  return (
                    <div key={label}>
                      <div className="flex items-center justify-between mb-1.5">
                        <span className="text-sm text-slate-700">{label}</span>
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium text-slate-900 tabular-nums">{(value * 100).toFixed(1)}%</span>
                          <span className={`text-[10px] ${onTarget ? "text-emerald-600" : "text-amber-600"}`}>
                            {onTarget ? "on target" : "off target"}
                          </span>
                        </div>
                      </div>
                      <div className="h-2 bg-slate-100 rounded-full overflow-hidden relative">
                        <div className={`h-full ${barColor} transition-all duration-500`} style={{ width: `${Math.min(value * 100, 100)}%` }} />
                        <div className="absolute top-0 h-full w-px bg-slate-400/60" style={{ left: `${target * 100}%` }} />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Courier performance table */}
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-slate-400 mb-4">Courier performance</p>
            {rows.length === 0 ? (
              <p className="text-sm text-slate-400">No courier data for this period.</p>
            ) : (
              <table className="w-full text-sm border-collapse">
                <thead>
                  <tr className="border-b border-slate-200">
                    <th className="py-2.5 px-3 text-left text-xs font-medium uppercase tracking-wide text-slate-400">Courier</th>
                    <th className="py-2.5 px-3 text-right text-xs font-medium uppercase tracking-wide text-slate-400">Assigned</th>
                    <th className="py-2.5 px-3 text-right text-xs font-medium uppercase tracking-wide text-slate-400">Delivered</th>
                    <th className="py-2.5 px-3 text-right text-xs font-medium uppercase tracking-wide text-slate-400">Failed</th>
                    <th className="py-2.5 px-3 text-right text-xs font-medium uppercase tracking-wide text-slate-400">Rate</th>
                    <th className="py-2.5 px-3 w-24"></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => {
                    const rate = row.assigned_orders > 0 ? (row.delivered_orders / row.assigned_orders) * 100 : 0;
                    return (
                      <tr key={row.courier_id} className="border-b border-slate-100 hover:bg-slate-50 transition-colors duration-100">
                        <td className="py-2.5 px-3 font-mono text-xs text-slate-600">{row.courier_id.slice(0, 8)}</td>
                        <td className="py-2.5 px-3 text-right text-slate-700 tabular-nums">{row.assigned_orders}</td>
                        <td className="py-2.5 px-3 text-right text-emerald-600 font-medium tabular-nums">{row.delivered_orders}</td>
                        <td className="py-2.5 px-3 text-right text-red-500 tabular-nums">{row.failed_orders}</td>
                        <td className="py-2.5 px-3 text-right text-slate-700 tabular-nums">{rate > 0 ? `${rate.toFixed(1)}%` : "—"}</td>
                        <td className="py-2.5 px-3">
                          <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
                            <div className="h-full bg-emerald-500" style={{ width: `${Math.min(rate, 100)}%` }} />
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/** Extra context when the optimization-savings route is missing (HTTP 404). */
function optimizationGainsNotFoundHint(error: string | null): string | null {
  if (error == null) return null;
  if (!/\bnot found\b|^404\b/i.test(error.trim())) return null;
  return (
    "Your UI is calling /api/v1/analytics/optimization-savings but the running API does not expose " +
    "that route yet. With Docker Compose the API runs uvicorn without --reload, so new routes only " +
    "appear after a restart: from the backend/ folder run docker compose restart api (or recreate the api " +
    "service). Also confirm VITE_API_BASE_URL points at the same backend you're developing."
  );
}

// ─── Solver quality (Layer 2/3 cards) ─────────────────────────────────
// Three lenses on solver/operations quality, surfaced beneath the
// scenario-comparison hero in the Optimization-gains tab:
//   * ETA accuracy — mean abs error + p95 + within-window hit rate
//   * Failure causes — typed breakdown of FAILED orders in the range
//   * Plan stability — rolling stability_pct from re-runs
// All three read from data the Layer 0/2b/3 instrumentation captures
// automatically. Empty states are explicit so the dashboard doesn't
// lie when the relevant instrumentation hasn't fired yet.

function SolverQualityPanel({ data }: { data: SolverQualityResponse }) {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <ETAAccuracyCard data={data.eta_accuracy} />
      <FailureCausesCard data={data.failure_causes} />
      <PlanStabilityCard data={data.plan_stability} />
    </div>
  );
}

function fmtSeconds(s: number | null): string {
  if (s == null) return "—";
  const abs = Math.abs(s);
  if (abs < 60) return `${Math.round(s)}s`;
  if (abs < 3600) return `${(s / 60).toFixed(s < 0 ? 1 : 1)}m`;
  return `${(s / 3600).toFixed(1)}h`;
}

function ETAAccuracyCard({ data }: { data: SolverQualityResponse["eta_accuracy"] }) {
  const empty = data.predictions === 0;
  const otRate = data.on_time_rate;
  const otWindowMin = Math.round(data.on_time_window_s / 60);
  return (
    <div className="border border-slate-200 rounded-md p-5 flex flex-col">
      <div className="flex items-center justify-between mb-3">
        <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
          ETA accuracy
        </p>
        <span className="text-[10px] text-slate-400 tabular-nums">
          {data.predictions} predictions
        </span>
      </div>
      {empty ? (
        <p className="text-sm text-slate-500 flex-1">
          Waiting on completed deliveries. ETAs are logged at solve time
          and reconciled when stops complete; this card lights up after
          the first arrivals come in.
        </p>
      ) : (
        <>
          <div className="mb-4">
            <p className="text-3xl font-semibold tabular-nums tracking-tight text-slate-900">
              {otRate != null ? `${(otRate * 100).toFixed(1)}%` : "—"}
            </p>
            <p className="mt-1 text-xs text-slate-500">
              within ±{otWindowMin} min of predicted
            </p>
          </div>
          <dl className="grid grid-cols-2 gap-3 text-xs">
            <div>
              <dt className="text-slate-500">Mean abs error</dt>
              <dd className="text-sm font-medium text-slate-800 tabular-nums">
                {fmtSeconds(data.mean_abs_error_s)}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">p95 abs error</dt>
              <dd className="text-sm font-medium text-slate-800 tabular-nums">
                {fmtSeconds(data.p95_abs_error_s)}
              </dd>
            </div>
            <div className="col-span-2">
              <dt className="text-slate-500">Mean signed error</dt>
              <dd
                className={`text-sm font-medium tabular-nums ${
                  data.mean_error_signed_s == null
                    ? "text-slate-400"
                    : data.mean_error_signed_s > 120
                    ? "text-red-600"
                    : data.mean_error_signed_s < -120
                    ? "text-sky-600"
                    : "text-emerald-600"
                }`}
              >
                {data.mean_error_signed_s == null
                  ? "—"
                  : data.mean_error_signed_s >= 0
                  ? `+${fmtSeconds(data.mean_error_signed_s)} late`
                  : `${fmtSeconds(Math.abs(data.mean_error_signed_s))} early`}
              </dd>
            </div>
          </dl>
        </>
      )}
    </div>
  );
}

// Hex colours (not tailwind bg-* classes) so we can paint the SVG donut
// segments directly. Keep the legend dots aligned via the same hexes.
const FAILURE_CATEGORY_HEX: Record<string, string> = {
  CUSTOMER: "#fbbf24", // amber-400
  ADDRESS: "#fb923c", // orange-400
  COURIER: "#f43f5e", // rose-500
  VEHICLE: "#ef4444", // red-500
  WEATHER: "#38bdf8", // sky-400
  SYSTEM: "#d946ef", // fuchsia-500
  OTHER: "#94a3b8", // slate-400
  UNKNOWN: "#cbd5e1", // slate-300
};

function FailureCausesCard({
  data,
}: {
  data: SolverQualityResponse["failure_causes"];
}) {
  const empty = data.total_failures === 0;
  return (
    <div className="border border-slate-200 rounded-md p-5 flex flex-col">
      <div className="flex items-center justify-between mb-3">
        <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
          Failure causes
        </p>
        <span className="text-[10px] text-slate-400 tabular-nums">
          {data.total_failures} failed
        </span>
      </div>
      {empty ? (
        <p className="text-sm text-slate-500 flex-1">
          No failed orders in this range — clean run.
        </p>
      ) : (
        <div className="flex items-center gap-4">
          {/* Donut */}
          <FailureDonut rows={data.by_category} total={data.total_failures} />
          {/* Legend */}
          <ul className="flex-1 space-y-1 text-xs min-w-0">
            {data.by_category.map((row) => {
              const color =
                FAILURE_CATEGORY_HEX[row.category] ??
                FAILURE_CATEGORY_HEX.OTHER;
              return (
                <li
                  key={row.category}
                  className="flex items-center justify-between gap-2"
                >
                  <span className="flex items-center gap-1.5 min-w-0">
                    <span
                      className="inline-block w-2 h-2 rounded-full shrink-0"
                      style={{ backgroundColor: color }}
                    />
                    <span className="text-slate-700 font-medium capitalize truncate">
                      {row.category.toLowerCase()}
                    </span>
                  </span>
                  <span className="text-slate-500 tabular-nums shrink-0">
                    {row.count}
                    <span className="text-slate-400 ml-1">
                      ({(row.share * 100).toFixed(0)}%)
                    </span>
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}

function FailureDonut({
  rows,
  total,
}: {
  rows: SolverQualityResponse["failure_causes"]["by_category"];
  total: number;
}) {
  const size = 96;
  const stroke = 18;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        className="-rotate-90"
      >
        {rows.map((row) => {
          const color =
            FAILURE_CATEGORY_HEX[row.category] ?? FAILURE_CATEGORY_HEX.OTHER;
          const len = row.share * circumference;
          const seg = (
            <circle
              key={row.category}
              cx={size / 2}
              cy={size / 2}
              r={radius}
              stroke={color}
              strokeWidth={stroke}
              fill="none"
              strokeDasharray={`${len} ${circumference - len}`}
              strokeDashoffset={-offset}
            />
          );
          offset += len;
          return seg;
        })}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-lg font-semibold tabular-nums text-slate-900">
          {total}
        </span>
        <span className="text-[9px] uppercase tracking-wide text-slate-400">
          failed
        </span>
      </div>
    </div>
  );
}

function PlanStabilityCard({
  data,
}: {
  data: SolverQualityResponse["plan_stability"];
}) {
  const empty = data.samples === 0;
  const recent = data.recent;
  const avg = data.avg_stability_pct;
  const SPARK_H = 56;
  return (
    <div className="border border-slate-200 rounded-md p-5 flex flex-col">
      <div className="flex items-center justify-between mb-3">
        <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
          Plan stability
        </p>
        <span className="text-[10px] text-slate-400 tabular-nums">
          {data.samples} re-runs
        </span>
      </div>
      {empty ? (
        <p className="text-sm text-slate-500 flex-1">
          Stability is computed when the range planner re-runs over an
          overlapping date range. This card fills in once you've run
          range-optimization twice on the same window.
        </p>
      ) : (
        <>
          <div className="mb-4">
            <p
              className={`text-3xl font-semibold tabular-nums tracking-tight ${
                avg == null
                  ? "text-slate-400"
                  : avg >= 0.85
                  ? "text-emerald-600"
                  : avg >= 0.7
                  ? "text-amber-600"
                  : "text-red-600"
              }`}
            >
              {avg != null ? `${(avg * 100).toFixed(0)}%` : "—"}
            </p>
            <p className="mt-1 text-xs text-slate-500">
              avg of {data.samples} most recent re-runs
            </p>
          </div>
          {recent.length > 1 && (
            <div className="flex items-end gap-0.5 h-[56px] mt-auto">
              {recent.map((r, i) => {
                const v = r.stability_pct ?? 0;
                const h = Math.max(2, v * SPARK_H);
                const tone =
                  v >= 0.85
                    ? "bg-emerald-400"
                    : v >= 0.7
                    ? "bg-amber-400"
                    : "bg-red-400";
                return (
                  <div
                    key={i}
                    className={`${tone} flex-1 rounded-t-sm transition-all`}
                    style={{ height: h }}
                    title={`${(v * 100).toFixed(0)}% · ${r.orders_evaluated} orders`}
                  />
                );
              })}
            </div>
          )}
        </>
      )}
    </div>
  );
}


// ─── Cost savings ───────────────────────────────────────────────────────────
// The pilot's headline KPI surface. Fuel cost in TRY (primary), CO₂ in kg
// (derived secondary), per-day stacked bars, and counterfactual savings vs
// the baseline solver. Reads from materialized `route_performance_snapshots`
// via /api/v1/analytics/cost-savings — fast and cheap as the route table
// grows.

function CostSavingsPanel({
  cost,
  loading,
  error,
  onRetry,
}: {
  cost: CostSavingsResponse | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}) {
  return (
    <div className="border border-slate-200 rounded-md p-5">
      <div className="flex items-center justify-between gap-3 mb-3">
        <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
          Cost savings · fuel + CO₂
        </p>
        {error != null && (
          <button
            type="button"
            onClick={onRetry}
            className="inline-flex items-center gap-1 text-[11px] font-medium text-slate-600 hover:text-slate-900 px-2 py-1 rounded border border-slate-200 hover:bg-slate-50"
          >
            <RefreshCw size={12} />
            Retry
          </button>
        )}
      </div>
      {error != null && (
        <p
          className={`text-xs mb-3 ${cost != null ? "text-amber-700" : "text-red-600"}`}
        >
          {cost != null ? "Could not refresh:" : "Could not load:"} {error}
        </p>
      )}
      {loading && cost == null && (
        <div className="space-y-3 animate-pulse" aria-busy="true" aria-label="Loading cost savings">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="space-y-2">
                <div className="h-7 bg-slate-100 rounded w-28" />
                <div className="h-3 bg-slate-100 rounded w-16" />
              </div>
            ))}
          </div>
          <div className="h-32 bg-slate-100 rounded" />
        </div>
      )}
      {cost != null && <CostSavingsSection cost={cost} />}
    </div>
  );
}

function fmtTRY(value: number): string {
  return `₺${value.toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  })}`;
}

function fmtCO2(value: number): string {
  if (value >= 1000) return `${(value / 1000).toFixed(2)} t`;
  return `${value.toFixed(1)} kg`;
}

function CostSavingsSection({ cost }: { cost: CostSavingsResponse }) {
  const { totals, per_day, routes_included } = cost;

  if (routes_included === 0) {
    return (
      <div className="py-10 text-center">
        <Fuel size={28} className="mx-auto text-slate-300" />
        <p className="mt-3 text-sm font-medium text-slate-700">
          No completed routes in this range
        </p>
        <p className="mt-1 text-xs text-slate-500 max-w-md mx-auto leading-relaxed">
          Cost snapshots materialize when routes complete in production.
          Seed the demo with{" "}
          <code className="text-[11px] bg-slate-100 px-1 py-0.5 rounded">
            python -m app.seed_insights_demo --force
          </code>
          .
        </p>
      </div>
    );
  }

  const fuelSaved = totals.fuel_cost_saved_try ?? 0;
  const baselineCost =
    totals.fuel_cost_saved_try != null
      ? totals.total_fuel_cost_try + totals.fuel_cost_saved_try
      : null;
  const savedPct =
    fuelSaved > 0 && baselineCost && baselineCost > 0
      ? (fuelSaved / baselineCost) * 100
      : null;
  const co2Saved =
    totals.distance_saved_km && totals.distance_saved_km > 0
      ? (totals.distance_saved_km /
          Math.max(totals.total_distance_km, 0.0001)) *
        totals.total_co2_kg
      : null;
  // A mature tree absorbs ~22 kg CO₂ per year (EPA). The "trees / year"
  // equivalent reads more tangibly than a kg number for ESG storytelling.
  const treesEquivalent = Math.round(totals.total_co2_kg / 22);
  const treesSaved =
    co2Saved && co2Saved > 0 ? Math.round(co2Saved / 22) : null;

  const maxCost = per_day.reduce(
    (m, d) =>
      Math.max(
        m,
        d.total_fuel_cost_try + Math.max(0, d.fuel_cost_saved_try ?? 0),
      ),
    0,
  );
  // Chart geometry — generous heights so daily bars carry real visual
  // weight. Five gridlines at 0/25/50/75/100% of `maxCost`.
  const CHART_H = 196;
  const GRIDLINES = [1, 0.75, 0.5, 0.25, 0];

  return (
    <div className="space-y-4">
      {/* Hero — savings is the headline. Two-column grid: BIG number
          on the left, daily bars on the right. */}
      <div className="grid grid-cols-1 lg:grid-cols-[minmax(260px,360px)_minmax(0,1fr)] gap-4">
        {/* Saved hero */}
        <div className="rounded-lg border border-emerald-200 bg-gradient-to-br from-emerald-50 to-white p-5">
          <div className="flex items-center gap-1.5 text-emerald-700">
            <TrendingDown size={14} />
            <p className="text-[11px] font-medium uppercase tracking-wide">
              Fuel saved this period
            </p>
          </div>
          <p className="mt-2 text-5xl font-semibold tabular-nums tracking-tight text-emerald-700">
            {fuelSaved > 0 ? fmtTRY(fuelSaved) : "—"}
          </p>
          {savedPct != null ? (
            <p className="mt-1.5 text-sm text-emerald-700/80">
              <span className="font-semibold tabular-nums">
                ↓ {savedPct.toFixed(1)}%
              </span>{" "}
              vs hand-built baseline
            </p>
          ) : (
            <p className="mt-1.5 text-sm text-slate-500">
              Baseline not recorded for these runs yet.
            </p>
          )}
          {baselineCost != null && (
            <div className="mt-4 pt-3 border-t border-emerald-100 grid grid-cols-2 gap-3">
              <div>
                <p className="text-[10px] uppercase tracking-wide text-slate-500">
                  Baseline cost
                </p>
                <p className="text-sm font-semibold text-slate-700 tabular-nums">
                  {fmtTRY(baselineCost)}
                </p>
              </div>
              <div>
                <p className="text-[10px] uppercase tracking-wide text-slate-500">
                  Actual cost
                </p>
                <p className="text-sm font-semibold text-slate-700 tabular-nums">
                  {fmtTRY(totals.total_fuel_cost_try)}
                </p>
              </div>
            </div>
          )}
        </div>

        {/* Daily chart */}
        <div className="rounded-lg border border-slate-200 bg-white p-5">
          <div className="flex items-center justify-between mb-3">
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wide text-slate-500">
                Daily fuel cost
              </p>
              <p className="text-[10px] text-slate-400">
                {routes_included}{" "}
                {routes_included === 1 ? "route" : "routes"} ·{" "}
                {totals.total_deliveries} deliveries
              </p>
            </div>
            <div className="flex items-center gap-3 text-[10px] text-slate-500">
              <span className="flex items-center gap-1">
                <span className="inline-block w-2.5 h-2.5 rounded-sm bg-orange-500 ring-1 ring-orange-600/30" />
                actual
              </span>
              <span className="flex items-center gap-1">
                <span className="inline-block w-2.5 h-2.5 rounded-sm bg-emerald-300 ring-1 ring-emerald-500/30" />
                saved
              </span>
            </div>
          </div>
          {per_day.length === 0 ? (
            <p className="text-xs text-slate-500 py-8 text-center">
              No daily data yet.
            </p>
          ) : (
            <div className="relative" style={{ height: CHART_H + 28 }}>
              {/* Gridlines + axis labels */}
              <div className="absolute inset-x-12 top-0" style={{ height: CHART_H }}>
                {GRIDLINES.map((frac) => (
                  <div
                    key={frac}
                    className="absolute inset-x-0 border-t border-slate-100"
                    style={{ top: CHART_H * (1 - frac) }}
                  />
                ))}
              </div>
              {/* y-axis labels */}
              <div
                className="absolute left-0 top-0 w-10 text-[9px] text-slate-400 tabular-nums"
                style={{ height: CHART_H }}
              >
                {GRIDLINES.map((frac) => (
                  <span
                    key={frac}
                    className="absolute right-0 -translate-y-1/2"
                    style={{ top: CHART_H * (1 - frac) }}
                  >
                    ₺{Math.round(maxCost * frac)}
                  </span>
                ))}
              </div>
              {/* Bars */}
              <div
                className="absolute inset-x-12 top-0 flex items-end justify-between gap-1.5"
                style={{ height: CHART_H }}
              >
                {per_day.map((d) => {
                  const actual = d.total_fuel_cost_try;
                  const saved = Math.max(0, d.fuel_cost_saved_try ?? 0);
                  const scale = (v: number) =>
                    maxCost > 0 ? (v / maxCost) * CHART_H : 0;
                  const actualPx = Math.max(actual > 0 ? 4 : 0, scale(actual));
                  const savedPx = Math.max(saved > 0 ? 4 : 0, scale(saved));
                  return (
                    <div
                      key={d.date}
                      className="group flex flex-1 flex-col items-center"
                      style={{ height: CHART_H }}
                    >
                      <div
                        className="flex w-full max-w-[3.25rem] flex-col-reverse overflow-hidden rounded-sm"
                        style={{ height: CHART_H }}
                      >
                        <div
                          className="bg-orange-500 transition-all group-hover:bg-orange-600 ring-1 ring-orange-600/20"
                          style={{ height: actualPx }}
                          title={`${d.date} · actual ₺${actual.toFixed(0)} · ${d.total_fuel_liters.toFixed(1)} L · ${d.total_distance_km.toFixed(0)} km`}
                        />
                        {saved > 0 && (
                          <div
                            className="bg-emerald-300 transition-all group-hover:bg-emerald-400 ring-1 ring-emerald-500/20"
                            style={{ height: savedPx }}
                            title={`${d.date} · saved ₺${saved.toFixed(0)} vs baseline`}
                          />
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
              {/* x-axis labels */}
              <div
                className="absolute inset-x-12 flex justify-between gap-1.5 text-[10px] text-slate-500 tabular-nums"
                style={{ top: CHART_H + 8 }}
              >
                {per_day.map((d) => (
                  <span
                    key={d.date}
                    className="flex-1 text-center font-medium"
                  >
                    {d.date.slice(5)}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Supporting metrics rail */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <SupportingMetric
          icon={<Fuel size={13} />}
          label="Fuel burned"
          primary={fmtTRY(totals.total_fuel_cost_try)}
          secondary={`${totals.total_fuel_liters.toFixed(0)} L`}
        />
        <SupportingMetric
          icon={<MapPin size={13} />}
          label="Distance"
          primary={`${totals.total_distance_km.toLocaleString(undefined, {
            maximumFractionDigits: 0,
          })} km`}
          secondary={
            totals.distance_saved_km && totals.distance_saved_km > 0
              ? `↓ ${totals.distance_saved_km.toFixed(0)} km vs baseline`
              : `across ${totals.total_routes} routes`
          }
        />
        <SupportingMetric
          label="Cost / delivery"
          primary={
            totals.cost_per_delivery_try != null
              ? fmtTRY(totals.cost_per_delivery_try)
              : "—"
          }
          secondary={
            totals.cost_per_km_try != null
              ? `${fmtTRY(totals.cost_per_km_try)} / km`
              : `${totals.total_deliveries} deliveries`
          }
        />
        <SupportingMetric
          icon={<Leaf size={13} />}
          label="CO₂ emitted"
          primary={fmtCO2(totals.total_co2_kg)}
          secondary={
            co2Saved && co2Saved > 0
              ? `↓ ${fmtCO2(co2Saved)} vs baseline`
              : `≈ ${treesEquivalent} trees / yr`
          }
        />
      </div>

      {/* Sustainability banner — makes the abstract CO₂ tangible */}
      {totals.total_co2_kg > 0 && (
        <div className="rounded-lg border border-slate-200 bg-gradient-to-r from-slate-50 to-white p-4 flex items-center gap-4">
          <span className="rounded-full bg-emerald-100 p-2 text-emerald-700">
            <Leaf size={16} />
          </span>
          <div className="flex-1">
            <p className="text-sm text-slate-700">
              Your fleet's footprint:{" "}
              <span className="font-semibold tabular-nums">
                {fmtCO2(totals.total_co2_kg)}
              </span>{" "}
              of CO₂ — what{" "}
              <span className="font-semibold tabular-nums">
                {treesEquivalent.toLocaleString()}
              </span>{" "}
              mature trees absorb in a year.
            </p>
            {treesSaved != null && treesSaved > 0 && (
              <p className="text-xs text-emerald-700 mt-0.5">
                Optimization avoided ≈{" "}
                <span className="font-semibold">{treesSaved}</span> trees'
                worth of CO₂ this period.
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function SupportingMetric({
  icon,
  label,
  primary,
  secondary,
}: {
  icon?: React.ReactNode;
  label: string;
  primary: string;
  secondary: string;
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3">
      <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wide text-slate-500">
        {icon && <span className="text-slate-400">{icon}</span>}
        <span>{label}</span>
      </div>
      <p className="mt-1 text-lg font-semibold tabular-nums tracking-tight text-slate-900">
        {primary}
      </p>
      <p className="text-[11px] text-slate-500 tabular-nums">{secondary}</p>
    </div>
  );
}

function OptimizationGainsPanel({
  savings,
  loading,
  error,
  onRetry,
}: {
  savings: OptimizationSavingsResponse | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}) {
  const notFoundHint = optimizationGainsNotFoundHint(error);

  return (
    <div className="border border-slate-200 rounded-md p-5">
      <div className="flex items-center justify-between gap-3 mb-3">
        <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
          Optimization gains
        </p>
        {error != null && (
          <button
            type="button"
            onClick={onRetry}
            className="inline-flex items-center gap-1 text-[11px] font-medium text-slate-600 hover:text-slate-900 px-2 py-1 rounded border border-slate-200 hover:bg-slate-50"
          >
            <RefreshCw size={12} />
            Retry
          </button>
        )}
      </div>
      {error != null && (
        <div className="mb-3 space-y-2">
          <p className={`text-xs ${savings != null ? "text-amber-700" : "text-red-600"}`}>
            {savings != null ? "Could not refresh:" : "Could not load:"} {error}
          </p>
          {notFoundHint != null ? (
            <p className="text-xs text-slate-500 leading-relaxed">{notFoundHint}</p>
          ) : null}
        </div>
      )}
      {loading && savings == null && (
        <div className="space-y-3 animate-pulse" aria-busy="true" aria-label="Loading optimization gains">
          <div className="h-4 bg-slate-100 rounded w-2/3" />
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="space-y-2">
                <div className="h-6 bg-slate-100 rounded w-20" />
                <div className="h-3 bg-slate-100 rounded w-16" />
              </div>
            ))}
          </div>
        </div>
      )}
      {savings != null && <OptimizationGainsSection savings={savings} />}
    </div>
  );
}

function OpItem({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "neutral" | "amber" | "red";
}) {
  const valueColor =
    tone === "red" ? "text-red-600" : tone === "amber" ? "text-amber-600" : "text-slate-700";
  return (
    <div>
      <span className={`text-2xl font-semibold tabular-nums tracking-tight ${valueColor}`}>{value}</span>
      <p className="mt-1 text-xs text-slate-500">{label}</p>
    </div>
  );
}

// ─── Optimization gains ─────────────────────────────────────────────────────
// Shows what we won (or didn't) by picking an optimized scenario over the
// human-dispatcher baseline. Renders three pieces:
//   1) Hero KPI strip (distance, hours, on-time bump, fuel/CO₂)
//   2) Per-day grouped bars: baseline vs optimized km (same vertical scale)
//   3) Solver-mix breakdown: which solver won each comparison
// Empty state kicks in when `comparisons_count === 0`.

function OptimizationGainsSection({ savings }: { savings: OptimizationSavingsResponse }) {
  const { comparisons_count, totals, per_day, solver_mix } = savings;

  if (comparisons_count === 0) {
    return (
      <p className="text-sm text-slate-500">
        No completed route-scenario comparisons in this range yet. Run a
        comparison from the Orders page (<span className="font-medium">Compare routes</span>)
        and pick a winning scenario — savings will land here. You can also seed
        demo data:{" "}
        <code className="text-xs bg-slate-100 px-1 py-0.5 rounded">
          python -m app.seed_optimization_savings_demo
        </code>
      </p>
    );
  }

  // Cap so a single outlier day doesn't squash the rest.
  const maxKm = per_day.reduce(
    (m, d) => Math.max(m, d.baseline_distance_km, d.optimized_distance_km),
    0,
  );
  const solverTotal = solver_mix.reduce((s, r) => s + r.count, 0);
  // Pixel-based bar heights so bars stay visible (percentage heights inside nested
  // flex were collapsing to ~0 in some layouts).
  const CHART_INNER_PX = 112;
  const BAR_MIN_PX = 6;

  return (
    <div>
      <div className="flex justify-end mb-4">
        <span className="text-[10px] text-slate-400">
          {comparisons_count} {comparisons_count === 1 ? "comparison" : "comparisons"} · baseline vs selected
        </span>
      </div>

      {/* Hero KPIs */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-6 mb-6">
        <GainItem
          label="Distance saved"
          value={`${totals.distance_saved_km.toFixed(1)} km`}
          delta={totals.distance_saved_pct}
          tone="emerald"
          sub={`vs ${totals.baseline_distance_km.toFixed(0)} km baseline`}
        />
        <GainItem
          label="Time saved"
          value={`${(totals.duration_saved_min / 60).toFixed(1)} h`}
          delta={totals.duration_saved_pct}
          tone="emerald"
          sub={`${totals.duration_saved_min.toFixed(0)} courier-minutes`}
        />
        <GainItem
          label="On-time bump"
          value={`${(totals.on_time_rate_delta * 100).toFixed(1)} pp`}
          tone={totals.on_time_rate_delta >= 0 ? "emerald" : "red"}
          sub={`${(totals.baseline_on_time_rate * 100).toFixed(0)}% → ${(totals.optimized_on_time_rate * 100).toFixed(0)}%`}
        />
        <GainItem
          label="CO₂ saved"
          value={`${totals.co2_saved_kg.toFixed(1)} kg`}
          tone="emerald"
          sub={`${totals.fuel_saved_liters.toFixed(1)} L fuel · ₺${totals.energy_cost_saved_try.toFixed(0)}`}
        />
      </div>

      {/* Per-day grouped bars (baseline | optimized) */}
      {per_day.length > 0 && (
        <div className="mb-6">
          <div className="flex items-center justify-between mb-3">
            <p className="text-xs text-slate-600">Daily distance: baseline vs optimized</p>
            <div className="flex items-center gap-3 text-[10px] text-slate-500">
              <span className="flex items-center gap-1">
                <span className="inline-block w-2.5 h-2.5 rounded-sm bg-slate-400 ring-1 ring-slate-300/80" /> baseline
              </span>
              <span className="flex items-center gap-1">
                <span className="inline-block w-2.5 h-2.5 rounded-sm bg-emerald-500 ring-1 ring-emerald-600/30" /> optimized
              </span>
            </div>
          </div>
          <div className="flex justify-between gap-2 sm:gap-3 rounded-md border border-slate-100 bg-slate-50/60 px-2 py-3">
            {per_day.map((d) => {
              const scale = (km: number) =>
                maxKm > 0 ? (km / maxKm) * CHART_INNER_PX : CHART_INNER_PX * 0.5;
              const basePx = Math.min(
                CHART_INNER_PX,
                Math.max(BAR_MIN_PX, scale(d.baseline_distance_km)),
              );
              const optPx = Math.min(
                CHART_INNER_PX,
                Math.max(BAR_MIN_PX, scale(d.optimized_distance_km)),
              );
              const saved = d.baseline_distance_km - d.optimized_distance_km;
              return (
                <div key={d.date} className="flex min-w-0 flex-1 flex-col items-center gap-1.5 group">
                  <div
                    className="flex w-full max-w-[4.75rem] items-end justify-center gap-1 overflow-hidden rounded-sm bg-white/50 px-0.5"
                    style={{ height: CHART_INNER_PX }}
                  >
                    <div
                      className="min-w-0 flex-1 shrink-0 rounded-sm bg-slate-400 shadow-sm ring-1 ring-slate-300/80 transition-all group-hover:bg-slate-500"
                      style={{ height: basePx }}
                      title={`Baseline · ${d.baseline_distance_km.toFixed(1)} km`}
                    />
                    <div
                      className="min-w-0 flex-1 shrink-0 rounded-sm bg-emerald-500 shadow-sm ring-1 ring-emerald-600/30 transition-all group-hover:bg-emerald-600"
                      style={{ height: optPx }}
                      title={`Optimized (${scenarioLabel(d.selected_scenario_key)}) · ${d.optimized_distance_km.toFixed(1)} km`}
                    />
                  </div>
                  <div className="text-[10px] text-slate-500 tabular-nums leading-tight text-center">
                    <div className="font-medium text-slate-600">{d.date.slice(5)}</div>
                    <div className={saved >= 0 ? "text-emerald-600" : "text-red-500"}>
                      {saved >= 0 ? "−" : "+"}
                      {Math.abs(saved).toFixed(0)} km
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Solver mix */}
      {solver_mix.length > 0 && (
        <div>
          <p className="text-xs text-slate-600 mb-3">Winning solvers</p>
          <div className="space-y-2">
            {solver_mix.map((row, i) => {
              const share = solverTotal > 0 ? (row.count / solverTotal) * 100 : 0;
              const color = SOLVER_MIX_COLORS[i % SOLVER_MIX_COLORS.length];
              return (
                <div key={row.scenario_key}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-sm text-slate-700">{scenarioLabel(row.scenario_key)}</span>
                    <span className="text-sm font-medium text-slate-900 tabular-nums">
                      {row.count} <span className="text-slate-400 text-xs">({share.toFixed(0)}%)</span>
                    </span>
                  </div>
                  <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                    <div className={`h-full ${color} transition-all duration-500`} style={{ width: `${share}%` }} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function GainItem({
  label,
  value,
  delta,
  tone,
  sub,
}: {
  label: string;
  value: string;
  delta?: number; // 0-1 fraction; renders as "(12.3%)"
  tone: "emerald" | "red" | "neutral";
  sub?: string;
}) {
  const valueColor =
    tone === "emerald"
      ? "text-emerald-600"
      : tone === "red"
        ? "text-red-600"
        : "text-slate-700";
  return (
    <div>
      <div className="flex items-baseline gap-1.5">
        <span className={`text-xl font-semibold tabular-nums tracking-tight ${valueColor}`}>
          {value}
        </span>
        {delta !== undefined && delta !== 0 && (
          <span className={`text-[10px] tabular-nums ${valueColor}`}>
            ({(delta * 100).toFixed(1)}%)
          </span>
        )}
      </div>
      <p className="mt-1 text-xs text-slate-500">{label}</p>
      {sub && <p className="text-[10px] text-slate-400 mt-0.5">{sub}</p>}
    </div>
  );
}

// ─── CSV report builder ─────────────────────────────────────────────────────
// Bundles everything visible on Insights into a single multi-section CSV.
// All data is already fetched, so the export is just a JS string assembly —
// no extra backend round-trip and no risk of the report drifting from the UI.

function csvCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  const s = String(value);
  // Quote when needed per RFC 4180: contains comma, quote, or newline.
  if (/[",\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

function csvRow(cells: unknown[]): string {
  return cells.map(csvCell).join(",");
}

type CourierRow = {
  courier_id: string;
  assigned_orders: number;
  delivered_orders: number;
  failed_orders: number;
};

type ExceptionCounts = {
  pending_approval_runs: number;
  critical_alerts: number;
  unassigned_orders: number;
  failed_orders: number;
  total_open_items: number;
};

type ReportInput = {
  rangeLabel: string;
  summary: AnalyticsSummary;
  fleet: FleetUtilizationResponse | null | undefined;
  exceptionCounts: ExceptionCounts | null | undefined;
  courierRows: CourierRow[];
  savings: OptimizationSavingsResponse | null | undefined;
};

function buildInsightsReportCsv({ rangeLabel, summary, fleet, exceptionCounts, courierRows, savings }: ReportInput): string {
  const lines: string[] = [];
  const stamp = new Date().toISOString();
  lines.push(csvRow(["Insights report"]));
  lines.push(csvRow(["Time range", rangeLabel]));
  lines.push(csvRow(["Generated at", stamp]));
  lines.push("");

  // Summary KPIs
  lines.push(csvRow(["# Summary"]));
  lines.push(csvRow(["Metric", "Value"]));
  lines.push(csvRow(["Total orders", summary.total_orders]));
  lines.push(csvRow(["Delivered", summary.delivered_orders]));
  lines.push(csvRow(["Failed", summary.failed_orders]));
  lines.push(csvRow(["Pending", summary.pending_orders]));
  lines.push(csvRow(["Completion rate", `${(summary.completion_rate * 100).toFixed(2)}%`]));
  lines.push(csvRow(["Failure rate", `${(summary.failed_rate * 100).toFixed(2)}%`]));
  lines.push(csvRow(["On-time rate", `${(summary.on_time_rate * 100).toFixed(2)}%`]));
  lines.push("");

  // Order status breakdown
  if (summary.status_breakdown) {
    const breakdown = summary.status_breakdown;
    lines.push(csvRow(["# Order status breakdown"]));
    lines.push(csvRow(["Status", "Count", "Share"]));
    for (const { key, label } of STATUS_DISPLAY) {
      const value = breakdown[key as keyof typeof breakdown] ?? 0;
      const share = summary.total_orders > 0 ? (value / summary.total_orders) * 100 : 0;
      lines.push(csvRow([label, value, `${share.toFixed(2)}%`]));
    }
    lines.push("");
  }

  // Fleet utilisation
  if (fleet) {
    lines.push(csvRow(["# Fleet health"]));
    lines.push(csvRow(["Metric", "Value"]));
    lines.push(csvRow(["Active couriers", fleet.rows.length]));
    lines.push(csvRow(["Routes", fleet.total_route_count]));
    lines.push(csvRow(["Distance driven (km)", fleet.total_distance_km.toFixed(2)]));
    lines.push(csvRow(["Hours logged", fleet.total_hours.toFixed(2)]));
    lines.push("");
  }

  // Operations (right-now exception center snapshot)
  if (exceptionCounts) {
    lines.push(csvRow(["# Operations (right now)"]));
    lines.push(csvRow(["Metric", "Value"]));
    lines.push(csvRow(["Pending approval runs", exceptionCounts.pending_approval_runs]));
    lines.push(csvRow(["Critical alerts", exceptionCounts.critical_alerts]));
    lines.push(csvRow(["Unassigned orders", exceptionCounts.unassigned_orders]));
    lines.push(csvRow(["Failed orders", exceptionCounts.failed_orders]));
    lines.push(csvRow(["Total open items", exceptionCounts.total_open_items]));
    lines.push("");
  }

  // Optimization gains (baseline vs selected scenario)
  if (savings && savings.comparisons_count > 0) {
    const t = savings.totals;
    lines.push(csvRow(["# Optimization gains"]));
    lines.push(csvRow(["Metric", "Value"]));
    lines.push(csvRow(["Comparisons", savings.comparisons_count]));
    lines.push(csvRow(["Distance saved (km)", t.distance_saved_km.toFixed(2)]));
    lines.push(csvRow(["Distance saved (%)", `${(t.distance_saved_pct * 100).toFixed(2)}%`]));
    lines.push(csvRow(["Duration saved (min)", t.duration_saved_min.toFixed(1)]));
    lines.push(csvRow(["Duration saved (%)", `${(t.duration_saved_pct * 100).toFixed(2)}%`]));
    lines.push(csvRow(["On-time rate delta (pp)", (t.on_time_rate_delta * 100).toFixed(2)]));
    lines.push(csvRow(["Late minutes saved", t.late_minutes_saved]));
    lines.push(csvRow(["Energy cost saved (TRY)", t.energy_cost_saved_try.toFixed(2)]));
    lines.push(csvRow(["Fuel saved (L)", t.fuel_saved_liters.toFixed(2)]));
    lines.push(csvRow(["CO₂ saved (kg)", t.co2_saved_kg.toFixed(2)]));
    lines.push(csvRow(["Routes saved", t.routes_saved]));
    lines.push("");

    if (savings.per_day.length > 0) {
      lines.push(csvRow(["# Optimization gains — per day"]));
      lines.push(csvRow([
        "Date", "Selected scenario",
        "Baseline km", "Optimized km",
        "Baseline min", "Optimized min",
        "Baseline on-time %", "Optimized on-time %",
        "Baseline energy (TRY)", "Optimized energy (TRY)",
        "Baseline CO₂ (kg)", "Optimized CO₂ (kg)",
      ]));
      for (const d of savings.per_day) {
        lines.push(csvRow([
          d.date, scenarioLabel(d.selected_scenario_key),
          d.baseline_distance_km.toFixed(2), d.optimized_distance_km.toFixed(2),
          d.baseline_duration_min.toFixed(1), d.optimized_duration_min.toFixed(1),
          `${(d.baseline_on_time_rate * 100).toFixed(2)}%`, `${(d.optimized_on_time_rate * 100).toFixed(2)}%`,
          d.baseline_energy_cost_try.toFixed(2), d.optimized_energy_cost_try.toFixed(2),
          d.baseline_co2_kg.toFixed(2), d.optimized_co2_kg.toFixed(2),
        ]));
      }
      lines.push("");
    }

    if (savings.solver_mix.length > 0) {
      lines.push(csvRow(["# Optimization gains — winning solvers"]));
      lines.push(csvRow(["Scenario", "Wins"]));
      for (const r of savings.solver_mix) {
        lines.push(csvRow([scenarioLabel(r.scenario_key), r.count]));
      }
      lines.push("");
    }
  }

  // Courier performance table
  if (courierRows.length > 0) {
    lines.push(csvRow(["# Courier performance"]));
    lines.push(csvRow(["Courier ID", "Assigned", "Delivered", "Failed", "Completion rate"]));
    for (const row of courierRows) {
      const rate = row.assigned_orders > 0 ? (row.delivered_orders / row.assigned_orders) * 100 : 0;
      lines.push(csvRow([row.courier_id, row.assigned_orders, row.delivered_orders, row.failed_orders, `${rate.toFixed(2)}%`]));
    }
    lines.push("");
  }

  return lines.join("\r\n");
}
