type AuthState = {
  accessToken: string | null;
  refreshToken: string | null;
};

type ApiHarness = {
  api: typeof import("../api");
  fetchMock: jest.MockedFunction<typeof fetch>;
  authState: AuthState;
  saveAuthTokens: jest.Mock;
  clearAuthTokens: jest.Mock;
};

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

function getHeaderValue(options: RequestInit | undefined, key: string): string | null {
  const headers = options?.headers;
  if (!headers) return null;
  return new Headers(headers as HeadersInit).get(key);
}

function setupApiHarness(initial: Partial<AuthState> = {}): ApiHarness {
  jest.resetModules();

  const authState: AuthState = {
    accessToken: initial.accessToken ?? "access-old",
    refreshToken: initial.refreshToken ?? "refresh-old",
  };

  const saveAuthTokens = jest.fn(
    async ({ accessToken, refreshToken }: { accessToken: string; refreshToken: string }) => {
      authState.accessToken = accessToken;
      authState.refreshToken = refreshToken;
    },
  );
  const clearAuthTokens = jest.fn(async () => {
    authState.accessToken = null;
    authState.refreshToken = null;
  });
  const getAccessToken = jest.fn(async () => authState.accessToken);
  const getRefreshToken = jest.fn(async () => authState.refreshToken);

  jest.doMock("../auth", () => ({
    clearAuthTokens,
    getAccessToken,
    getRefreshToken,
    saveAuthTokens,
  }));

  const fetchMock = jest.fn() as jest.MockedFunction<typeof fetch>;
  global.fetch = fetchMock;

  const api = require("../api") as typeof import("../api");
  return {
    api,
    fetchMock,
    authState,
    saveAuthTokens,
    clearAuthTokens,
  };
}

