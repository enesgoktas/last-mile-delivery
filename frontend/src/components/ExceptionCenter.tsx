import { useMemo, useState } from "react";
import { Zap } from "lucide-react";
import type { Courier, ExceptionCenterCounts, OptimizationRun, Order, SLAAlert } from "../types";
import { Button } from "./ui/Button";

type ExceptionCenterProps = {
  counts: ExceptionCenterCounts;
  pendingRun: OptimizationRun | null;
  criticalAlerts: SLAAlert[];
  failedOrders: Order[];
  pendingOrders: Order[];
  couriers: Courier[];
  assigningOptimally: boolean;
  acknowledgingAlertId: string | null;
  assigningOrderId: string | null;
  dispatchingOrderId: string | null;
  pinningOrderId: string | null;
  cancellingOrderId: string | null;
  onReviewPendingRun: () => void;
  onAssignOptimally: () => void;
  onOpenRoute: (routeId: string) => void;
  onAcknowledgeAlert: (alertId: string) => void;
  onAssignOrder: (orderId: string, courierId: string) => void;
  onDispatchNow: (orderId: string) => void;
  onSetPreferredCourier: (orderId: string, courierId: string | null) => void;
  onRescheduleOrder: (order: Order) => void;
  onCancelOrder: (order: Order) => void;
};

