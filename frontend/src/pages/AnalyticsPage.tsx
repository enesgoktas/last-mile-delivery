import { useCallback, useMemo, useState } from "react";
import { useAuth } from "../state/AuthContext";
import { usePolling } from "../hooks/usePolling";
import { getAnalyticsSummary, getCourierAnalytics } from "../api/client";

const POLL = 60_000;

const TIME_RANGES = [
  { label: "Today", value: "today" },
  { label: "This Week", value: "week" },
  { label: "This Month", value: "month" },
  { label: "All Time", value: "all" },
] as const;

type TimeRangeValue = (typeof TIME_RANGES)[number]["value"];

/** Convert a UI time-range token into ISO date strings for the backend. */
function toDateParams(range: TimeRangeValue): { date_from?: string; date_to?: string } {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  const isoDate = (d: Date) =>
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;

  if (range === "today") {
    const start = new Date(now);
    start.setHours(0, 0, 0, 0);
    return { date_from: isoDate(start) };
  }
  if (range === "week") {
    const start = new Date(now);
    start.setDate(now.getDate() - 6);
    start.setHours(0, 0, 0, 0);
    return { date_from: isoDate(start) };
  }
  if (range === "month") {
    const start = new Date(now);
    start.setDate(now.getDate() - 29);
    start.setHours(0, 0, 0, 0);
    return { date_from: isoDate(start) };
  }
  // "all" — no date filters
  return {};
}

function KpiTile({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5">
      <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">{label}</p>
      <p className={`text-2xl font-bold mt-1 ${color ?? "text-gray-900"}`}>{value}</p>
      {sub && <p className="text-xs text-gray-400 mt-1">{sub}</p>}
    </div>
  );
}

function BarSegment({ label, value, total, color }: { label: string; value: number; total: number; color: string }) {
  const pct = total > 0 ? (value / total) * 100 : 0;
  return (
    <div>
      <div className="flex items-center justify-between text-sm mb-1.5">
        <span className="text-gray-600">{label}</span>
        <span className="font-medium text-gray-900">{value} <span className="text-gray-400 text-xs">({pct.toFixed(1)}%)</span></span>
      </div>
      <div className="w-full h-2.5 bg-gray-100 rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full transition-all duration-500`} style={{ width: `${Math.min(pct, 100)}%` }} />
      </div>
    </div>
  );
}

