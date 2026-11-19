import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  acknowledgeSLAAlert,
  approveOptimizationRun,
  assignOrder,
  cancelOrder,
  dispatchOrderNow,
  getExceptionCenterSummary,
  rejectOptimizationRun,
  rescheduleOrder,
  setPreferredCourier,
  triggerOptimizePreview,
} from "../api/client";
import { usePolling } from "./usePolling";
import type { ExceptionCenterCounts, ExceptionCenterSummary, Order } from "../types";

const POLL_INTERVAL_MS = 15_000;

const EMPTY_COUNTS: ExceptionCenterCounts = {
  pending_approval_runs: 0,
  critical_alerts: 0,
  failed_orders: 0,
  unassigned_orders: 0,
  total_open_items: 0,
};

function toLocalDateTimeInputValue(iso: string): string {
  const date = new Date(iso);
  const offsetMs = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offsetMs).toISOString().slice(0, 16);
}

function toIsoFromLocalDateTime(value: string): string {
  return new Date(value).toISOString();
}

function buildDefaultRescheduleWindow(order: Order): { earliest: string; latest: string } {
  const earliest = new Date(order.earliest_delivery);
  const latest = new Date(order.latest_delivery);
  earliest.setDate(earliest.getDate() + 1);
  latest.setDate(latest.getDate() + 1);
  return {
    earliest: toLocalDateTimeInputValue(earliest.toISOString()),
    latest: toLocalDateTimeInputValue(latest.toISOString()),
  };
}

type ToastTone = "success" | "error" | "info";

type UseLiveOpsExceptionsArgs = {
  token: string;
  addToast: (message: string, tone: ToastTone) => void;
  refetchRoutes: () => void;
};

