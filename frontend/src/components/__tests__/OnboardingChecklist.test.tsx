import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { OnboardingChecklist } from "../OnboardingChecklist";

const authState = {
  role: "ADMIN",
};

vi.mock("../../state/AuthContext", () => ({
  useAuth: () => ({
    role: authState.role,
  }),
}));

function renderChecklist() {
  return render(
    <MemoryRouter>
      <OnboardingChecklist />
    </MemoryRouter>,
  );
}

describe("OnboardingChecklist", () => {
  beforeEach(() => {
    authState.role = "ADMIN";
    const storage = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => {
        storage.set(key, value);
      },
      removeItem: (key: string) => {
        storage.delete(key);
      },
    });
  });

  it("shows the depot settings step to admin users", () => {
    renderChecklist();

    expect(screen.getByText("Create your first depot")).toBeInTheDocument();
    expect(screen.getByText("0 / 4 steps complete")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to Settings → Depots →" })).toHaveAttribute(
      "href",
      "/settings?tab=Depots",
    );
  });

  it("hides admin-only onboarding steps from dispatchers", () => {
    authState.role = "DISPATCHER";

    renderChecklist();

    expect(screen.queryByText("Create your first depot")).not.toBeInTheDocument();
    expect(screen.getByText("0 / 3 steps complete")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Settings/i })).not.toBeInTheDocument();
  });
});
