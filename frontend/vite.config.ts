/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import { TanStackRouterVite } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Use 127.0.0.1, not "localhost": on Windows "localhost" resolves to ::1 first and stalls.
const backendUrl = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [
    TanStackRouterVite({ target: "react", autoCodeSplitting: true }),
    react(),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      "@": new URL("./src", import.meta.url).pathname,
    },
  },
  server: {
    port: 5173,
    // Local dev binds loopback only; the Docker image overrides it with `--host 0.0.0.0`.
    host: "127.0.0.1",
    proxy: {
      "/health": backendUrl,
      "/api": backendUrl,
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});
