import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { UsersTab } from "../UsersTab";
import { inviteUser, listUsers, updateUser } from "../../../api/client";
import { usePolling } from "../../../hooks/usePolling";
import { ToastProvider } from "../../../state/ToastContext";
import { ToastContainer } from "../../../components/Toast";

vi.mock("../../../state/AuthContext", () => ({
  useAuth: () => ({ token: "tok" }),
}));

vi.mock("../../../hooks/usePolling");
const mockUsePolling = vi.mocked(usePolling);

vi.mock("../../../api/client", () => ({
  listUsers: vi.fn(),
  inviteUser: vi.fn(),
  updateUser: vi.fn(),
}));
const mockInviteUser = vi.mocked(inviteUser);
const mockUpdateUser = vi.mocked(updateUser);

const noUsers = { data: null, isLoading: false, error: null, refetch: vi.fn() };

function buildUser(overrides = {}) {
  return {
    id: "u1",
    tenant_id: "t1",
    email: "alice@test.com",
    role: "DISPATCHER",
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function renderTab() {
  return render(
    <ToastProvider>
      <UsersTab />
      <ToastContainer />
    </ToastProvider>,
  );
}

describe("UsersTab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUsePolling.mockReturnValue(noUsers);
  });

  it("shows loading spinner while users are fetching", () => {
    mockUsePolling.mockReturnValue({ data: null, isLoading: true, error: null, refetch: vi.fn() });
    renderTab();
    expect(screen.getByText(/loading users/i)).toBeInTheDocument();
  });

  it("shows empty state when no users exist", () => {
    mockUsePolling.mockReturnValue({ data: [], isLoading: false, error: null, refetch: vi.fn() });
    renderTab();
    expect(screen.getByText("No users yet.")).toBeInTheDocument();
  });

  it("renders user rows when data is available", () => {
    mockUsePolling.mockReturnValue({
      data: [buildUser()],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    renderTab();
    expect(screen.getByText("alice@test.com")).toBeInTheDocument();
    expect(screen.getByText("DISPATCHER")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
  });

  it("filters users by search query", () => {
    mockUsePolling.mockReturnValue({
      data: [buildUser({ email: "alice@test.com" }), buildUser({ id: "u2", email: "bob@test.com" })],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    renderTab();

    fireEvent.change(screen.getByPlaceholderText(/search by email/i), { target: { value: "bob" } });

    expect(screen.getByText("bob@test.com")).toBeInTheDocument();
    expect(screen.queryByText("alice@test.com")).not.toBeInTheDocument();
  });

  it("shows 'No users match' when search has no results", () => {
    mockUsePolling.mockReturnValue({
      data: [buildUser()],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    renderTab();
    fireEvent.change(screen.getByPlaceholderText(/search by email/i), { target: { value: "zzz" } });
    expect(screen.getByText("No users match your search.")).toBeInTheDocument();
  });

  describe("Invite modal", () => {
    it("opens the invite modal when + Invite User is clicked", () => {
      renderTab();
      fireEvent.click(screen.getByRole("button", { name: /invite user/i }));
      expect(screen.getByText("Invite User")).toBeInTheDocument();
      expect(screen.getByLabelText(/^email$/i)).toBeInTheDocument();
    });

    it("sends invite and shows success confirmation", async () => {
      const refetch = vi.fn();
      mockUsePolling.mockReturnValue({ data: [], isLoading: false, error: null, refetch });
      mockInviteUser.mockResolvedValue(undefined as never);

      renderTab();
      fireEvent.click(screen.getByRole("button", { name: /invite user/i }));

      fireEvent.change(screen.getByLabelText(/^email$/i), { target: { value: "new@test.com" } });
      fireEvent.submit(screen.getByRole("button", { name: /send invitation/i }));

      await waitFor(() =>
        expect(mockInviteUser).toHaveBeenCalledWith("tok", { email: "new@test.com", role: "DISPATCHER" }),
      );
      expect(await screen.findByText(/invitation sent to/i)).toBeInTheDocument();
      expect(await screen.findByText("Invitation sent")).toBeInTheDocument();
      expect(refetch).toHaveBeenCalled();
    });

    it("shows error in modal when invite fails", async () => {
      mockInviteUser.mockRejectedValue(new Error("Email already invited"));
      renderTab();
      fireEvent.click(screen.getByRole("button", { name: /invite user/i }));

      fireEvent.change(screen.getByLabelText(/^email$/i), { target: { value: "dup@test.com" } });
      fireEvent.submit(screen.getByRole("button", { name: /send invitation/i }));

      expect(await screen.findByText("Email already invited")).toBeInTheDocument();
    });

    it("closes the modal when Cancel is clicked", () => {
      renderTab();
      fireEvent.click(screen.getByRole("button", { name: /invite user/i }));
      fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
      expect(screen.queryByText("Invite User")).not.toBeInTheDocument();
    });
  });

  describe("Edit modal", () => {
    it("opens the edit modal when Edit is clicked", () => {
      mockUsePolling.mockReturnValue({
        data: [buildUser()],
        isLoading: false,
        error: null,
        refetch: vi.fn(),
      });
      renderTab();
      fireEvent.click(screen.getByRole("button", { name: /edit/i }));
      expect(screen.getByText("Edit User")).toBeInTheDocument();
      // alice@test.com appears in both the table row and the modal — just assert at least one exists
      expect(screen.getAllByText("alice@test.com").length).toBeGreaterThanOrEqual(1);
    });

    it("calls updateUser and shows success toast", async () => {
      const refetch = vi.fn();
      mockUsePolling.mockReturnValue({
        data: [buildUser()],
        isLoading: false,
        error: null,
        refetch,
      });
      mockUpdateUser.mockResolvedValue(undefined as never);

      renderTab();
      fireEvent.click(screen.getByRole("button", { name: /edit/i }));
      fireEvent.submit(screen.getByRole("button", { name: /save changes/i }));

      await waitFor(() =>
        expect(mockUpdateUser).toHaveBeenCalledWith(
          "tok",
          "u1",
          expect.objectContaining({ role: "DISPATCHER", is_active: true }),
        ),
      );
      expect(await screen.findByText("User updated")).toBeInTheDocument();
      expect(refetch).toHaveBeenCalled();
    });

    it("shows error inside edit modal when update fails", async () => {
      mockUsePolling.mockReturnValue({
        data: [buildUser()],
        isLoading: false,
        error: null,
        refetch: vi.fn(),
      });
      mockUpdateUser.mockRejectedValue(new Error("Permission denied"));

      renderTab();
      fireEvent.click(screen.getByRole("button", { name: /edit/i }));
      fireEvent.submit(screen.getByRole("button", { name: /save changes/i }));

      expect(await screen.findByText("Permission denied")).toBeInTheDocument();
    });
  });

  describe("Toggle active", () => {
    it("calls updateUser to deactivate an active user", async () => {
      const refetch = vi.fn();
      mockUsePolling.mockReturnValue({
        data: [buildUser({ is_active: true })],
        isLoading: false,
        error: null,
        refetch,
      });
      mockUpdateUser.mockResolvedValue(undefined as never);

      renderTab();
      fireEvent.click(screen.getByRole("button", { name: /active/i }));

      await waitFor(() =>
        expect(mockUpdateUser).toHaveBeenCalledWith("tok", "u1", { is_active: false }),
      );
      expect(await screen.findByText("User deactivated")).toBeInTheDocument();
    });
  });
});