describe("api service", () => {
  test("login stores returned tokens", async () => {
    const harness = setupApiHarness({
      accessToken: null,
      refreshToken: null,
    });

    harness.fetchMock.mockResolvedValueOnce(
      jsonResponse(200, {
        access_token: "access-new",
        refresh_token: "refresh-new",
        token_type: "bearer",
      }),
    );

    await harness.api.login("courier@example.com", "pw");

    expect(harness.fetchMock).toHaveBeenCalledTimes(1);
    expect(String(harness.fetchMock.mock.calls[0][0])).toBe(
      "http://localhost:8000/api/v1/auth/login",
    );
    expect(harness.fetchMock.mock.calls[0][1]?.method).toBe("POST");
    expect(harness.fetchMock.mock.calls[0][1]?.body).toBe(
      JSON.stringify({ email: "courier@example.com", password: "pw" }),
    );
    expect(harness.saveAuthTokens).toHaveBeenCalledWith({
      accessToken: "access-new",
      refreshToken: "refresh-new",
    });
  });

  test("login sends tenant_slug when tenant slug is provided", async () => {
    const harness = setupApiHarness({
      accessToken: null,
      refreshToken: null,
    });

    harness.fetchMock.mockResolvedValueOnce(
      jsonResponse(200, {
        access_token: "access-new",
        refresh_token: "refresh-new",
        token_type: "bearer",
      }),
    );

    await harness.api.login("courier@example.com", "pw", "  acme  ");

    expect(JSON.parse(String(harness.fetchMock.mock.calls[0][1]?.body))).toEqual({
      email: "courier@example.com",
      password: "pw",
      tenant_slug: "acme",
    });
  });

  test("request retries after 401 using refreshed token", async () => {
    const harness = setupApiHarness();

    harness.fetchMock
      .mockResolvedValueOnce(jsonResponse(401, { detail: "Token expired" }))
      .mockResolvedValueOnce(
        jsonResponse(200, {
          access_token: "access-new",
          refresh_token: "refresh-new",
          token_type: "bearer",
        }),
      )
      .mockResolvedValueOnce(jsonResponse(200, { id: "courier-1", full_name: "Courier" }));

    const profile = await harness.api.getMyCourierProfile();

    expect(profile).toMatchObject({ id: "courier-1" });
    expect(harness.fetchMock).toHaveBeenCalledTimes(3);

    expect(getHeaderValue(harness.fetchMock.mock.calls[0][1], "Authorization")).toBe(
      "Bearer access-old",
    );
    expect(String(harness.fetchMock.mock.calls[1][0])).toBe(
      "http://localhost:8000/api/v1/auth/refresh",
    );
    expect(harness.fetchMock.mock.calls[1][1]?.body).toBe(
      JSON.stringify({ refresh_token: "refresh-old" }),
    );
    expect(getHeaderValue(harness.fetchMock.mock.calls[2][1], "Authorization")).toBe(
      "Bearer access-new",
    );
  });

  test("refresh failure clears local tokens and surfaces ApiError", async () => {
    const harness = setupApiHarness();

    harness.fetchMock
      .mockResolvedValueOnce(jsonResponse(401, { detail: "Token expired" }))
      .mockResolvedValueOnce(jsonResponse(401, { detail: "Invalid refresh token" }));

    await expect(harness.api.getMyCourierProfile()).rejects.toMatchObject({
      name: "ApiError",
      status: 401,
      message: "Invalid refresh token",
    });

    expect(harness.clearAuthTokens).toHaveBeenCalledTimes(1);
    expect(harness.fetchMock).toHaveBeenCalledTimes(2);
  });

  test("concurrent 401 responses trigger only one refresh request", async () => {
    const harness = setupApiHarness();
    let refreshCalls = 0;

    harness.fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const authorization = getHeaderValue(init, "Authorization");

      if (url.endsWith("/api/v1/auth/refresh")) {
        refreshCalls += 1;
        return jsonResponse(200, {
          access_token: "access-new",
          refresh_token: "refresh-new",
          token_type: "bearer",
        });
      }

      if (url.endsWith("/api/v1/couriers/me")) {
        if (authorization === "Bearer access-old") {
          return jsonResponse(401, { detail: "Token expired" });
        }
        return jsonResponse(200, { id: "courier-1", full_name: "Courier" });
      }

      if (url.endsWith("/api/v1/couriers/courier-1/next-stop")) {
        if (authorization === "Bearer access-old") {
          return jsonResponse(401, { detail: "Token expired" });
        }
        return jsonResponse(200, { id: "stop-1", sequence_number: 1 });
      }

      throw new Error(`Unexpected URL in test: ${url}`);
    });

    const [profile, nextStop] = await Promise.all([
      harness.api.getMyCourierProfile(),
      harness.api.getNextStop("courier-1"),
    ]);

    expect(profile).toMatchObject({ id: "courier-1" });
    expect(nextStop).toMatchObject({ id: "stop-1", sequence_number: 1 });
    expect(refreshCalls).toBe(1);
  });

  test("stop action endpoints send stable payload contracts", async () => {
    const harness = setupApiHarness();

    harness.fetchMock.mockResolvedValue(jsonResponse(200, { status: "ok" }));

    await harness.api.completeStop("route-1", "stop-1", {
      notes: "left with concierge",
      photo_urls: ["https://cdn.example.com/photo.jpg"],
      signature_url: "https://cdn.example.com/signature.jpg",
      receiver_name: "Alex",
      otp_code: "123456",
    });
    await harness.api.failStop("route-1", "stop-2", "OTHER", "No answer", "Gate locked");

    expect(String(harness.fetchMock.mock.calls[0][0])).toBe(
      "http://localhost:8000/api/v1/routes/route-1/stops/stop-1/complete",
    );
    expect(harness.fetchMock.mock.calls[0][1]?.body).toBe(
      JSON.stringify({
        notes: "left with concierge",
        photo_urls: ["https://cdn.example.com/photo.jpg"],
        signature_url: "https://cdn.example.com/signature.jpg",
        receiver_name: "Alex",
        otp_code: "123456",
      }),
    );

    expect(String(harness.fetchMock.mock.calls[1][0])).toBe(
      "http://localhost:8000/api/v1/routes/route-1/stops/stop-2/fail",
    );
    expect(harness.fetchMock.mock.calls[1][1]?.body).toBe(
      JSON.stringify({
        failure_reason: "OTHER",
        notes: "No answer",
        custom_reason_label: "Gate locked",
      }),
    );
  });

  test("startRoute posts to route start URL without JSON body", async () => {
    const harness = setupApiHarness();
    harness.fetchMock.mockResolvedValueOnce(jsonResponse(200, { id: "route-1", status: "ACTIVE" }));

    const route = await harness.api.startRoute("abc-route");

    expect(route).toMatchObject({ id: "route-1", status: "ACTIVE" });
    expect(String(harness.fetchMock.mock.calls[0][0])).toBe(
      "http://localhost:8000/api/v1/routes/abc-route/start",
    );
    expect(harness.fetchMock.mock.calls[0][1]?.method).toBe("POST");
    expect(harness.fetchMock.mock.calls[0][1]?.body).toBeUndefined();
  });

  test("arriveStop posts empty JSON object", async () => {
    const harness = setupApiHarness();
    harness.fetchMock.mockResolvedValueOnce(jsonResponse(200, { id: "stop-9", sequence_number: 2 }));

    const stop = await harness.api.arriveStop("route-1", "stop-9");

    expect(stop).toMatchObject({ id: "stop-9", sequence_number: 2 });
    expect(String(harness.fetchMock.mock.calls[0][0])).toBe(
      "http://localhost:8000/api/v1/routes/route-1/stops/stop-9/arrive",
    );
    expect(harness.fetchMock.mock.calls[0][1]?.method).toBe("POST");
    expect(harness.fetchMock.mock.calls[0][1]?.body).toBe(JSON.stringify({}));
  });

  test("getOrderPackages GETs packages for order id", async () => {
    const harness = setupApiHarness();
    const packages = [
      {
        id: "pkg-1",
        order_id: "ord-1",
        label: "Box A",
        weight_kg: 1.5,
        length_cm: null,
        width_cm: null,
        height_cm: null,
        volume_cm3: 1000,
        status: "CREATED",
      },
    ];
    harness.fetchMock.mockResolvedValueOnce(jsonResponse(200, packages));

    const result = await harness.api.getOrderPackages("ord-1");

    expect(result).toEqual(packages);
    expect(String(harness.fetchMock.mock.calls[0][0])).toBe(
      "http://localhost:8000/api/v1/orders/ord-1/packages",
    );
    expect(harness.fetchMock.mock.calls[0][1]?.method ?? "GET").toBe("GET");
  });

  test("getActiveRoute GETs courier active route", async () => {
    const harness = setupApiHarness();
    harness.fetchMock.mockResolvedValueOnce(jsonResponse(200, { id: "route-77", status: "ACTIVE" }));

    const route = await harness.api.getActiveRoute("courier-1");

    expect(route).toMatchObject({ id: "route-77" });
    expect(String(harness.fetchMock.mock.calls[0][0])).toBe(
      "http://localhost:8000/api/v1/couriers/courier-1/active-route",
    );
  });

  test("getNextStop GETs courier next stop", async () => {
    const harness = setupApiHarness();
    harness.fetchMock.mockResolvedValueOnce(jsonResponse(200, { id: "stop-next", sequence_number: 1 }));

    const stop = await harness.api.getNextStop("courier-2");

    expect(stop).toMatchObject({ id: "stop-next" });
    expect(String(harness.fetchMock.mock.calls[0][0])).toBe(
      "http://localhost:8000/api/v1/couriers/courier-2/next-stop",
    );
  });

  test("getCourierCurrentLoad GETs courier load summary", async () => {
    const harness = setupApiHarness();
    const load = {
      courier_id: "c-1",
      total_weight_kg: 10,
      total_volume_cm3: 5000,
      package_count: 2,
      packages: [],
    };
    harness.fetchMock.mockResolvedValueOnce(jsonResponse(200, load));

    const result = await harness.api.getCourierCurrentLoad("c-1");

    expect(result).toEqual(load);
    expect(String(harness.fetchMock.mock.calls[0][0])).toBe(
      "http://localhost:8000/api/v1/couriers/c-1/current-load",
    );
  });

  test("logout posts refresh token and clears local auth when access token exists", async () => {
    const harness = setupApiHarness();
    harness.fetchMock.mockResolvedValueOnce(jsonResponse(200, { status: "ok" }));

    await harness.api.logout();

    expect(String(harness.fetchMock.mock.calls[0][0])).toBe("http://localhost:8000/api/v1/auth/logout");
    expect(harness.fetchMock.mock.calls[0][1]?.method).toBe("POST");
    expect(harness.fetchMock.mock.calls[0][1]?.body).toBe(
      JSON.stringify({ refresh_token: "refresh-old" }),
    );
    expect(harness.clearAuthTokens).toHaveBeenCalledTimes(1);
  });

  test("logout skips remote call but still clears local auth when no access token", async () => {
    // `null` is overridden by `?? "access-old"` in the harness; empty string is a logged-out session.
    const harness = setupApiHarness({
      accessToken: "",
      refreshToken: "orphan-refresh",
    });

    await harness.api.logout();

    expect(harness.fetchMock).not.toHaveBeenCalled();
    expect(harness.clearAuthTokens).toHaveBeenCalledTimes(1);
  });

  test("updateCourierStatus PUTs status payload", async () => {
    const harness = setupApiHarness();
    harness.fetchMock.mockResolvedValueOnce(jsonResponse(200, { id: "c-1", full_name: "Courier" }));

    const profile = await harness.api.updateCourierStatus("c-1", "ON_BREAK");

    expect(profile).toMatchObject({ id: "c-1" });
    expect(String(harness.fetchMock.mock.calls[0][0])).toBe(
      "http://localhost:8000/api/v1/couriers/c-1/status",
    );
    expect(harness.fetchMock.mock.calls[0][1]?.method).toBe("PUT");
    expect(harness.fetchMock.mock.calls[0][1]?.body).toBe(JSON.stringify({ status: "ON_BREAK" }));
  });

  test("updateCourierLocation PUTs geolocation payload", async () => {
    const harness = setupApiHarness();
    harness.fetchMock.mockResolvedValueOnce(
      jsonResponse(200, { status: "ok", geofence_status: "inside" }),
    );

    const result = await harness.api.updateCourierLocation("c-2", {
      lat: 41.01,
      lon: 29.02,
      heading: 90,
      speed_kmh: 12.5,
      accuracy_m: 8,
    });

    expect(result).toMatchObject({ status: "ok", geofence_status: "inside" });
    expect(String(harness.fetchMock.mock.calls[0][0])).toBe(
      "http://localhost:8000/api/v1/couriers/c-2/location",
    );
    expect(harness.fetchMock.mock.calls[0][1]?.method).toBe("PUT");
    expect(harness.fetchMock.mock.calls[0][1]?.body).toBe(
      JSON.stringify({
        lat: 41.01,
        lon: 29.02,
        heading: 90,
        speed_kmh: 12.5,
        accuracy_m: 8,
      }),
    );
  });

  test("updateCourierPushToken PUTs expo token payload", async () => {
    const harness = setupApiHarness();
    harness.fetchMock.mockResolvedValueOnce(jsonResponse(200, { id: "c-3", full_name: "Courier" }));

    await harness.api.updateCourierPushToken("c-3", {
      push_provider: "EXPO",
      push_token: "ExponentPushToken[abc]",
    });

    expect(String(harness.fetchMock.mock.calls[0][0])).toBe(
      "http://localhost:8000/api/v1/couriers/c-3/push-token",
    );
    expect(harness.fetchMock.mock.calls[0][1]?.method).toBe("PUT");
    expect(harness.fetchMock.mock.calls[0][1]?.body).toBe(
      JSON.stringify({
        push_provider: "EXPO",
        push_token: "ExponentPushToken[abc]",
      }),
    );
  });

  test("updateCourierPushToken with null clears tokens on server", async () => {
    const harness = setupApiHarness();
    harness.fetchMock.mockResolvedValueOnce(jsonResponse(200, { id: "c-4", full_name: "Courier" }));

    await harness.api.updateCourierPushToken("c-4", null);

    expect(String(harness.fetchMock.mock.calls[0][0])).toBe(
      "http://localhost:8000/api/v1/couriers/c-4/push-token",
    );
    expect(harness.fetchMock.mock.calls[0][1]?.body).toBe(
      JSON.stringify({ push_provider: null, push_token: null }),
    );
  });

  test("requestPodUploadUrl POSTs file_type and content_type", async () => {
    const harness = setupApiHarness();
    const podResponse = {
      upload_url: "https://upload.example/presign",
      file_url: "https://cdn.example/pod/sig.png",
      object_key: "routes/r1/stops/s1/sig",
      expires_at: "2026-01-01T00:00:00.000Z",
    };
    harness.fetchMock.mockResolvedValueOnce(jsonResponse(200, podResponse));

    const result = await harness.api.requestPodUploadUrl("r1", "s1", "signature", "image/png");

    expect(result).toEqual(podResponse);
    expect(String(harness.fetchMock.mock.calls[0][0])).toBe(
      "http://localhost:8000/api/v1/routes/r1/stops/s1/pod/upload-url",
    );
    expect(harness.fetchMock.mock.calls[0][1]?.method).toBe("POST");
    expect(harness.fetchMock.mock.calls[0][1]?.body).toBe(
      JSON.stringify({ file_type: "signature", content_type: "image/png" }),
    );
  });

  test("network failures surface a structured ApiError", async () => {
    const harness = setupApiHarness();
    harness.fetchMock.mockRejectedValueOnce(new TypeError("network down"));

    await expect(harness.api.getMyCourierProfile()).rejects.toMatchObject({
      name: "ApiError",
      status: 0,
      message: "Cannot reach API at http://localhost:8000. Confirm the courier backend is reachable from this device.",
    });
  });
});
