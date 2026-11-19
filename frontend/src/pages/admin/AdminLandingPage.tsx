import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Building2, CheckCircle2, Plus, Users } from "lucide-react";
import { useAuth } from "../../state/AuthContext";
import { listTenants } from "../../api/superadmin";
import type { SuperadminTenantSummary } from "../../types";

export function AdminLandingPage() {
  const { token } = useAuth();
  const [tenants, setTenants] = useState<SuperadminTenantSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    listTenants(token)
      .then((result) => {
        if (!cancelled) setTenants(result);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load tenants");
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const stats = useMemo(() => {
    if (!tenants) return null;
    return {
      total: tenants.length,
      active: tenants.filter((t) => t.is_active).length,
      ridesEnabled: tenants.filter((t) => t.feature_flags.rides_enabled).length,
      users: tenants.reduce((sum, t) => sum + t.user_count, 0),
    };
  }, [tenants]);

  return (
    <div className="px-8 py-8 max-w-6xl mx-auto w-full">
      <div className="flex items-baseline justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Overview</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Cross-tenant view of every company on the platform.
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

      {error && (
        <div className="mb-4 px-3 py-2 bg-red-50 border border-red-200 text-red-700 text-sm rounded">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-3 mb-8">
        <StatCard
          icon={Building2}
          label="Tenants"
          value={stats?.total ?? "—"}
          loading={tenants === null}
        />
        <StatCard
          icon={CheckCircle2}
          label="Active"
          value={stats?.active ?? "—"}
          loading={tenants === null}
        />
        <StatCard
          icon={Users}
          label="Users"
          value={stats?.users ?? "—"}
          loading={tenants === null}
        />
        <StatCard
          icon={Building2}
          label="Rides enabled"
          value={stats?.ridesEnabled ?? "—"}
          loading={tenants === null}
        />
      </div>

      <h2 className="text-sm font-semibold text-slate-700 mb-2">Recent tenants</h2>
      <div className="border border-slate-200 rounded-md overflow-hidden">
        {tenants === null && (
          <div className="px-4 py-6 text-sm text-slate-400">Loading…</div>
        )}
        {tenants && tenants.length === 0 && (
          <div className="px-4 py-6 text-sm text-slate-400">
            No tenants yet. Create one to get started.
          </div>
        )}
        {tenants &&
          tenants.slice(0, 5).map((t) => (
            <Link
              key={t.id}
              to={`/admin/tenants/${t.id}`}
              className="flex items-center justify-between px-4 py-3 border-b border-slate-100 last:border-b-0 hover:bg-slate-50 transition-colors"
            >
              <div>
                <div className="text-sm font-medium text-slate-900">{t.name}</div>
                <div className="text-xs text-slate-500 mt-0.5">{t.slug}</div>
              </div>
              <div className="flex items-center gap-3">
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
                <span className="text-xs text-slate-500">{t.user_count} users</span>
              </div>
            </Link>
          ))}
      </div>
    </div>
  );
}

function StatCard({
  icon: Icon,
  label,
  value,
  loading,
}: {
  icon: typeof Building2;
  label: string;
  value: number | string;
  loading: boolean;
}) {
  return (
    <div className="border border-slate-200 rounded-md px-4 py-3 bg-white">
      <div className="flex items-center gap-2 text-slate-400 text-xs">
        <Icon size={13} />
        {label}
      </div>
      <div className="mt-1 text-2xl font-semibold text-slate-900 tabular-nums">
        {loading ? "—" : value}
      </div>
    </div>
  );
}
