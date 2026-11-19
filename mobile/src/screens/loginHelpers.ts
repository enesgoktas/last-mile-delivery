const NETWORK_ERROR_PREFIX = "Cannot reach API";

export function validateLoginInput(tenantSlug: string, email: string, password: string): string | null {
  const normalizedTenantSlug = tenantSlug.trim();
  const normalizedEmail = email.trim();
  if (!normalizedTenantSlug) {
    return "Tenant is required.";
  }
  if (!normalizedEmail || !normalizedEmail.includes("@")) {
    return "Enter a valid email address.";
  }
  if (!password) {
    return "Password is required.";
  }
  return null;
}

export function getLoginFailureMessage(error: unknown): string {
  const status =
    typeof error === "object" && error !== null && "status" in error
      ? (error as { status?: unknown }).status
      : undefined;
  if (status === 401) {
    return "Invalid credentials. Check your tenant, email, and password.";
  }
  if (status === 403) {
    return "Your account cannot sign in right now. Contact your dispatcher.";
  }

  if (error instanceof Error && error.message.startsWith(NETWORK_ERROR_PREFIX)) {
    return "Cannot reach the backend. Check your API URL and network connection.";
  }

  if (error instanceof Error && error.message.trim()) {
    return error.message;
  }

  return "Unknown error";
}
