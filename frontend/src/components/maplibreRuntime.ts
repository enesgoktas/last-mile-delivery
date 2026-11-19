import type { Map as MapLibreMap } from "maplibre-gl";

export type MapLibreRuntime = {
  Map: new (...args: any[]) => MapLibreMap;
  NavigationControl: new (...args: any[]) => any;
};

type RuntimeLoaderDeps = {
  importModule: () => Promise<unknown>;
  importScriptUrl: () => Promise<string>;
  getGlobalRuntime: () => MapLibreRuntime | null;
  loadScript: (src: string) => Promise<void>;
};

const RUNTIME_SCRIPT_DATA_ATTR = "maplibreRuntime";
const RUNTIME_SCRIPT_DATA_VALUE = "dispatcher-maplibre";

let runtimeScriptLoadPromise: Promise<void> | null = null;

function isRuntimeCandidate(candidate: unknown): candidate is MapLibreRuntime {
  if (!candidate || typeof candidate !== "object") {
    return false;
  }
  const value = candidate as Record<string, unknown>;
  return typeof value.Map === "function" && typeof value.NavigationControl === "function";
}

function toErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  if (typeof error === "string" && error.trim().length > 0) {
    return error;
  }
  return "unknown error";
}

function resolveRuntimeFromGlobal(): MapLibreRuntime | null {
  if (typeof window === "undefined") {
    return null;
  }
  const globalRuntime = (window as Window & { maplibregl?: unknown }).maplibregl;
  return isRuntimeCandidate(globalRuntime) ? globalRuntime : null;
}

async function loadRuntimeScriptOnce(src: string): Promise<void> {
  if (typeof document === "undefined") {
    throw new Error("Document is unavailable for maplibre runtime script loading");
  }

  if (runtimeScriptLoadPromise) {
    return runtimeScriptLoadPromise;
  }

  runtimeScriptLoadPromise = new Promise<void>((resolve, reject) => {
    const existing = document.querySelector(
      `script[data-${RUNTIME_SCRIPT_DATA_ATTR}="${RUNTIME_SCRIPT_DATA_VALUE}"]`,
    ) as HTMLScriptElement | null;

    const handleLoad = () => resolve();
    const handleError = (script: HTMLScriptElement) => {
      script.dataset.failed = "true";
      reject(new Error("MapLibre runtime script failed to load"));
    };

    if (existing) {
      if (existing.dataset.failed === "true" || existing.src !== new URL(src, window.location.href).toString()) {
        existing.remove();
      } else if (existing.dataset.loaded === "true") {
        resolve();
        return;
      } else {
        existing.addEventListener("load", handleLoad, { once: true });
        existing.addEventListener("error", () => handleError(existing), { once: true });
        return;
      }
    }

    const script = document.createElement("script");
    script.src = src;
    script.async = true;
    script.dataset[RUNTIME_SCRIPT_DATA_ATTR] = RUNTIME_SCRIPT_DATA_VALUE;
    script.addEventListener(
      "load",
      () => {
        script.dataset.loaded = "true";
        script.dataset.failed = "false";
        handleLoad();
      },
      { once: true },
    );
    script.addEventListener("error", () => handleError(script), { once: true });
    (document.head ?? document.body ?? document.documentElement).append(script);
  }).catch((error) => {
    runtimeScriptLoadPromise = null;
    throw error;
  });

  return runtimeScriptLoadPromise;
}

export function resolveRuntimeFromModule(moduleValue: unknown): MapLibreRuntime | null {
  const candidates = [
    moduleValue,
    (moduleValue as any)?.default,
    (moduleValue as any)?.["module.exports"],
    (moduleValue as any)?.default?.default,
    (moduleValue as any)?.default?.["module.exports"],
  ];

  for (const candidate of candidates) {
    if (isRuntimeCandidate(candidate)) {
      return candidate;
    }
  }

  return null;
}

export async function loadMapLibreRuntime(deps?: Partial<RuntimeLoaderDeps>): Promise<MapLibreRuntime> {
  const resolvedDeps: RuntimeLoaderDeps = {
    importModule: deps?.importModule ?? (() => import("maplibre-gl")),
    importScriptUrl:
      deps?.importScriptUrl ??
      (async () => {
        const mod = await import("maplibre-gl/dist/maplibre-gl.js?url");
        return mod.default;
      }),
    getGlobalRuntime: deps?.getGlobalRuntime ?? resolveRuntimeFromGlobal,
    loadScript: deps?.loadScript ?? loadRuntimeScriptOnce,
  };

  const fromGlobal = resolvedDeps.getGlobalRuntime();
  if (fromGlobal) {
    return fromGlobal;
  }

  let moduleError: unknown = null;
  try {
    const moduleValue = await resolvedDeps.importModule();
    const runtime = resolveRuntimeFromModule(moduleValue);
    if (runtime) {
      return runtime;
    }
    moduleError = new Error("maplibre-gl module shape was unexpected");
  } catch (error) {
    moduleError = error;
  }

  try {
    const runtimeScriptUrl = await resolvedDeps.importScriptUrl();
    await resolvedDeps.loadScript(runtimeScriptUrl);
    const runtime = resolvedDeps.getGlobalRuntime();
    if (runtime) {
      return runtime;
    }
    throw new Error("MapLibre runtime script loaded but window.maplibregl is unavailable");
  } catch (scriptError) {
    throw new Error(
      `maplibre-gl failed to initialize (module: ${toErrorMessage(moduleError)}; script: ${toErrorMessage(scriptError)})`,
    );
  }
}

export function resetMapLibreRuntimeForTests() {
  runtimeScriptLoadPromise = null;
}
