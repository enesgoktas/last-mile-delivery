const DEFAULT_STYLE_URL = "https://tiles.openfreemap.org/styles/liberty";
const FALLBACK_STYLE_URL = "https://tiles.openfreemap.org/styles/liberty";

export function resolveDispatcherMapStyleUrl(): string {
  const configured = import.meta.env.VITE_MAP_STYLE_URL?.trim();
  if (configured && configured.length > 0) {
    if (configured.includes("{apiKey}")) {
      const apiKey = import.meta.env.VITE_STADIA_API_KEY?.trim();
      if (!apiKey) return FALLBACK_STYLE_URL;
      return configured.replaceAll("{apiKey}", encodeURIComponent(apiKey));
    }
    return configured;
  }
  return DEFAULT_STYLE_URL;
}
