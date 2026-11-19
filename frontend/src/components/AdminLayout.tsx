import { NavLink, Outlet, useLocation } from "react-router-dom";
import { Building2, Home, LogOut, ShieldCheck, Zap } from "lucide-react";
import { useAuth } from "../state/AuthContext";
import { ToastContainer } from "./Toast";

const NAV = [
  { to: "/admin", label: "Overview", icon: Home, exact: true },
  { to: "/admin/tenants", label: "Tenants", icon: Building2, exact: false },
  { to: "/admin/charging-stations", label: "Chargers", icon: Zap, exact: false },
];

export function AdminLayout() {
  const { logout, role } = useAuth();
  const location = useLocation();

  return (
    <div className="flex h-screen overflow-hidden bg-white">
      <aside className="w-[200px] shrink-0 flex flex-col bg-slate-50 border-r border-slate-200">
        <div className="h-14 flex items-center gap-2 px-4 shrink-0">
          <ShieldCheck size={16} className="text-accent-500" />
          <span className="text-[13px] font-bold text-slate-900 tracking-tight">
            Avernsys Platform
          </span>
        </div>

        <div className="flex-1 flex flex-col px-3 pt-0.5 gap-0.5">
          <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-400 px-0 mb-1">
            Control plane
          </p>
          {NAV.map(({ to, label, icon: Icon, exact }) => {
            const active = exact
              ? location.pathname === to
              : location.pathname.startsWith(to);
            return (
              <NavLink
                key={to}
                to={to}
                end={exact}
                className={[
                  "group flex items-center gap-2.5 h-9 text-sm transition-colors duration-100",
                  active
                    ? "text-slate-900 font-semibold"
                    : "text-slate-500 hover:text-slate-800",
                ].join(" ")}
              >
                <span
                  className={[
                    "w-7 h-7 flex items-center justify-center rounded-md shrink-0 transition-colors duration-100",
                    active
                      ? "bg-accent-500 text-white"
                      : "text-slate-400 group-hover:text-slate-600 group-hover:bg-slate-100",
                  ].join(" ")}
                >
                  <Icon size={15} strokeWidth={active ? 2.25 : 1.75} />
                </span>
                {label}
              </NavLink>
            );
          })}
        </div>

        <div className="px-3 pb-3 shrink-0">
          <div className="border-t border-slate-200 pt-2 mt-1 flex items-center gap-2">
            <span className="flex-1 text-[11px] text-slate-400 truncate capitalize leading-none pl-0.5">
              {role?.toLowerCase() ?? "user"}
            </span>
            <button
              onClick={() => void logout()}
              title="Sign out"
              className="p-1 rounded text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-colors duration-100"
            >
              <LogOut size={13} />
            </button>
          </div>
        </div>
      </aside>

      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <header className="h-14 shrink-0 flex items-center px-5 border-b border-slate-200 bg-white">
          <span className="text-[13px] font-medium text-slate-700">
            Platform admin
          </span>
        </header>
        <div className="flex-1 overflow-auto">
          <Outlet />
        </div>
      </div>

      <ToastContainer />
    </div>
  );
}
