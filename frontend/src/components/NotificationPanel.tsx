import { useCallback } from "react";
import { RefreshCw } from "lucide-react";
import { useAuth } from "../state/AuthContext";
import { useToast } from "../state/ToastContext";
import { usePolling } from "../hooks/usePolling";
import { listSLAAlerts, acknowledgeSLAAlert, triggerSLAScan } from "../api/client";
import { SlideOver } from "./ui/SlideOver";
import { Button } from "./ui/Button";
import { StatusBadge } from "./ui/Badge";
import type { SLAAlert } from "../types";

interface NotificationPanelProps {
  open: boolean;
  onClose: () => void;
}

function AlertRow({ alert, onAcknowledge }: { alert: SLAAlert; onAcknowledge: (id: string) => void }) {
  const severityColor = alert.severity === "CRITICAL" ? "border-l-red-500" : "border-l-amber-400";

  return (
    <div className={`border-l-2 ${severityColor} pl-3 py-3 border-b border-slate-100`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm text-slate-800 leading-snug truncate">{alert.message}</p>
          <div className="flex items-center gap-2 mt-1">
            <StatusBadge value={alert.severity} />
            <span className="text-xs text-slate-400">
              {new Date(alert.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
            </span>
          </div>
        </div>
        {alert.status === "ACTIVE" && (
          <button
            onClick={() => onAcknowledge(alert.id)}
            className="shrink-0 text-xs text-slate-500 hover:text-slate-800 underline transition-colors duration-150"
          >
            Dismiss
          </button>
        )}
      </div>
    </div>
  );
}

export function NotificationPanel({ open, onClose }: NotificationPanelProps) {
  const { token } = useAuth();
  const { addToast } = useToast();

  const fetchAlerts = useCallback(
    () => (token ? listSLAAlerts(token) : Promise.resolve([])),
    [token],
  );
  const { data: alerts, refetch } = usePolling(fetchAlerts, 30_000);

  const activeAlerts = (alerts ?? []).filter((a) => a.status === "ACTIVE");
  const otherAlerts = (alerts ?? []).filter((a) => a.status !== "ACTIVE");

  async function handleAcknowledge(id: string) {
    if (!token) return;
    try {
      await acknowledgeSLAAlert(token, id);
      refetch();
    } catch {
      addToast("Failed to dismiss alert", "error");
    }
  }

  async function handleScan() {
    if (!token) return;
    try {
      const result = await triggerSLAScan(token);
      addToast(`Scan complete — ${result.alerts_created} created, ${result.alerts_resolved} resolved`, "success");
      refetch();
    } catch {
      addToast("Scan failed", "error");
    }
  }

  return (
    <SlideOver open={open} onClose={onClose} title="SLA Alerts" subtitle={activeAlerts.length > 0 ? `${activeAlerts.length} active` : "All clear"} width="w-[380px]">
      <div className="px-5 py-3 border-b border-slate-100 flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wide text-slate-400">Active</span>
        <Button variant="ghost" size="sm" onClick={() => void handleScan()}>
          <RefreshCw size={13} />
          Run scan
        </Button>
      </div>

      {activeAlerts.length === 0 ? (
        <div className="px-5 py-8 text-sm text-slate-400">No active alerts.</div>
      ) : (
        <div className="px-5">
          {activeAlerts.map((alert) => (
            <AlertRow key={alert.id} alert={alert} onAcknowledge={(id) => void handleAcknowledge(id)} />
          ))}
        </div>
      )}

      {otherAlerts.length > 0 && (
        <>
          <div className="px-5 pt-4 pb-2">
            <span className="text-xs font-medium uppercase tracking-wide text-slate-400">Recent</span>
          </div>
          <div className="px-5">
            {otherAlerts.slice(0, 10).map((alert) => (
              <AlertRow key={alert.id} alert={alert} onAcknowledge={(id) => void handleAcknowledge(id)} />
            ))}
          </div>
        </>
      )}
    </SlideOver>
  );
}
