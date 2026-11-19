import { Navigate } from "react-router-dom";
import { useAuth } from "../state/AuthContext";
import { isBackofficeRole, isSuperadminRole } from "../config/roles";

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const { token, role } = useAuth();
  if (!token) {
    return <Navigate to="/login" replace />;
  }
  // Platform admins live at /admin/* and have no business in the per-tenant
  // dispatcher dashboard at /. Send them home.
  if (isSuperadminRole(role)) {
    return <Navigate to="/admin" replace />;
  }
  if (!isBackofficeRole(role)) {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
}
