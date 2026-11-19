export type StoredAuthTokens = {
  accessToken: string;
  refreshToken: string;
};

const ACCESS_TOKEN_KEY = "lmd_access_token";
const REFRESH_TOKEN_KEY = "lmd_refresh_token";
const AUTH_STORAGE_EVENT = "lmd-auth-storage-changed";

function emitAuthStorageChanged() {
  window.dispatchEvent(new Event(AUTH_STORAGE_EVENT));
}

export function readAuthTokens(): StoredAuthTokens | null {
  const accessToken = localStorage.getItem(ACCESS_TOKEN_KEY);
  const refreshToken = localStorage.getItem(REFRESH_TOKEN_KEY);
  if (!accessToken || !refreshToken) {
    return null;
  }
  return { accessToken, refreshToken };
}

export function getStoredAccessToken(): string | null {
  return localStorage.getItem(ACCESS_TOKEN_KEY);
}

export function getStoredRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_TOKEN_KEY);
}

export function saveAuthTokens(tokens: StoredAuthTokens): void {
  localStorage.setItem(ACCESS_TOKEN_KEY, tokens.accessToken);
  localStorage.setItem(REFRESH_TOKEN_KEY, tokens.refreshToken);
  emitAuthStorageChanged();
}

export function clearAuthTokens(): void {
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
  emitAuthStorageChanged();
}

export function subscribeAuthStorage(listener: () => void): () => void {
  const onStorage = (event: Event) => {
    if (!(event instanceof StorageEvent) || event.key === ACCESS_TOKEN_KEY || event.key === REFRESH_TOKEN_KEY || event.key === null) {
      listener();
    }
  };
  window.addEventListener(AUTH_STORAGE_EVENT, listener);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(AUTH_STORAGE_EVENT, listener);
    window.removeEventListener("storage", onStorage);
  };
}
