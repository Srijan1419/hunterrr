import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.tsx"],
    include: ["**/*.test.{ts,tsx}"],
    passWithNoTests: true,
    globals: true,
    // The database and auth tests start an in-memory WASM Postgres and import heavy modules; under load
    // (several checks running at once) the 10 s default hook timeout flaked. These are generous ceilings, not delays.
    testTimeout: 30_000,
    hookTimeout: 90_000,
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./"),
    },
  },
});