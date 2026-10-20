import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the API runs on :8000 and Vite proxies /api to it.
// In production the API serves the built files itself (see Dockerfile).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: process.env.TAHGUL_API_URL ?? "http://localhost:8000", changeOrigin: true },
    },
  },
  build: { outDir: "dist", sourcemap: true },
});
