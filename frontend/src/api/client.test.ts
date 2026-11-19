import { afterEach, describe, expect, it, vi } from "vitest";

type StorageState = {
  accessToken: string | null;
  refreshToken: string | null;
};

function jsonResponse(status: number, body: unknown, headers?: Record<string, string>): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers(headers),
    json: async () => body,
  } as Response;
}

async function setupClientHarness(initial: Partial<StorageState> = {}) {
  vi.resetModules();

  const storageState: StorageState = {
    accessToken: initial.accessToken ?? "access-old",
    refreshToken: initial.refreshToken ?? "refresh-old",
  };

  const saveAuthTokens = vi.fn((tokens: { accessToken: string; refreshToken: string }) => {
    storageState.accessToken = tokens.accessToken;
    storageState.refreshToken = tokens.refreshToken;
  });
  const clearAuthTokens = vi.fn(() => {
    storageState.accessToken = null;
    storageState.refreshToken = null;
  });

  vi.doMock("../state/authStorage", () => ({
    clearAuthTokens,
    getStoredAccessToken: () => storageState.accessToken,
    getStoredRefreshToken: () => storageState.refreshToken,
    readAuthTokens: () => (
      storageState.accessToken && storageState.refreshToken
        ? { accessToken: storageState.accessToken, refreshToken: storageState.refreshToken }
        : null
    ),
    saveAuthTokens,
    subscribeAuthStorage: () => () => {},
  }));

  const fetchMock = vi.fn<typeof fetch>();
  vi.stubGlobal("fetch", fetchMock);

  const client = await import("./client");
  return {
    client,
    fetchMock,
    storageState,
    saveAuthTokens,
    clearAuthTokens,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("api client auth flows", () => {
  it("login sends tenant_slug when provided", async () => {
    const harness = await setupClientHarness({ accessToken: null, refreshToken: null });
    harness.fetchMock.mockResolvedValueOnce(
      jsonResponse(200, {
        access_token: "access-new",
        refresh_token: "refresh-new",
        token_type: "bearer",
      }),
    );

    const result = await harness.client.login("ops@example.com", "pw", "  default  ");

    expect(result.access_token).toBe("access-new");
    expect(String(harness.fetchMock.mock.calls[0]?.[0])).toBe("http://127.0.0.1:8000/api/v1/auth/login");
    expect(harness.fetchMock.mock.calls[0]?.[1]?.body).toBe(
      JSON.stringify({
        email: "ops@example.com",
        password: "pw",
        tenant_slug: "default",
      }),
    );
  });

  it("retries an authenticated request after refreshing the session", async () => {
    const harness = await setupClientHarness();
    harness.fetchMock
      .mockResolvedValueOnce(jsonResponse(401, { detail: "Token expired" }))
      .mockResolvedValueOnce(
        jsonResponse(200, {
          access_token: "access-new",
          refresh_token: "refresh-new",
          token_type: "bearer",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(200, [
          {
            id: "user-1",
            tenant_id: "tenant-1",
            email: "ops@example.com",
            role: "ADMIN",
            is_active: true,
            created_at: "2026-03-15T10:00:00Z",
          },
        ]),
      );

    const users = await harness.client.listUsers("access-old");

    expect(users).toHaveLength(1);
    expect(harness.saveAuthTokens).toHaveBeenCalledWith({
      accessToken: "access-new",
      refreshToken: "refresh-new",
    });
    expect(harness.fetchMock).toHaveBeenCalledTimes(3);
    expect(harness.fetchMock.mock.calls[1]?.[1]?.body).toBe(
      JSON.stringify({ refresh_token: "refresh-old" }),
    );
    const retryHeaders = new Headers(harness.fetchMock.mock.calls[2]?.[1]?.headers as HeadersInit);
    expect(retryHeaders.get("Authorization")).toBe("Bearer access-new");
  });

  it("surfaces a helpful error when the API is unreachable", async () => {
    const harness = await setupClientHarness({ accessToken: null, refreshToken: null });
    harness.fetchMock.mockRejectedValueOnce(new TypeError("network down"));

    await expect(harness.client.login("ops@example.com", "pw")).rejects.toThrow(
      "Cannot reach API at http://127.0.0.1:8000. Confirm backend is running and CORS allows your frontend origin.",
    );
  });

  it("uses the current LAN host for the default API base URL", async () => {
    vi.stubGlobal("location", { hostname: "192.168.1.108" });
    const harness = await setupClientHarness({ accessToken: null, refreshToken: null });
    harness.fetchMock.mockResolvedValueOnce(
      jsonResponse(200, {
        access_token: "access-new",
        refresh_token: "refresh-new",
        token_type: "bearer",
      }),
    );

    await harness.client.login("ops@example.com", "pw");

    expect(String(harness.fetchMock.mock.calls[0]?.[0])).toBe("http://192.168.1.108:8000/api/v1/auth/login");
  });
});