export function useLiveOpsExceptions({
  token,
  addToast,
  refetchRoutes,
}: UseLiveOpsExceptionsArgs) {
  const timeoutIdsRef = useRef<number[]>([]);
  const fetchExceptionSummary = useCallback(
    () => getExceptionCenterSummary(token),
    [token],
  );
  const {
    data: exceptionSummary,
    isLoading,
    error,
    refetch: refetchExceptionSummary,
  } = usePolling<ExceptionCenterSummary>(fetchExceptionSummary, POLL_INTERVAL_MS);

  const [optimizing, setOptimizing] = useState(false);
  const [acknowledgingAlertId, setAcknowledgingAlertId] = useState<string | null>(null);
  const [assigningOrderId, setAssigningOrderId] = useState<string | null>(null);
  const [dispatchingOrderId, setDispatchingOrderId] = useState<string | null>(null);
  const [pinningOrderId, setPinningOrderId] = useState<string | null>(null);
  const [cancellingOrderId, setCancellingOrderId] = useState<string | null>(null);
  const [rescheduleTarget, setRescheduleTarget] = useState<Order | null>(null);
  const [rescheduleEarliest, setRescheduleEarliest] = useState("");
  const [rescheduleLatest, setRescheduleLatest] = useState("");
  const [rescheduling, setRescheduling] = useState(false);

  const counts = exceptionSummary?.counts ?? EMPTY_COUNTS;
  const pendingRun = exceptionSummary?.pending_run ?? null;
  const criticalAlerts = exceptionSummary?.critical_alerts ?? [];
  const failedOrders = exceptionSummary?.failed_orders ?? [];
  const unassignedOrders = exceptionSummary?.unassigned_orders ?? [];

  const refreshLiveOps = useCallback(() => {
    refetchExceptionSummary();
    refetchRoutes();
  }, [refetchExceptionSummary, refetchRoutes]);

  const scheduleRefresh = useCallback(
    (delayMs: number) => {
      const timeoutId = window.setTimeout(() => {
        refreshLiveOps();
        timeoutIdsRef.current = timeoutIdsRef.current.filter((id) => id !== timeoutId);
      }, delayMs);
      timeoutIdsRef.current.push(timeoutId);
    },
    [refreshLiveOps],
  );

  useEffect(() => {
    return () => {
      for (const timeoutId of timeoutIdsRef.current) {
        window.clearTimeout(timeoutId);
      }
      timeoutIdsRef.current = [];
    };
  }, []);

  useEffect(() => {
    if (!rescheduleTarget) return;
    const nextWindow = buildDefaultRescheduleWindow(rescheduleTarget);
    setRescheduleEarliest(nextWindow.earliest);
    setRescheduleLatest(nextWindow.latest);
  }, [rescheduleTarget]);

  const hasAttention = useMemo(() => counts.total_open_items > 0, [counts.total_open_items]);

  const openRescheduleModal = useCallback((order: Order) => {
    setRescheduleTarget(order);
  }, []);

  const closeRescheduleModal = useCallback(() => {
    setRescheduleTarget(null);
  }, []);

  const handleOptimizePreview = useCallback(async () => {
    setOptimizing(true);
    try {
      const response = await triggerOptimizePreview(token);
      addToast(response.message, "info");
      scheduleRefresh(5_000);
      scheduleRefresh(12_000);
    } catch (err) {
      addToast(err instanceof Error ? err.message : "Optimization failed", "error");
    } finally {
      setOptimizing(false);
    }
  }, [addToast, scheduleRefresh, token]);

  const handleApprovePendingRun = useCallback(async () => {
    if (!pendingRun) return;
    try {
      const response = await approveOptimizationRun(token, pendingRun.id);
      addToast(`Approved — ${response.approved_routes} routes activated`, "success");
      refreshLiveOps();
      return true;
    } catch (err) {
      addToast(err instanceof Error ? err.message : "Approve failed", "error");
      return false;
    }
  }, [addToast, pendingRun, refreshLiveOps, token]);

  const handleRejectPendingRun = useCallback(async () => {
    if (!pendingRun) return;
    try {
      await rejectOptimizationRun(token, pendingRun.id);
      addToast("Optimization run rejected", "info");
      refreshLiveOps();
      return true;
    } catch (err) {
      addToast(err instanceof Error ? err.message : "Reject failed", "error");
      return false;
    }
  }, [addToast, pendingRun, refreshLiveOps, token]);

  const handleAcknowledgeAlert = useCallback(async (alertId: string) => {
    setAcknowledgingAlertId(alertId);
    try {
      await acknowledgeSLAAlert(token, alertId);
      addToast("Alert acknowledged", "success");
      refetchExceptionSummary();
    } catch (err) {
      addToast(err instanceof Error ? err.message : "Failed to acknowledge alert", "error");
    } finally {
      setAcknowledgingAlertId(null);
    }
  }, [addToast, refetchExceptionSummary, token]);

  const handleAssignOrder = useCallback(async (orderId: string, courierId: string) => {
    if (!courierId) return;
    setAssigningOrderId(orderId);
    try {
      await assignOrder(token, orderId, courierId);
      addToast("Order assigned to courier", "success");
      refreshLiveOps();
    } catch (err) {
      addToast(err instanceof Error ? err.message : "Failed to assign order", "error");
      throw err;
    } finally {
      setAssigningOrderId(null);
    }
  }, [addToast, refreshLiveOps, token]);

  const handleDispatchNow = useCallback(async (orderId: string) => {
    setDispatchingOrderId(orderId);
    try {
      const res = await dispatchOrderNow(token, orderId);
      if (res.assigned) {
        addToast(res.message, "success");
        refreshLiveOps();
      } else {
        addToast(res.message, "error");
      }
    } catch (err) {
      addToast(err instanceof Error ? err.message : "Dispatch failed", "error");
    } finally {
      setDispatchingOrderId(null);
    }
  }, [addToast, refreshLiveOps, token]);

  const handleSetPreferredCourier = useCallback(async (orderId: string, courierId: string | null) => {
    setPinningOrderId(orderId);
    try {
      await setPreferredCourier(token, orderId, courierId);
      addToast(
        courierId ? "Preferred courier pinned for the next optimization run" : "Preferred courier cleared",
        "success",
      );
      refetchExceptionSummary();
    } catch (err) {
      addToast(err instanceof Error ? err.message : "Failed to update preferred courier", "error");
      throw err;
    } finally {
      setPinningOrderId(null);
    }
  }, [addToast, refetchExceptionSummary, token]);

  const handleCancelFailedOrder = useCallback(async (order: Order) => {
    setCancellingOrderId(order.id);
    try {
      await cancelOrder(token, order.id);
      addToast("Order cancelled", "success");
      refreshLiveOps();
    } catch (err) {
      addToast(err instanceof Error ? err.message : "Failed to cancel order", "error");
    } finally {
      setCancellingOrderId(null);
    }
  }, [addToast, refreshLiveOps, token]);

  const handleConfirmReschedule = useCallback(async () => {
    if (!rescheduleTarget) return false;
    if (!rescheduleEarliest || !rescheduleLatest) {
      addToast("Choose both delivery window times", "error");
      return false;
    }
    if (Date.parse(rescheduleLatest) <= Date.parse(rescheduleEarliest)) {
      addToast("Latest delivery must be after earliest delivery", "error");
      return false;
    }

    setRescheduling(true);
    try {
      await rescheduleOrder(token, rescheduleTarget.id, {
        earliest_delivery: toIsoFromLocalDateTime(rescheduleEarliest),
        latest_delivery: toIsoFromLocalDateTime(rescheduleLatest),
      });
      addToast("Order rescheduled and returned to the dispatch queue", "success");
      setRescheduleTarget(null);
      refreshLiveOps();
      return true;
    } catch (err) {
      addToast(err instanceof Error ? err.message : "Failed to reschedule order", "error");
      return false;
    } finally {
      setRescheduling(false);
    }
  }, [
    addToast,
    refreshLiveOps,
    rescheduleEarliest,
    rescheduleLatest,
    rescheduleTarget,
    token,
  ]);

  return {
    counts,
    criticalAlerts,
    error,
    failedOrders,
    hasAttention,
    isLoading,
    optimizing,
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
    handleOptimizePreview,
    handleApprovePendingRun,
    handleRejectPendingRun,
    handleAcknowledgeAlert,
    handleAssignOrder,
    handleDispatchNow,
    handleSetPreferredCourier,
    handleCancelFailedOrder,
    handleConfirmReschedule,
    refetchExceptionSummary,
  };
}
