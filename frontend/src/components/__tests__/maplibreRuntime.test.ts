import { describe, expect, it } from "vitest";
import { loadMapLibreRuntime, resolveRuntimeFromModule } from "../maplibreRuntime";

const fakeRuntime = {
  Map: function FakeMap(this: unknown) {
    return this;
  } as any,
  NavigationControl: function FakeNavigationControl(this: unknown) {
    return this;
  } as any,
};

describe("resolveRuntimeFromModule", () => {
  it("returns runtime from direct module exports", () => {
    const runtime = resolveRuntimeFromModule(fakeRuntime);
    expect(runtime).toBe(fakeRuntime);
  });

  it("returns runtime from nested default exports", () => {
    const runtime = resolveRuntimeFromModule({ default: { default: fakeRuntime } });
    expect(runtime).toBe(fakeRuntime);
  });

  it("returns null for invalid module shapes", () => {
    expect(resolveRuntimeFromModule({ default: {} })).toBeNull();
  });
});

describe("loadMapLibreRuntime", () => {
  it("uses module runtime when available", async () => {
    const runtime = await loadMapLibreRuntime({
      importModule: async () => ({ default: fakeRuntime }),
      importScriptUrl: async () => "/assets/maplibre.js",
      getGlobalRuntime: () => null,
      loadScript: async () => {
        throw new Error("script fallback should not be called");
      },
    });

    expect(runtime).toBe(fakeRuntime);
  });

  it("falls back to script runtime when module import fails", async () => {
    let globalRuntime: typeof fakeRuntime | null = null;
    let loadedScriptUrl: string | null = null;

    const runtime = await loadMapLibreRuntime({
      importModule: async () => {
        throw new Error("__publicField is not defined");
      },
      importScriptUrl: async () => "/assets/maplibre.js",
      getGlobalRuntime: () => globalRuntime,
      loadScript: async (src) => {
        loadedScriptUrl = src;
        globalRuntime = fakeRuntime;
      },
    });

    expect(runtime).toBe(fakeRuntime);
    expect(loadedScriptUrl).toBe("/assets/maplibre.js");
  });

  it("reports module and script errors when both fail", async () => {
    await expect(
      loadMapLibreRuntime({
        importModule: async () => {
          throw new Error("__publicField is not defined");
        },
        importScriptUrl: async () => "/assets/maplibre.js",
        getGlobalRuntime: () => null,
        loadScript: async () => {
          throw new Error("Script blocked by CSP");
        },
      }),
    ).rejects.toThrow(/module: __publicField is not defined; script: Script blocked by CSP/);
  });
});
