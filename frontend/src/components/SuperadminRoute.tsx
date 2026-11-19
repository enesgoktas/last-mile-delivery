import { Navigate } from "react-router-dom";
import { useAuth } from "../state/AuthContext";
import { isSuperadminRole } from "../config/roles";

export function SuperadminRoute({ children }: { children: React.ReactNode }) {
  const { token, role } = useAuth();
  if (!token) {
    return <Navigate to="/login" replace />;
  }
  if (!isSuperadminRole(role)) {
    return <Navigate to="/" replace />;
  }
  return <>{children}</>;
}
