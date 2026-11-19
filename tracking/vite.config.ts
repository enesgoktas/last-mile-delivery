import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

const workspaceRoot = fileURLToPath(new URL("..", import.meta.url));

export default defineConfig({
  plugins: [react()],
  build: {
    // MapLibre is intentionally lazy-loaded and large; isolate it from core app code.
    rollupOptions: {
      output: {
        manualChunks: {
          maplibre: ["maplibre-gl"],
        },
      },
    },
    chunkSizeWarningLimit: 1200,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov", "html"],
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/test/**",
        "src/**/*.test.{ts,tsx}",
        "src/**/__tests__/**",
        "src/main.tsx",
      ],
      thresholds: {
        statements: 89,
        branches: 81,
        functions: 95,
        lines: 89,
      },
    },
  },
  server: {
    port: 5175,
    fs: {
      allow: [workspaceRoot, path.resolve(workspaceRoot, "shared")],
    },
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
