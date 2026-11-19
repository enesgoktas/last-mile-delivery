import { createContext, useContext, useEffect, useMemo, useState } from "react";
import type { UserRole } from "../types";
import { logout as logoutSession } from "../api/client";
import {
  clearAuthTokens,
  readAuthTokens,
  saveAuthTokens,
  subscribeAuthStorage,
  getStoredRefreshToken,
} from "./authStorage";

type AuthState = {
  token: string | null;
  role: UserRole | null;
  tenantId: string | null;
};

type AuthContextValue = AuthState & {
  loginWithSession: (accessToken: string, refreshToken: string) => void;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

type TokenClaims = { role: UserRole | null; tenantId: string | null; expired: boolean };

function decodeTokenClaims(token: string): TokenClaims {
  try {
    const payloadB64 = token.split(".")[1];
    if (!payloadB64) return { role: null, tenantId: null, expired: true };
    const normalized = payloadB64.replace(/-/g, "+").replace(/_/g, "/");
    const padding = normalized.length % 4;
    const json = atob(padding ? normalized.padEnd(normalized.length + (4 - padding), "=") : normalized);
    const payload = JSON.parse(json) as { role?: UserRole; tenant_id?: string; exp?: number };
    const expired =
      typeof payload.exp === "number" ? Date.now() / 1000 >= payload.exp - 30 : false;
    return {
      role: payload.role ?? null,
      tenantId: payload.tenant_id ?? null,
      expired,
    };
  } catch {
    return { role: null, tenantId: null, expired: true };
  }
}

function readAuthState(): AuthState {
  const storedTokens = readAuthTokens();
  if (!storedTokens) {
    return { token: null, role: null, tenantId: null };
  }
  const claims = decodeTokenClaims(storedTokens.accessToken);
  // Treat expired tokens as unauthenticated. The refresh-on-401 mechanism in
  // api/client.ts handles silent renewal during active sessions; this guard
  // prevents presenting stale tokens on a fresh page load.
  if (claims.expired) {
    clearAuthTokens();
    return { token: null, role: null, tenantId: null };
  }
  return {
    token: storedTokens.accessToken,
    role: claims.role,
    tenantId: claims.tenantId,
  };
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [authState, setAuthState] = useState<AuthState>(() => readAuthState());

  useEffect(() => subscribeAuthStorage(() => setAuthState(readAuthState())), []);

  const value = useMemo<AuthContextValue>(
    () => ({
      ...authState,
      loginWithSession: (accessToken: string, refreshToken: string) => {
        saveAuthTokens({ accessToken, refreshToken });
        setAuthState(readAuthState());
      },
      logout: async () => {
        try {
          if (authState.token) {
            await logoutSession(authState.token, getStoredRefreshToken());
          }
        } finally {
          clearAuthTokens();
          setAuthState(readAuthState());
        }
      },
    }),
    [authState],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return ctx;
}
