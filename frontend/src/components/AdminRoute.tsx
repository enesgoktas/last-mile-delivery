import { Navigate } from "react-router-dom";
import { isAdminLikeRole } from "../config/roles";
import { useAuth } from "../state/AuthContext";

interface AdminRouteProps {
  children: React.ReactNode;
  redirectTo?: string;
}

export function AdminRoute({ children, redirectTo = "/" }: AdminRouteProps) {
  const { role } = useAuth();

  if (!isAdminLikeRole(role)) {
    return <Navigate to={redirectTo} replace />;
  }

  return <>{children}</>;
}
