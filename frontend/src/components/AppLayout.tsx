import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useState, useCallback, useMemo } from "react";
import {
  Map,
  Package,
  Repeat,
  Truck,
  BarChart2,
  Settings,
  LogOut,
  Bell,
  Users,
} from "lucide-react";
import { useAuth } from "../state/AuthContext";
import { usePolling } from "../hooks/usePolling";
import { useFeatures } from "../hooks/useFeatures";
import { listSLAAlerts } from "../api/client";
import { ToastContainer } from "./Toast";
import { OnboardingChecklist } from "./OnboardingChecklist";
import { NotificationPanel } from "./NotificationPanel";
import { isAdminLikeRole } from "../config/roles";
import { AssignProvider } from "../state/AssignContext";
import { AssignProgressBar } from "./AssignProgressBar";

type NavItem = { to: string; label: string; icon: typeof Map; exact: boolean };

const BASE_NAV: NavItem[] = [
  { to: "/", label: "Command", icon: Map, exact: true },
];
const DELIVERY_NAV: NavItem[] = [
  { to: "/orders", label: "Orders", icon: Package, exact: false },
  { to: "/recurring-orders", label: "Recurring", icon: Repeat, exact: false },
];
const RIDES_NAV: NavItem[] = [
  { to: "/rides", label: "Rides", icon: Users, exact: false },
];
const COMMON_NAV: NavItem[] = [
  { to: "/fleet", label: "Fleet", icon: Truck, exact: false },
  { to: "/insights", label: "Insights", icon: BarChart2, exact: false },
];

function SidebarLink({
  to,
  label,
  icon: Icon,
  exact,
  active,
}: {
  to: string;
  label: string;
  icon: typeof Map;
  exact: boolean;
  active: boolean;
}) {
  return (
    <NavLink
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
}

export function AppLayout() {
  const { logout, role, token } = useAuth();
  const location = useLocation();
  const [alertsOpen, setAlertsOpen] = useState(false);

  const fetchAlertCount = useCallback(
    () => (token ? listSLAAlerts(token, "CRITICAL") : Promise.resolve([])),
    [token],
  );
  const { data: criticalAlerts } = usePolling(fetchAlertCount, 60_000);
  const criticalCount = (criticalAlerts ?? []).filter((a) => a.status === "ACTIVE").length;

  const isAdmin = isAdminLikeRole(role);
  const features = useFeatures();

  // Build nav from flags so a rides-only tenant doesn't see Orders, etc.
  const NAV = useMemo<NavItem[]>(() => {
    const items = [...BASE_NAV];
    if (features.deliveries_enabled) items.push(...DELIVERY_NAV);
    if (features.rides_enabled) items.push(...RIDES_NAV);
    items.push(...COMMON_NAV);
    return items;
  }, [features.deliveries_enabled, features.rides_enabled]);

  return (
    <AssignProvider>
    <div className="flex h-screen overflow-hidden bg-white">
      {/* Sidebar */}
      <aside className="w-[196px] shrink-0 flex flex-col bg-slate-50 border-r border-slate-200">
        {/* Brand */}
        <div className="h-14 flex items-center gap-2 px-4 shrink-0">
          <span className="text-[13px] font-bold text-slate-900 tracking-tight">FleetFlow</span>
        </div>

        {/* Nav */}
        <div className="flex-1 flex flex-col px-3 pt-0.5 gap-0.5">
          <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-400 px-0 mb-1">Navigate</p>
          {NAV.map(({ to, label, icon, exact }) => (
            <SidebarLink
              key={to}
              to={to}
              label={label}
              icon={icon}
              exact={exact}
              active={exact ? location.pathname === to : location.pathname.startsWith(to)}
            />
          ))}
        </div>

        {/* Footer */}
        <div className="px-3 pb-3 shrink-0 space-y-0.5">
          {isAdmin && (
            <NavLink
              to="/settings"
              className={({ isActive }) =>
                [
                  "group flex items-center gap-2.5 h-9 text-sm transition-colors duration-100",
                  isActive
                    ? "text-slate-900 font-semibold"
                    : "text-slate-500 hover:text-slate-800",
                ].join(" ")
              }
            >
              {({ isActive }) => (
                <>
                  <span
                    className={[
                      "w-7 h-7 flex items-center justify-center rounded-md shrink-0 transition-colors duration-100",
                      isActive
                        ? "bg-accent-500 text-white"
                        : "text-slate-400 group-hover:text-slate-600 group-hover:bg-slate-100",
                    ].join(" ")}
                  >
                    <Settings size={15} strokeWidth={1.75} />
                  </span>
                  Settings
                </>
              )}
            </NavLink>
          )}

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

      {/* Main */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Top bar */}
        <header className="h-14 shrink-0 flex items-center justify-end px-5 border-b border-slate-200 bg-white">
          <button
            onClick={() => setAlertsOpen(true)}
            title="SLA Alerts"
            className="relative p-1.5 rounded text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors duration-100"
          >
            <Bell size={17} />
            {criticalCount > 0 && (
              <span className="absolute top-1 right-1 w-1.5 h-1.5 rounded-full bg-red-500 pulse-dot" />
            )}
          </button>
        </header>

        <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
          <Outlet />
        </div>
      </div>

      <NotificationPanel open={alertsOpen} onClose={() => setAlertsOpen(false)} />
      <ToastContainer />
      <OnboardingChecklist />
      <AssignProgressBar />
    </div>
    </AssignProvider>
  );
}
