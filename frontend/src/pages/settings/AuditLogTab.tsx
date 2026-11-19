import { useState } from "react";
import { useAuth } from "../../state/AuthContext";
import { usePolling } from "../../hooks/usePolling";
import { listAuditLogs } from "../../api/client";
import { Button } from "../../components/ui/Button";
import type { AuditLogEntry } from "../../types";

const PAGE_SIZE = 50;

const ACTION_PRESETS = [
  { label: "All actions",       value: "" },
  { label: "Login",             value: "auth.login" },
  { label: "Logout",            value: "auth.logout" },
  { label: "Signup",            value: "auth.signup" },
  { label: "Password reset",    value: "auth.password_reset" },
  { label: "Email verified",    value: "auth.email_verified" },
  { label: "OIDC login",        value: "auth.oidc_login" },
  { label: "Order created",     value: "order.created" },
  { label: "Order cancelled",   value: "order.cancelled" },
  { label: "Route started",     value: "route.started" },
  { label: "Settings changed",  value: "settings.updated" },
  { label: "User invited",      value: "user.invited" },
  { label: "Branding updated",  value: "branding.updated" },
  { label: "OIDC configured",   value: "oidc.configure" },
];

const RESOURCE_PRESETS = [
  { label: "All resources",  value: "" },
  { label: "user",           value: "user" },
  { label: "tenant",         value: "tenant" },
  { label: "order",          value: "order" },
  { label: "route",          value: "route" },
  { label: "oidc_provider",  value: "oidc_provider" },
  { label: "api_client",     value: "api_client" },
];

const inputCls = "border border-slate-300 rounded-md px-2.5 py-1.5 text-xs text-slate-700 bg-white focus:outline-none focus:border-accent-500";

function actionColor(action: string): string {
  if (action.includes("fail") || action.includes("error")) return "text-red-600";
  if (action.startsWith("auth.")) return "text-blue-600";
  if (action.startsWith("oidc.")) return "text-violet-600";
  return "text-slate-600";
}

export function AuditLogTab() {
  const { token } = useAuth();
  const [actionFilter, setActionFilter] = useState("");
  const [resourceFilter, setResourceFilter] = useState("");
  const [page, setPage] = useState(0);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const { data: entries, isLoading, error, refetch } = usePolling<AuditLogEntry[]>(
    () => listAuditLogs(token!, { action: actionFilter || undefined, resource_type: resourceFilter || undefined, limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
    30_000,
    !!token,
  );

  function changeFilter(setter: (v: string) => void) {
    return (v: string) => { setter(v); setPage(0); };
  }

  const rows = entries ?? [];
  const hasMore = rows.length === PAGE_SIZE;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <select value={actionFilter} onChange={(e) => changeFilter(setActionFilter)(e.target.value)} className={inputCls}>
          {ACTION_PRESETS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
        </select>
        <select value={resourceFilter} onChange={(e) => changeFilter(setResourceFilter)(e.target.value)} className={inputCls}>
          {RESOURCE_PRESETS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
        </select>
        <Button variant="ghost" size="sm" onClick={() => void refetch()}>Refresh</Button>
      </div>

      {error && <p className="text-sm text-red-600 border-l-2 border-l-red-500 pl-3">{error}</p>}

      {isLoading && !entries ? (
        <div className="space-y-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="h-9 bg-slate-100 rounded animate-pulse" />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <p className="py-12 text-center text-sm text-slate-400">No audit events — try adjusting filters.</p>
      ) : (
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="border-b border-slate-200">
              <th className="py-2.5 px-3 text-left text-xs font-medium uppercase tracking-wide text-slate-400">Timestamp</th>
              <th className="py-2.5 px-3 text-left text-xs font-medium uppercase tracking-wide text-slate-400">Action</th>
              <th className="py-2.5 px-3 text-left text-xs font-medium uppercase tracking-wide text-slate-400">Actor</th>
              <th className="py-2.5 px-3 text-left text-xs font-medium uppercase tracking-wide text-slate-400">Resource</th>
              <th className="py-2.5 px-3 text-left text-xs font-medium uppercase tracking-wide text-slate-400">IP</th>
              <th className="py-2.5 px-3 w-6" />
            </tr>
          </thead>
          <tbody>
            {rows.map((entry) => (
              <>
                <tr
                  key={entry.id}
                  className="border-b border-slate-100 hover:bg-slate-50 cursor-pointer transition-colors duration-100"
                  onClick={() => setExpandedId(expandedId === entry.id ? null : entry.id)}
                >
                  <td className="py-2.5 px-3 text-xs text-slate-400 tabular-nums whitespace-nowrap">
                    {new Date(entry.created_at).toLocaleString()}
                  </td>
                  <td className="py-2.5 px-3">
                    <span className={`text-xs font-mono font-medium ${actionColor(entry.action)}`}>{entry.action}</span>
                  </td>
                  <td className="py-2.5 px-3 text-xs font-mono text-slate-600">
                    {entry.actor_user_id ? entry.actor_user_id.slice(0, 8) : "system"}
                  </td>
                  <td className="py-2.5 px-3 text-xs text-slate-600">
                    {entry.resource_type && (
                      <span>
                        <span className="font-medium">{entry.resource_type}</span>
                        {entry.resource_id && <span className="text-slate-400 ml-1">#{entry.resource_id.slice(0, 8)}</span>}
                      </span>
                    )}
                  </td>
                  <td className="py-2.5 px-3 text-xs font-mono text-slate-400">{entry.ip_address ?? "—"}</td>
                  <td className="py-2.5 px-3 text-xs text-slate-300 text-right">{entry.details ? (expandedId === entry.id ? "▲" : "▼") : null}</td>
                </tr>
                {expandedId === entry.id && entry.details && (
                  <tr key={`${entry.id}-d`} className="bg-slate-50 border-b border-slate-100">
                    <td colSpan={6} className="px-4 py-3">
                      <pre className="text-xs font-mono text-slate-600 whitespace-pre-wrap overflow-x-auto">{JSON.stringify(entry.details, null, 2)}</pre>
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
      )}

      {(page > 0 || hasMore) && (
        <div className="flex items-center gap-3">
          <Button variant="secondary" size="sm" disabled={page === 0} onClick={() => setPage((p) => Math.max(0, p - 1))}>← Previous</Button>
          <span className="text-xs text-slate-500 tabular-nums">Page {page + 1}</span>
          <Button variant="secondary" size="sm" disabled={!hasMore} onClick={() => setPage((p) => p + 1)}>Next →</Button>
        </div>
      )}
    </div>
  );
}
