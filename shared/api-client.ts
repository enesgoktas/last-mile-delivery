export class ApiClientError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiClientError";
    this.status = status;
  }
}

export type AuthTokens = {
  accessToken: string;
  refreshToken: string;
};

export type Awaitable<T> = T | Promise<T>;

export type AuthTokenStore = {
  getAccessToken: () => Awaitable<string | null>;
  getRefreshToken: () => Awaitable<string | null>;
  saveAuthTokens: (tokens: AuthTokens) => Awaitable<void>;
  clearAuthTokens: () => Awaitable<void>;
};

export type ApiRequestOptions = {
  method?: string;
  token?: string | null;
  body?: unknown;
  rawBody?: BodyInit;
  headers?: HeadersInit;
  skipContentType?: boolean;
  auth?: boolean;
  retryOnUnauthorized?: boolean;
  signal?: AbortSignal;
  errorFallbackMessage?: string;
};

export type PaginatedResult<T> = { data: T[]; total: number };

type ApiClientOptions = {
  baseUrl: string;
  tokenStore?: AuthTokenStore;
  defaultAuth?: boolean;
  refreshPath?: string;
  networkErrorMessage?: string;
  errorFactory?: (message: string, status: number) => Error;
};

function isRawBody(value: unknown): value is BodyInit {
  if (typeof value === "string") {
    return true;
  }
  if (typeof FormData !== "undefined" && value instanceof FormData) {
    return true;
  }
  if (typeof Blob !== "undefined" && value instanceof Blob) {
    return true;
  }
  if (typeof URLSearchParams !== "undefined" && value instanceof URLSearchParams) {
    return true;
  }
  if (value instanceof ArrayBuffer) {
    return true;
  }
  return ArrayBuffer.isView(value);
}

function normalizeBaseUrl(baseUrl: string): string {
  return baseUrl.replace(/\/$/, "");
}

function createError(
  errorFactory: ((message: string, status: number) => Error) | undefined,
  message: string,
  status: number,
): Error {
  if (errorFactory) {
    return errorFactory(message, status);
  }
  return new ApiClientError(message, status);
}

function buildBody(options: ApiRequestOptions): BodyInit | undefined {
  if (options.rawBody !== undefined) {
    return options.rawBody;
  }
  if (options.body === undefined) {
    return undefined;
  }
  if (isRawBody(options.body)) {
    return options.body;
  }
  return JSON.stringify(options.body);
}

export function createApiClient(options: ApiClientOptions) {
  const baseUrl = normalizeBaseUrl(options.baseUrl);
  const tokenStore = options.tokenStore;
  const defaultAuth = options.defaultAuth ?? false;
  const refreshPath = options.refreshPath ?? "/api/v1/auth/refresh";
  let refreshInFlight: Promise<string> | null = null;

  async function parseError(response: Response, fallback?: string): Promise<Error> {
    let detail = fallback ?? `API request failed (${response.status})`;
    try {
      const payload = await response.json();
      if (payload?.detail) {
        detail = String(payload.detail);
      }
    } catch {
      // Keep fallback detail.
    }
    return createError(options.errorFactory, detail, response.status);
  }

  async function execute(path: string, requestOptions: ApiRequestOptions, accessToken: string | null): Promise<Response> {
    const headers = new Headers(requestOptions.headers ?? {});
    if (!requestOptions.skipContentType && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    if (accessToken) {
      headers.set("Authorization", `Bearer ${accessToken}`);
    }

    try {
      return await fetch(`${baseUrl}${path}`, {
        method: requestOptions.method ?? "GET",
        headers,
        body: buildBody(requestOptions),
        signal: requestOptions.signal,
      });
    } catch {
      throw createError(
        options.errorFactory,
        options.networkErrorMessage ?? `Cannot reach API at ${baseUrl || "current origin"}.`,
        0,
      );
    }
  }

  async function refreshAccessToken(): Promise<string> {
    if (!tokenStore) {
      throw createError(options.errorFactory, "Session expired. Please sign in again.", 401);
    }

    const refreshToken = await tokenStore.getRefreshToken();
    if (!refreshToken) {
      throw createError(options.errorFactory, "Session expired. Please sign in again.", 401);
    }

    const response = await execute(
      refreshPath,
      {
        method: "POST",
        body: { refresh_token: refreshToken },
        auth: false,
        retryOnUnauthorized: false,
      },
      null,
    );

    if (!response.ok) {
      await tokenStore.clearAuthTokens();
      throw await parseError(response, "Session refresh failed");
    }

    const payload = (await response.json()) as {
      access_token: string;
      refresh_token: string;
      token_type: string;
    };
    await tokenStore.saveAuthTokens({
      accessToken: payload.access_token,
      refreshToken: payload.refresh_token,
    });
    return payload.access_token;
  }

  async function refreshAccessTokenSingleFlight(): Promise<string> {
    if (!refreshInFlight) {
      refreshInFlight = (async () => {
        try {
          return await refreshAccessToken();
        } finally {
          refreshInFlight = null;
        }
      })();
    }
    return refreshInFlight;
  }

  async function requestResponse(path: string, requestOptions: ApiRequestOptions = {}): Promise<Response> {
    const auth = requestOptions.auth ?? (requestOptions.token !== undefined ? true : defaultAuth);
    const retryOnUnauthorized = requestOptions.retryOnUnauthorized ?? auth;
    let accessToken = requestOptions.token ?? null;
    if (auth && requestOptions.token === undefined && tokenStore) {
      accessToken = await tokenStore.getAccessToken();
    }

    let response = await execute(path, requestOptions, accessToken);
    if (response.status === 401 && auth && retryOnUnauthorized && tokenStore) {
      accessToken = await refreshAccessTokenSingleFlight();
      response = await execute(path, { ...requestOptions, retryOnUnauthorized: false }, accessToken);
    }
    return response;
  }

  async function request<T>(path: string, requestOptions: ApiRequestOptions = {}): Promise<T> {
    const response = await requestResponse(path, requestOptions);
    if (!response.ok) {
      throw await parseError(response, requestOptions.errorFallbackMessage);
    }
    if (response.status === 204) {
      return undefined as T;
    }
    return response.json() as Promise<T>;
  }

  async function requestPaginated<T>(path: string, requestOptions: ApiRequestOptions = {}): Promise<PaginatedResult<T>> {
    const response = await requestResponse(path, requestOptions);
    if (!response.ok) {
      throw await parseError(response, requestOptions.errorFallbackMessage);
    }
    if (response.status === 204) {
      return { data: [], total: 0 };
    }

    const total = Number.parseInt(response.headers.get("X-Total-Count") ?? "0", 10);
    let data: T[];
    try {
      data = (await response.json()) as T[];
    } catch {
      data = [];
    }
    return { data, total };
  }

  return {
    request,
    requestPaginated,
    requestResponse,
  };
}
