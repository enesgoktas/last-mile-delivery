import { getLoginFailureMessage } from "../loginHelpers";
import { validateLoginInput } from "../loginHelpers";

describe("getLoginFailureMessage", () => {
  it("maps invalid credentials to a friendly message", () => {
    const error = { status: 401 };
    expect(getLoginFailureMessage(error)).toBe(
      "Invalid credentials. Check your tenant, email, and password.",
    );
  });

  it("maps forbidden sign-in to account guidance", () => {
    const error = { status: 403 };
    expect(getLoginFailureMessage(error)).toBe(
      "Your account cannot sign in right now. Contact your dispatcher.",
    );
  });

  it("maps backend reachability error to network guidance", () => {
    const error = new Error(
      "Cannot reach API at http://192.168.1.6:8000. Confirm the courier backend is reachable from this device.",
    );
    expect(getLoginFailureMessage(error)).toBe(
      "Cannot reach the backend. Check your API URL and network connection.",
    );
  });

  it("keeps other explicit error messages", () => {
    expect(getLoginFailureMessage(new Error("Rate limited"))).toBe("Rate limited");
  });

  it("falls back to unknown error", () => {
    expect(getLoginFailureMessage(null)).toBe("Unknown error");
  });
});

describe("validateLoginInput", () => {
  it("requires tenant", () => {
    expect(validateLoginInput("", "courier@example.com", "secret")).toBe("Tenant is required.");
  });

  it("requires valid email", () => {
    expect(validateLoginInput("default", "courier-example.com", "secret")).toBe(
      "Enter a valid email address.",
    );
  });

  it("accepts tenant and email with extra whitespace", () => {
    expect(validateLoginInput("  default  ", "  courier@example.com  ", "secret")).toBeNull();
  });

  it("requires password", () => {
    expect(validateLoginInput("default", "courier@example.com", "")).toBe(
      "Password is required.",
    );
  });

  it("returns null for valid input", () => {
    expect(validateLoginInput("default", "courier@example.com", "secret")).toBeNull();
  });
});
