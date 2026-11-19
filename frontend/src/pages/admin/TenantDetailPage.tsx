import { useEffect, useState } from "react";
import { NavLink, Outlet, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { Link } from "react-router-dom";
import { useAuth } from "../../state/AuthContext";
import { getTenant } from "../../api/superadmin";
import type { SuperadminTenantDetail } from "../../types";

const SUB_NAV = [
  { to: "", label: "Overview", end: true },
  { to: "settings", label: "Settings", end: false },
  { to: "users", label: "Users", end: false },
  { to: "branding", label: "Branding", end: false },
];

export function TenantDetailPage() {
  const { tenantId } = useParams<{ tenantId: string }>();
  const { token } = useAuth();
  const [tenant, setTenant] = useState<SuperadminTenantDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token || !tenantId) return;
    let cancelled = false;
    getTenant(token, tenantId)
      .then((r) => {
        if (!cancelled) setTenant(r);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load");
      });
    return () => {
      cancelled = true;
    };
  }, [token, tenantId]);

  return (
    <div className="px-8 py-8 max-w-6xl mx-auto w-full">
      <Link
        to="/admin/tenants"
        className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-700 mb-3"
      >
        <ArrowLeft size={12} />
        Back to tenants
      </Link>

      {error && (
        <div className="mb-4 px-3 py-2 bg-red-50 border border-red-200 text-red-700 text-sm rounded">
          {error}
        </div>
      )}

      <div className="mb-6">
        <h1 className="text-xl font-semibold text-slate-900">
          {tenant?.name ?? (error ? "—" : "Loading…")}
        </h1>
        <p className="text-sm text-slate-500 mt-0.5">
          {tenant?.slug ?? ""}
        </p>
      </div>

      <div className="border-b border-slate-200 mb-6">
        <nav className="flex gap-4">
          {SUB_NAV.map(({ to, label, end }) => (
            <NavLink
              key={to || "overview"}
              to={to}
              end={end}
              className={({ isActive }) =>
                [
                  "py-2 text-sm transition-colors border-b-2 -mb-px",
                  isActive
                    ? "text-slate-900 font-semibold border-accent-500"
                    : "text-slate-500 hover:text-slate-800 border-transparent",
                ].join(" ")
              }
            >
              {label}
            </NavLink>
          ))}
        </nav>
      </div>

      <Outlet context={{ tenant }} />
    </div>
  );
}
