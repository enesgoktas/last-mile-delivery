import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ToastProvider } from "../../state/ToastContext";
import { ToastContainer } from "../../components/Toast";

function renderSettings(ui: React.ReactElement) {
  return render(
    <MemoryRouter>
      <ToastProvider>
        {ui}
        <ToastContainer />
      </ToastProvider>
    </MemoryRouter>,
  );
}

const authState = { role: "ADMIN" };

vi.mock("../../state/AuthContext", () => ({
  useAuth: () => ({ token: "test-token", role: authState.role }),
}));

vi.mock("../settings/DepotsTab", () => ({
  DepotsTab: () => <div>Depots content</div>,
}));

vi.mock("../settings/OperationsPolicyTab", () => ({
  OperationsPolicyTab: () => <div>Operations content</div>,
}));

vi.mock("../settings/CouriersTab", () => ({
  CouriersTab: () => <div>Couriers content</div>,
}));

vi.mock("../settings/ConstraintsTab", () => ({
  ConstraintsTab: () => <div>Constraints content</div>,
}));

vi.mock("../settings/ApiKeysTab", () => ({
  ApiKeysTab: () => <div>API keys content</div>,
}));

vi.mock("../settings/WebhooksTab", () => ({
  WebhooksTab: () => <div>Webhooks content</div>,
}));

vi.mock("../settings/UsersTab", () => ({
  UsersTab: () => <div>Users content</div>,
}));

import { SettingsPage } from "../SettingsPage";

describe("SettingsPage", () => {
  beforeEach(() => {
    authState.role = "ADMIN";
  });

  it("renders the settings header and default tab", () => {
    renderSettings(<SettingsPage />);

    expect(screen.getByText("Settings")).toBeInTheDocument();
    expect(screen.getByText("Operations content")).toBeInTheDocument();
  });

  it("switches between tabs without rendering every panel at once", () => {
    renderSettings(<SettingsPage />);

    fireEvent.click(screen.getByRole("button", { name: "Depots" }));
    expect(screen.getByText("Depots content")).toBeInTheDocument();
    expect(screen.queryByText("Operations content")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Users" }));
    expect(screen.getByText("Users content")).toBeInTheDocument();
    expect(screen.queryByText("Depots content")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "API Keys" }));
    expect(screen.getByText("API keys content")).toBeInTheDocument();
    expect(screen.queryByText("Users content")).not.toBeInTheDocument();
  });

  it("shows the admin access guard for non-admin roles", () => {
    authState.role = "COURIER";

    renderSettings(<SettingsPage />);

    expect(screen.getByText(/Admin access required\./)).toBeInTheDocument();
    expect(screen.queryByText("Depots content")).not.toBeInTheDocument();
  });
});
