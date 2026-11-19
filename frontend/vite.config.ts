import path from "node:path";
import { fileURLToPath } from "node:url";
import { configDefaults, defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const workspaceRoot = fileURLToPath(new URL("..", import.meta.url));

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    target: "es2022",
  },
  optimizeDeps: {
    esbuildOptions: {
      // Avoid transpiling class fields down to helper-based output that can break worker bundles.
      target: "es2022",
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
    exclude: [...configDefaults.exclude, "e2e/**"],
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
        statements: 29,
        branches: 31,
        functions: 21,
        lines: 31,
      },
    },
  },
  server: {
    fs: {
      allow: [workspaceRoot, path.resolve(workspaceRoot, "shared")],
    },
  },
});
