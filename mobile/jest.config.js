/** @type {import("jest").Config} */
module.exports = {
  preset: "jest-expo",
  // Transpiled imports from `../shared/*` resolve from repo root; map runtime to this app.
  moduleNameMapper: {
    "^@babel/runtime/(.*)$": "<rootDir>/node_modules/@babel/runtime/$1",
  },
  clearMocks: true,
  resetMocks: true,
  restoreMocks: true,
  roots: ["<rootDir>"],
  testMatch: ["<rootDir>/src/**/__tests__/**/*.test.ts", "<rootDir>/src/**/__tests__/**/*.test.tsx"],
  collectCoverageFrom: [
    "src/**/*.{ts,tsx}",
    "!src/**/__tests__/**",
    "!src/**/*.test.{ts,tsx}",
  ],
  coverageProvider: "v8",
  coverageReporters: ["text", "lcov", "html"],
  coverageDirectory: "coverage",
  coverageThreshold: {
    global: {
      statements: 25,
      branches: 75,
      functions: 63,
      lines: 25,
    },
  },
};
