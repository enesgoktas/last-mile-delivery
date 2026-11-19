import { Navigate, Route, Routes } from "react-router-dom";
import { AdminLayout } from "./components/AdminLayout";
import { AdminRoute } from "./components/AdminRoute";
import { AppLayout } from "./components/AppLayout";
import { AuthGuard } from "./components/AuthGuard";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { SuperadminRoute } from "./components/SuperadminRoute";
import { AcceptInvitePage } from "./pages/AcceptInvitePage";
import { GuidePage } from "./pages/GuidePage";
import { WalkthroughPage } from "./pages/WalkthroughPage";
import { LoginPage } from "./pages/LoginPage";
import { ResetPasswordPage } from "./pages/ResetPasswordPage";
import { LiveOpsPage } from "./pages/LiveOpsPage";
import { OrdersPage } from "./pages/OrdersPage";
import { RecurringOrdersPage } from "./pages/RecurringOrdersPage";
import { RidesPage } from "./pages/RidesPage";
import { RidesReportsPage } from "./pages/RidesReportsPage";
import { FleetPage } from "./pages/FleetPage";
import { InsightsPage } from "./pages/InsightsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { AdminLandingPage } from "./pages/admin/AdminLandingPage";
import { ChargingStationsTab } from "./pages/admin/ChargingStationsTab";
import { CreateTenantWizardPage } from "./pages/admin/CreateTenantWizardPage";
import { TenantBrandingTab } from "./pages/admin/TenantBrandingTab";
import { TenantDetailPage } from "./pages/admin/TenantDetailPage";
import { TenantOverviewTab } from "./pages/admin/TenantOverviewTab";
import { TenantSettingsTab } from "./pages/admin/TenantSettingsTab";
import { TenantUsersTab } from "./pages/admin/TenantUsersTab";
import { TenantsListPage } from "./pages/admin/TenantsListPage";

function ProtectedLayout() {
  return (
    <AuthGuard>
      <ErrorBoundary>
        <AppLayout />
      </ErrorBoundary>
    </AuthGuard>
  );
}

export default function App() {
  return (
    <Routes>
      <Route
        path="/login"
        element={
          <ErrorBoundary>
            <LoginPage />
          </ErrorBoundary>
        }
      />
      <Route
        path="/reset-password"
        element={
          <ErrorBoundary>
            <ResetPasswordPage />
          </ErrorBoundary>
        }
      />
      <Route
        path="/accept-invite"
        element={
          <ErrorBoundary>
            <AcceptInvitePage />
          </ErrorBoundary>
        }
      />
      <Route
        path="/guide"
        element={
          <ErrorBoundary>
            <GuidePage />
          </ErrorBoundary>
        }
      />
      <Route
        path="/walkthrough"
        element={
          <ErrorBoundary>
            <WalkthroughPage />
          </ErrorBoundary>
        }
      />
      <Route
        path="/admin"
        element={
          <SuperadminRoute>
            <ErrorBoundary>
              <AdminLayout />
            </ErrorBoundary>
          </SuperadminRoute>
        }
      >
        <Route index element={<AdminLandingPage />} />
        <Route path="tenants" element={<TenantsListPage />} />
        <Route path="tenants/new" element={<CreateTenantWizardPage />} />
        <Route path="tenants/:tenantId" element={<TenantDetailPage />}>
          <Route index element={<TenantOverviewTab />} />
          <Route path="settings" element={<TenantSettingsTab />} />
          <Route path="users" element={<TenantUsersTab />} />
          <Route path="branding" element={<TenantBrandingTab />} />
        </Route>
        <Route path="charging-stations" element={<ChargingStationsTab />} />
      </Route>
      <Route path="/" element={<ProtectedLayout />}>
        <Route index element={<LiveOpsPage />} />
        <Route path="orders" element={<OrdersPage />} />
        <Route path="recurring-orders" element={<RecurringOrdersPage />} />
        <Route path="rides" element={<RidesPage />} />
        <Route path="rides/reports" element={<RidesReportsPage />} />
        <Route path="fleet" element={<FleetPage />} />
        <Route path="insights" element={<InsightsPage />} />
        <Route
          path="settings"
          element={
            <AdminRoute>
              <SettingsPage />
            </AdminRoute>
          }
        />
        {/* Legacy redirects */}
        <Route path="couriers" element={<Navigate to="/fleet" replace />} />
        <Route path="analytics" element={<Navigate to="/insights" replace />} />
        <Route path="failed-orders" element={<Navigate to="/orders" replace />} />
        <Route path="sla-alerts" element={<Navigate to="/" replace />} />
        <Route path="audit-log" element={<Navigate to="/settings" replace />} />
        <Route path="integrations" element={<Navigate to="/settings" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