function formatDelay(seconds: number): string {
  const minutes = Math.max(0, Math.floor(seconds / 60));
  if (minutes < 60) return `${minutes}m late`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m late`;
}

function formatAlertSubtitle(alert: SLAAlert): string {
  if (alert.alert_type === "COURIER_OFFLINE") {
    return `Courier ${alert.courier_id.slice(0, 8)} · just now`;
  }
  return formatDelay(alert.estimated_delay_seconds);
}

function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function ExceptionCenter({
  counts,
  pendingRun,
  criticalAlerts,
  failedOrders,
  pendingOrders,
  couriers,
  assigningOptimally,
  acknowledgingAlertId,
  assigningOrderId,
  dispatchingOrderId,
  pinningOrderId,
  cancellingOrderId,
  onReviewPendingRun,
  onAssignOptimally,
  onOpenRoute,
  onAcknowledgeAlert,
  onAssignOrder,
  onDispatchNow,
  onSetPreferredCourier,
  onRescheduleOrder,
  onCancelOrder,
}: ExceptionCenterProps) {
  const [selectedCourierByOrderId, setSelectedCourierByOrderId] = useState<Record<string, string>>({});

  const courierById = useMemo(
    () => new Map(couriers.map((c) => [c.id, c])),
    [couriers],
  );
  const allCouriers = useMemo(
    () =>
      [...couriers].sort((a, b) => {
        if (a.availability_status === b.availability_status) return a.id.localeCompare(b.id);
        if (a.availability_status === "ONLINE") return -1;
        if (b.availability_status === "ONLINE") return 1;
        return a.availability_status.localeCompare(b.availability_status);
      }),
    [couriers],
  );

  const visibleAlerts = criticalAlerts.filter((a) => a.status === "ACTIVE").slice(0, 4);
  const visibleFailed = failedOrders.slice(0, 4);

  return (
    <div className="border-b border-slate-100 flex flex-col min-h-0">
      {/* Header */}
      <div className="px-4 pt-3 pb-2 flex flex-wrap items-center justify-between gap-2 shrink-0">
        <span className="text-xs font-medium uppercase tracking-wide text-slate-400">Needs attention</span>
        {pendingOrders.length > 0 && (
          <Button variant="primary" size="sm" onClick={onAssignOptimally} disabled={assigningOptimally}>
            <Zap size={12} />
            {assigningOptimally ? "Assigning…" : "Assign Optimally"}
          </Button>
        )}
      </div>

      {/* Summary chips */}
      <div className="px-4 pb-3 flex flex-wrap gap-1.5 shrink-0">
        {counts.pending_approval_runs > 0 && (
          <button
            onClick={onReviewPendingRun}
            className="text-xs text-violet-700 border border-violet-200 bg-violet-50 px-2 py-0.5 rounded hover:bg-violet-100 transition-colors duration-150"
          >
            {counts.pending_approval_runs} pending approval
          </button>
        )}
        {counts.critical_alerts > 0 && (
          <span className="text-xs text-red-700 border border-red-100 bg-red-50 px-2 py-0.5 rounded">
            {counts.critical_alerts} critical
          </span>
        )}
        {counts.failed_orders > 0 && (
          <span className="text-xs text-amber-700 border border-amber-100 bg-amber-50 px-2 py-0.5 rounded">
            {counts.failed_orders} failed today
          </span>
        )}
        {counts.unassigned_orders > 0 && (
          <span className="text-xs text-slate-600 border border-slate-200 bg-slate-50 px-2 py-0.5 rounded">
            {counts.unassigned_orders} unassigned today
          </span>
        )}
      </div>

      {/* Pending run */}
      {pendingRun && (
        <div className="mx-4 mb-3 border-l-2 border-l-violet-400 pl-3 py-2 bg-violet-50/40 shrink-0">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <p className="text-xs font-medium text-slate-800">Route optimization ready</p>
              <p className="text-xs text-slate-500 mt-0.5">
                {pendingRun.routes_created ?? 0} routes · {pendingRun.unassigned_count ?? 0} unassigned
              </p>
            </div>
            <Button variant="secondary" size="sm" onClick={onReviewPendingRun}>Review</Button>
          </div>
        </div>
      )}

      {/* Critical alerts (non-scrollable, max 4) */}
      {visibleAlerts.map((alert) => (
        <div key={alert.id} className="mx-4 mb-2 border-l-2 border-l-red-500 pl-3 pr-2 py-2.5 shrink-0">
          <p className="text-xs font-medium text-slate-800 leading-5 break-words">{alert.message}</p>
          <p className="text-xs text-slate-500 mt-0.5 leading-5">{formatAlertSubtitle(alert)}</p>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 mt-2">
            <button
              onClick={() => onOpenRoute(alert.route_id)}
              className="text-xs text-slate-600 hover:text-slate-900 underline transition-colors duration-150"
            >
              Open route
            </button>
            <button
              onClick={() => onAcknowledgeAlert(alert.id)}
              disabled={acknowledgingAlertId === alert.id}
              className="text-xs text-red-600 hover:text-red-800 underline disabled:opacity-50 transition-colors duration-150"
            >
              {acknowledgingAlertId === alert.id ? "…" : "Dismiss"}
            </button>
          </div>
        </div>
      ))}

      {/* Failed orders (non-scrollable, max 4) */}
      {visibleFailed.map((order) => (
        <div key={order.id} className="mx-4 mb-2 border-l-2 border-l-amber-400 pl-3 pr-2 py-2.5 shrink-0">
          <p className="text-xs font-medium text-slate-800 leading-5 break-words">{order.delivery_address.split(",")[0]}</p>
          <p className="text-xs text-slate-500 mt-0.5 leading-5 break-words">
            {order.attempt_count}/{order.max_attempts} attempts · {order.customer_email}
          </p>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 mt-2">
            <button
              onClick={() => onRescheduleOrder(order)}
              className="text-xs text-slate-600 hover:text-slate-900 underline transition-colors duration-150"
            >
              Reschedule
            </button>
            <button
              onClick={() => onCancelOrder(order)}
              disabled={cancellingOrderId === order.id}
              className="text-xs text-red-600 hover:text-red-800 underline disabled:opacity-50 transition-colors duration-150"
            >
              {cancellingOrderId === order.id ? "…" : "Cancel"}
            </button>
          </div>
        </div>
      ))}

      {/* Unassigned orders — scrollable list showing all */}
      {pendingOrders.length > 0 && (
        <div className="overflow-y-auto thin-scrollbar flex-1 min-h-0 max-h-[340px]">
          {pendingOrders.map((order) => {
            const selectedCourierId = selectedCourierByOrderId[order.id] ?? order.preferred_courier_id ?? "";
            const selectedCourier = selectedCourierId ? courierById.get(selectedCourierId) : undefined;
            const canAssign = selectedCourier?.availability_status === "ONLINE";
            const canPin = !!selectedCourierId && selectedCourierId !== order.preferred_courier_id;

            return (
              <div key={order.id} className="mx-4 mb-2 border-l-2 border-l-slate-300 pl-3 pr-2 py-2.5">
                <p className="text-xs font-medium text-slate-800 leading-5 break-words">{order.delivery_address.split(",")[0]}</p>
                <p className="text-xs text-slate-500 mt-0.5 leading-5">
                  {order.priority} · due {formatTime(order.latest_delivery)}
                </p>
                <div className="mt-2 space-y-1.5">
                  <Button
                    variant="primary"
                    size="sm"
                    className="w-full justify-center"
                    disabled={dispatchingOrderId === order.id}
                    onClick={() => onDispatchNow(order.id)}
                  >
                    {dispatchingOrderId === order.id ? "Finding best route…" : "Dispatch Now"}
                  </Button>
                  <div className="flex flex-wrap items-center gap-1.5">
                    <select
                      value={selectedCourierId}
                      onChange={(e) =>
                        setSelectedCourierByOrderId((prev) => ({ ...prev, [order.id]: e.target.value }))
                      }
                      className="flex-1 min-w-0 border border-slate-200 rounded text-xs px-2 py-1 text-slate-700 bg-white focus:outline-none focus:border-accent-500 truncate"
                    >
                      <option value="">Courier…</option>
                      {allCouriers.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.id.slice(0, 8)} · {c.vehicle_type.replace("_", " ")} · {c.availability_status}
                        </option>
                      ))}
                    </select>
                    <Button
                      variant="secondary"
                      size="sm"
                      disabled={!canPin || pinningOrderId === order.id}
                      onClick={() => onSetPreferredCourier(order.id, selectedCourierId || null)}
                    >
                      Pin
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
                      disabled={!selectedCourierId || !canAssign || assigningOrderId === order.id}
                      onClick={() => onAssignOrder(order.id, selectedCourierId)}
                    >
                      {assigningOrderId === order.id ? "…" : "Assign"}
                    </Button>
                  </div>
                </div>
              </div>
            );
          })}
          <div className="h-3" />
        </div>
      )}

      {pendingOrders.length === 0 && <div className="h-3 shrink-0" />}
    </div>
  );
}
