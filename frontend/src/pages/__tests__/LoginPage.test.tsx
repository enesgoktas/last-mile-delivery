import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { render } from "@testing-library/react";
import { LoginPage } from "../LoginPage";
import { login, signup, forgotPassword } from "../../api/client";
import { ToastProvider } from "../../state/ToastContext";
import { ToastContainer } from "../../components/Toast";

vi.mock("../../api/client", () => ({
  login: vi.fn(),
  signup: vi.fn(),
  forgotPassword: vi.fn(),
}));
const mockLogin = vi.mocked(login);
const mockSignup = vi.mocked(signup);
const mockForgotPassword = vi.mocked(forgotPassword);

const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => mockNavigate };
});

const mockLoginWithSession = vi.fn();
vi.mock("../../state/AuthContext", () => ({
  useAuth: () => ({ loginWithSession: mockLoginWithSession }),
}));

function renderLoginPage() {
  return render(
    <MemoryRouter>
      <ToastProvider>
        <LoginPage />
        <ToastContainer />
      </ToastProvider>
    </MemoryRouter>,
  );
}

describe("LoginPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("Sign In tab", () => {
    // Helper: submit the sign-in form directly (avoids ambiguity with tab button)
    function submitSignIn() {
      const emailInput = screen.getByLabelText(/^email$/i);
      fireEvent.submit(emailInput.closest("form")!);
    }

    it("renders the Sign In form by default", () => {
      renderLoginPage();
      expect(screen.getByLabelText(/^email$/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/^password$/i)).toBeInTheDocument();
      // Both the tab button and submit button say "Sign In" — just confirm at least one exists
      expect(screen.getAllByRole("button", { name: /sign in/i }).length).toBeGreaterThanOrEqual(1);
    });

    it("calls login API with correct credentials and navigates on success", async () => {
      mockLogin.mockResolvedValue({ access_token: "acc", refresh_token: "ref" } as never);
      renderLoginPage();

      fireEvent.change(screen.getByLabelText(/^email$/i), { target: { value: "ops@test.com" } });
      fireEvent.change(screen.getByLabelText(/^password$/i), { target: { value: "secret123" } });
      submitSignIn();

      await waitFor(() => expect(mockLogin).toHaveBeenCalledWith("ops@test.com", "secret123", "default"));
      expect(mockLoginWithSession).toHaveBeenCalledWith("acc", "ref");
      expect(mockNavigate).toHaveBeenCalledWith("/", { replace: true });
    });

    it("shows error message when login fails", async () => {
      mockLogin.mockRejectedValue(new Error("Invalid credentials"));
      renderLoginPage();

      fireEvent.change(screen.getByLabelText(/^email$/i), { target: { value: "bad@test.com" } });
      fireEvent.change(screen.getByLabelText(/^password$/i), { target: { value: "wrong" } });
      submitSignIn();

      expect(await screen.findByText("Invalid credentials")).toBeInTheDocument();
    });

    it("disables Sign In button while loading", async () => {
      let resolve: (v: unknown) => void;
      mockLogin.mockReturnValue(new Promise((r) => { resolve = r; }) as never);
      renderLoginPage();

      submitSignIn();

      await waitFor(() =>
        expect(screen.getByRole("button", { name: /signing in/i })).toBeDisabled(),
      );
      resolve!({ access_token: "a", refresh_token: "r" });
    });

    it("navigates to forgot password tab on link click", () => {
      renderLoginPage();
      fireEvent.click(screen.getByRole("button", { name: /forgot password/i }));
      expect(screen.getByText("Reset password")).toBeInTheDocument();
    });
  });

  describe("Create Account tab", () => {
    it("switches to signup form when Create Account tab is clicked", () => {
      renderLoginPage();
      fireEvent.click(screen.getByRole("button", { name: /create account/i }));
      expect(screen.getByLabelText(/company name/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/full name/i)).toBeInTheDocument();
    });

    it("calls signup API and navigates on success", async () => {
      mockSignup.mockResolvedValue({ access_token: "acc2", refresh_token: "ref2" } as never);
      renderLoginPage();

      fireEvent.click(screen.getByRole("button", { name: /create account/i }));

      fireEvent.change(screen.getByLabelText(/company name/i), { target: { value: "Acme Corp" } });
      fireEvent.change(screen.getByLabelText(/full name/i), { target: { value: "Jordan Lee" } });
      fireEvent.change(screen.getByLabelText(/work email/i), { target: { value: "jordan@acme.com" } });

      // Signup has two password inputs (Password + Confirm password)
      const pwInput = screen.getByLabelText(/^password$/i);
      const confirmInput = screen.getByLabelText(/^confirm$/i);
      fireEvent.change(pwInput, { target: { value: "Str0ng!Pass" } });
      fireEvent.change(confirmInput, { target: { value: "Str0ng!Pass" } });

      fireEvent.submit(pwInput.closest("form")!);

      await waitFor(() =>
        expect(mockSignup).toHaveBeenCalledWith({
          company_name: "Acme Corp",
          full_name: "Jordan Lee",
          email: "jordan@acme.com",
          password: "Str0ng!Pass",
        }),
      );
      expect(mockNavigate).toHaveBeenCalledWith("/", { replace: true });
    });

    it("shows error when passwords do not match", async () => {
      renderLoginPage();
      fireEvent.click(screen.getByRole("button", { name: /create account/i }));

      const pwInput = screen.getByLabelText(/^password$/i);
      const confirmInput = screen.getByLabelText(/^confirm$/i);
      fireEvent.change(pwInput, { target: { value: "Password1!" } });
      fireEvent.change(confirmInput, { target: { value: "DifferentPass1!" } });

      fireEvent.submit(pwInput.closest("form")!);

      expect(await screen.findByText("Passwords do not match")).toBeInTheDocument();
      expect(mockSignup).not.toHaveBeenCalled();
    });

    it("shows API error when signup fails", async () => {
      mockSignup.mockRejectedValue(new Error("Email already registered"));
      renderLoginPage();

      fireEvent.click(screen.getByRole("button", { name: /create account/i }));
      const pwInput = screen.getByLabelText(/^password$/i);
      const confirmInput = screen.getByLabelText(/^confirm$/i);
      fireEvent.change(pwInput, { target: { value: "Str0ng!Pass" } });
      fireEvent.change(confirmInput, { target: { value: "Str0ng!Pass" } });

      fireEvent.submit(pwInput.closest("form")!);

      expect(await screen.findByText("Email already registered")).toBeInTheDocument();
    });
  });

  describe("Forgot Password tab", () => {
    it("shows success message after forgot password request", async () => {
      mockForgotPassword.mockResolvedValue(undefined as never);
      renderLoginPage();

      fireEvent.click(screen.getByRole("button", { name: /forgot password/i }));

      const emailInput = screen.getByLabelText(/^email$/i);
      fireEvent.change(emailInput, { target: { value: "ops@test.com" } });
      fireEvent.submit(screen.getByRole("button", { name: /send reset link/i }));

      await waitFor(() => expect(mockForgotPassword).toHaveBeenCalledWith("ops@test.com"));
      expect(
        await screen.findByText(/reset link has been sent/i),
      ).toBeInTheDocument();
    });

    it("shows error when forgot password request fails", async () => {
      mockForgotPassword.mockRejectedValue(new Error("Too many requests"));
      renderLoginPage();

      fireEvent.click(screen.getByRole("button", { name: /forgot password/i }));
      fireEvent.submit(screen.getByRole("button", { name: /send reset link/i }));

      expect(await screen.findByText("Too many requests")).toBeInTheDocument();
    });

    it("can navigate back to Sign In from the forgot tab", () => {
      renderLoginPage();
      fireEvent.click(screen.getByRole("button", { name: /forgot password/i }));
      fireEvent.click(screen.getByRole("button", { name: /back to sign in/i }));
      // After returning to sign-in tab, email field is visible again
      expect(screen.getByLabelText(/^email$/i)).toBeInTheDocument();
    });
  });
});
