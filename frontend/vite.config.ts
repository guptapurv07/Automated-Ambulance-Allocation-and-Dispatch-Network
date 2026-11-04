/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import tailwind from "@tailwindcss/vite";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

// The FastAPI backend serves its routes at the root (/ambulances, /health, …). The dashboard calls
// /api/…, and the dev server forwards those calls to the backend with the /api prefix removed.
export default defineConfig({
  plugins: [react(), tailwind()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8000", rewrite: (p) => p.replace(/^\/api/, "") } },
  },
  preview: {
    proxy: { "/api": { target: "http://127.0.0.1:8000", rewrite: (p) => p.replace(/^\/api/, "") } },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 500 },
  test: { include: ["src/**/*.test.ts"] },
});
