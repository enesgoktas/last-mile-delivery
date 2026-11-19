import * as SecureStore from "expo-secure-store";

const ACCESS_TOKEN_KEY = "lmd_mobile_access_token";
const REFRESH_TOKEN_KEY = "lmd_mobile_refresh_token";

export type StoredAuthTokens = {
  accessToken: string;
  refreshToken: string;
};

export async function saveAccessToken(token: string) {
  await SecureStore.setItemAsync(ACCESS_TOKEN_KEY, token);
}

export async function saveRefreshToken(token: string) {
  await SecureStore.setItemAsync(REFRESH_TOKEN_KEY, token);
}

export async function saveAuthTokens(tokens: StoredAuthTokens) {
  await SecureStore.setItemAsync(ACCESS_TOKEN_KEY, tokens.accessToken);
  await SecureStore.setItemAsync(REFRESH_TOKEN_KEY, tokens.refreshToken);
}

export async function getAccessToken() {
  return SecureStore.getItemAsync(ACCESS_TOKEN_KEY);
}

export async function getRefreshToken() {
  return SecureStore.getItemAsync(REFRESH_TOKEN_KEY);
}

export async function getStoredAuthTokens(): Promise<StoredAuthTokens | null> {
  const accessToken = await SecureStore.getItemAsync(ACCESS_TOKEN_KEY);
  const refreshToken = await SecureStore.getItemAsync(REFRESH_TOKEN_KEY);
  if (!accessToken || !refreshToken) {
    return null;
  }
  return { accessToken, refreshToken };
}

export async function clearAccessToken() {
  await SecureStore.deleteItemAsync(ACCESS_TOKEN_KEY);
}

export async function clearAuthTokens() {
  await SecureStore.deleteItemAsync(ACCESS_TOKEN_KEY);
  await SecureStore.deleteItemAsync(REFRESH_TOKEN_KEY);
}