export function AnalyticsPage() {
  const { token } = useAuth();
  const [timeRange, setTimeRange] = useState<TimeRangeValue>("week");

  const dateParams = useMemo(() => toDateParams(timeRange), [timeRange]);

  const fetchSummary = useCallback(
    () => getAnalyticsSummary(token!, dateParams),
    [token, dateParams],
  );
  const fetchCouriers = useCallback(
    () => getCourierAnalytics(token!, dateParams),
    [token, dateParams],
  );

  const { data: summary, isLoading: summaryLoading, error: summaryError } = usePolling(fetchSummary, POLL);
  const { data: courierData, isLoading: couriersLoading, error: couriersError } = usePolling(fetchCouriers, POLL);

  const isInitialLoading = (summaryLoading && !summary) || (couriersLoading && !courierData);
  const fetchError = summaryError ?? couriersError ?? null;

  const rows = courierData?.rows ?? [];

  const sortedRows = useMemo(
    () => [...rows].sort((a, b) => b.delivered_orders - a.delivered_orders),
    [rows],
  );

  return (
    <div className="flex-1 overflow-y-auto">
      {/* Header */}
      <div className="px-6 pt-5 pb-4 flex items-center justify-between">
        <h1 className="text-lg font-semibold text-gray-900">Analytics</h1>
        <div className="flex items-center gap-1 bg-gray-100 rounded-lg p-1">
          {TIME_RANGES.map((range) => (
            <button
              key={range.value}
              onClick={() => setTimeRange(range.value)}
              className={`px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
                timeRange === range.value
                  ? "bg-white text-gray-900 shadow-sm"
                  : "text-gray-500 hover:text-gray-700"
              }`}
            >
              {range.label}
            </button>
          ))}
        </div>
      </div>

      {/* Error banner */}
      {fetchError && (
        <div className="mx-6 mb-2 px-4 py-3 bg-red-50 border border-red-200 rounded-lg flex items-center gap-2">
          <svg className="w-4 h-4 text-red-500 shrink-0" fill="currentColor" viewBox="0 0 20 20">
            <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
          </svg>
          <p className="text-sm text-red-700">Failed to load analytics: {fetchError}</p>
        </div>
      )}

      {/* Loading skeleton */}
      {isInitialLoading ? (
        <div className="px-6 pb-6 space-y-6">
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="bg-white rounded-xl border border-gray-200 p-5 space-y-2">
                <div className="h-3 w-20 bg-gray-100 rounded animate-pulse" />
                <div className="h-7 w-16 bg-gray-100 rounded animate-pulse" />
              </div>
            ))}
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {[...Array(2)].map((_, i) => (
              <div key={i} className="bg-white rounded-xl border border-gray-200 p-6 h-48 animate-pulse" />
            ))}
          </div>
        </div>
      ) : (

      <div className="px-6 pb-6 space-y-6">
        {/* KPI tiles */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <KpiTile
            label="Delivered"
            value={String(summary?.delivered_orders ?? "—")}
            sub={summary ? `${(summary.completion_rate * 100).toFixed(1)}% completion rate` : undefined}
            color="text-emerald-600"
          />
          <KpiTile
            label="Failed"
            value={String(summary?.failed_orders ?? "—")}
            sub={summary ? `${(summary.failed_rate * 100).toFixed(1)}% failure rate` : undefined}
            color={summary && summary.failed_orders > 0 ? "text-red-600" : "text-gray-900"}
          />
          <KpiTile
            label="On-Time Rate"
            value={summary ? `${(summary.on_time_rate * 100).toFixed(1)}%` : "—"}
            sub="Target: 95%"
          />
          <KpiTile
            label="Total Orders"
            value={String(summary?.total_orders ?? "—")}
            sub={`${summary?.pending_orders ?? 0} pending`}
          />
        </div>

        {/* Charts row */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Order funnel */}
          {summary && (
            <div className="bg-white rounded-xl border border-gray-200 p-6">
              <h3 className="text-sm font-semibold text-gray-900 mb-5">Order Breakdown</h3>
              <div className="space-y-4">
                <BarSegment label="Delivered" value={summary.delivered_orders} total={summary.total_orders} color="bg-emerald-500" />
                <BarSegment label="Pending" value={summary.pending_orders} total={summary.total_orders} color="bg-amber-500" />
                <BarSegment label="Failed" value={summary.failed_orders} total={summary.total_orders} color="bg-red-500" />
              </div>
            </div>
          )}

          {/* Rate gauges */}
          {summary && (
            <div className="bg-white rounded-xl border border-gray-200 p-6">
              <h3 className="text-sm font-semibold text-gray-900 mb-5">Performance Rates</h3>
              <div className="space-y-6">
                {[
                  { label: "Completion Rate", value: summary.completion_rate, target: 0.95, color: "bg-emerald-500" },
                  { label: "On-Time Rate", value: summary.on_time_rate, target: 0.95, color: "bg-blue-500" },
                  { label: "Failure Rate", value: summary.failed_rate, target: 0.05, color: "bg-red-500", invert: true },
                ].map((gauge) => (
                  <div key={gauge.label}>
                    <div className="flex items-center justify-between text-sm mb-2">
                      <span className="text-gray-600">{gauge.label}</span>
                      <span className="font-semibold text-gray-900">{(gauge.value * 100).toFixed(1)}%</span>
                    </div>
                    <div className="w-full h-3 bg-gray-100 rounded-full overflow-hidden relative">
                      <div className={`h-full ${gauge.color} rounded-full transition-all duration-500`} style={{ width: `${Math.min(gauge.value * 100, 100)}%` }} />
                      <div
                        className="absolute top-0 h-full w-0.5 bg-gray-400"
                        style={{ left: `${gauge.target * 100}%` }}
                        title={`Target: ${(gauge.target * 100).toFixed(0)}%`}
                      />
                    </div>
                    <p className="text-[10px] text-gray-400 mt-1">
                      Target: {(gauge.target * 100).toFixed(0)}%
                      {gauge.invert
                        ? (gauge.value <= gauge.target ? " — within target" : " — above target")
                        : (gauge.value >= gauge.target ? " — meeting target" : " — below target")
                      }
                    </p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Courier performance table */}
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <div className="px-6 py-4 border-b border-gray-100">
            <h3 className="text-sm font-semibold text-gray-900">Courier Performance</h3>
          </div>
          {!sortedRows.length ? (
            <div className="p-12 text-center text-gray-400 text-sm">No courier analytics available</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-100 bg-gray-50/50">
                    <th className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">Courier</th>
                    <th className="text-right px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">Assigned</th>
                    <th className="text-right px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">Delivered</th>
                    <th className="text-right px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">Failed</th>
                    <th className="text-right px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider">Success Rate</th>
                    <th className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wider w-32"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {sortedRows.map((row) => {
                    const rate = row.assigned_orders > 0 ? (row.delivered_orders / row.assigned_orders) * 100 : 0;
                    return (
                      <tr key={row.courier_id} className="hover:bg-gray-50/50">
                        <td className="px-4 py-3 font-mono text-xs text-gray-600">{row.courier_id.slice(0, 8)}</td>
                        <td className="px-4 py-3 text-right text-gray-700">{row.assigned_orders}</td>
                        <td className="px-4 py-3 text-right text-emerald-600 font-medium">{row.delivered_orders}</td>
                        <td className="px-4 py-3 text-right text-red-600 font-medium">{row.failed_orders}</td>
                        <td className="px-4 py-3 text-right text-gray-700">{rate > 0 ? `${rate.toFixed(1)}%` : "—"}</td>
                        <td className="px-4 py-3">
                          <div className="w-full h-1.5 bg-gray-100 rounded-full overflow-hidden">
                            <div className="h-full bg-emerald-500 rounded-full" style={{ width: `${Math.min(rate, 100)}%` }} />
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
      )}
    </div>
  );
}
