import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import App from "./App";

const authState = {
  token: "test-token",
  role: "DISPATCHER",
};

vi.mock("./state/AuthContext", () => ({
  useAuth: () => ({
    token: authState.token,
    role: authState.role,
  }),
}));

vi.mock("./components/AppLayout", async () => {
  const { Outlet } = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    AppLayout: () => (
      <div>
        <span>App Layout</span>
        <Outlet />
      </div>
    ),
  };
});

vi.mock("./components/ErrorBoundary", () => ({
  ErrorBoundary: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

vi.mock("./pages/LoginPage", () => ({
  LoginPage: () => <div>Login Page</div>,
}));

vi.mock("./pages/ResetPasswordPage", () => ({
  ResetPasswordPage: () => <div>Reset Password Page</div>,
}));

vi.mock("./pages/AcceptInvitePage", () => ({
  AcceptInvitePage: () => <div>Accept Invite Page</div>,
}));

vi.mock("./pages/LiveOpsPage", () => ({
  LiveOpsPage: () => <div>Live Ops Page</div>,
}));

vi.mock("./pages/OrdersPage", () => ({
  OrdersPage: () => <div>Orders Page</div>,
}));

vi.mock("./pages/CouriersPage", () => ({
  CouriersPage: () => <div>Couriers Page</div>,
}));

vi.mock("./pages/FailedOrdersPage", () => ({
  FailedOrdersPage: () => <div>Failed Orders Page</div>,
}));

vi.mock("./pages/AnalyticsPage", () => ({
  AnalyticsPage: () => <div>Analytics Page</div>,
}));

vi.mock("./pages/SettingsPage", () => ({
  SettingsPage: () => <div>Settings Page</div>,
}));

vi.mock("./pages/SLAAlertsPage", () => ({
  SLAAlertsPage: () => <div>SLA Alerts Page</div>,
}));

vi.mock("./pages/AuditLogPage", () => ({
  AuditLogPage: () => <div>Audit Log Page</div>,
}));

function renderApp(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <App />
    </MemoryRouter>,
  );
}

describe("App admin-only routing", () => {
  beforeEach(() => {
    authState.token = "test-token";
    authState.role = "DISPATCHER";
  });

  it.each(["/settings", "/audit-log", "/integrations"])(
    "redirects non-admin users away from %s",
    (path) => {
      renderApp(path);

      expect(screen.getByText("Live Ops Page")).toBeInTheDocument();
      expect(screen.queryByText("Settings Page")).not.toBeInTheDocument();
      expect(screen.queryByText("Audit Log Page")).not.toBeInTheDocument();
    },
  );

  it("allows admin users into settings", () => {
    authState.role = "ADMIN";

    renderApp("/settings");

    expect(screen.getByText("Settings Page")).toBeInTheDocument();
  });
});
