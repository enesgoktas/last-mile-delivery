import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Plus, Search } from "lucide-react";
import { useAuth } from "../../state/AuthContext";
import { listTenants } from "../../api/superadmin";
import type { SuperadminTenantSummary } from "../../types";

export function TenantsListPage() {
  const { token } = useAuth();
  const [tenants, setTenants] = useState<SuperadminTenantSummary[] | null>(null);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    listTenants(token)
      .then((r) => {
        if (!cancelled) setTenants(r);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load");
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const filtered = useMemo(() => {
    if (!tenants) return null;
    const q = filter.trim().toLowerCase();
    if (!q) return tenants;
    return tenants.filter(
      (t) => t.name.toLowerCase().includes(q) || t.slug.toLowerCase().includes(q),
    );
  }, [tenants, filter]);

  return (
    <div className="px-8 py-8 max-w-6xl mx-auto w-full">
      <div className="flex items-baseline justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Tenants</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Every company on the platform.
          </p>
        </div>
        <Link
          to="/admin/tenants/new"
          className="inline-flex items-center gap-1.5 h-9 px-3 bg-accent-500 hover:bg-accent-600 text-white text-sm font-medium rounded-md transition-colors"
        >
          <Plus size={14} />
          New tenant
        </Link>
      </div>

      <div className="relative mb-4">
        <Search
          size={14}
          className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
        />
        <input
          type="text"
          placeholder="Filter by name or slug…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="w-full h-9 pl-9 pr-3 border border-slate-200 rounded-md text-sm focus:outline-none focus:border-accent-400"
        />
      </div>

      {error && (
        <div className="mb-4 px-3 py-2 bg-red-50 border border-red-200 text-red-700 text-sm rounded">
          {error}
        </div>
      )}

      <div className="border border-slate-200 rounded-md overflow-hidden bg-white">
        <div className="grid grid-cols-[1fr_140px_100px_100px] px-4 py-2 text-[10px] font-semibold uppercase tracking-widest text-slate-400 bg-slate-50 border-b border-slate-200">
          <div>Tenant</div>
          <div>Modes</div>
          <div>Users</div>
          <div>Status</div>
        </div>
        {filtered === null && (
          <div className="px-4 py-6 text-sm text-slate-400">Loading…</div>
        )}
        {filtered && filtered.length === 0 && (
          <div className="px-4 py-6 text-sm text-slate-400">No tenants match.</div>
        )}
        {filtered &&
          filtered.map((t) => (
            <Link
              key={t.id}
              to={`/admin/tenants/${t.id}`}
              className="grid grid-cols-[1fr_140px_100px_100px] items-center px-4 py-3 border-b border-slate-100 last:border-b-0 hover:bg-slate-50 transition-colors"
            >
              <div>
                <div className="text-sm font-medium text-slate-900">{t.name}</div>
                <div className="text-xs text-slate-500 mt-0.5">{t.slug}</div>
              </div>
              <div className="flex gap-1">
                {t.feature_flags.deliveries_enabled && (
                  <ModeBadge>Deliveries</ModeBadge>
                )}
                {t.feature_flags.rides_enabled && <ModeBadge>Rides</ModeBadge>}
              </div>
              <div className="text-sm text-slate-700 tabular-nums">{t.user_count}</div>
              <div>
                <span
                  className={[
                    "text-[10px] px-1.5 py-0.5 rounded font-medium uppercase tracking-wider",
                    t.is_active
                      ? "bg-emerald-100 text-emerald-700"
                      : "bg-slate-100 text-slate-500",
                  ].join(" ")}
                >
                  {t.is_active ? "Active" : "Inactive"}
                </span>
              </div>
            </Link>
          ))}
      </div>
    </div>
  );
}

function ModeBadge({ children }: { children: React.ReactNode }) {
  return (
    <span className="text-[10px] px-1.5 py-0.5 rounded font-medium bg-slate-100 text-slate-700">
      {children}
    </span>
  );
}
