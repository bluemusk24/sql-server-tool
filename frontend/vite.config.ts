/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Vite + Vitest configuration for the frontend (#22). The API base URL
// comes from VITE_API_BASE_URL (see .env.example); tests run in jsdom.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/health": { target: "http://localhost:8000", changeOrigin: true },
      "/auth": { target: "http://localhost:8000", changeOrigin: true },
      "/investigations": { target: "http://localhost:8000", changeOrigin: true },
      "/jobs": { target: "http://localhost:8000", changeOrigin: true },
      "/reports": { target: "http://localhost:8000", changeOrigin: true },
      "/fleet": { target: "http://localhost:8000", changeOrigin: true },
      "/orgs": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    css: false,
  },
});
