import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  root: __dirname,
  plugins: [react()],
  optimizeDeps: { include: ["react","react-dom","react-dom/client","@tanstack/react-query","react-router-dom","framer-motion","sonner","three"] },
  resolve: {
    dedupe: ["react","react-dom","@tanstack/react-query"],
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api":     { target: "http://localhost:8000", changeOrigin: true },
      "/admin":   { target: "http://localhost:8000", changeOrigin: true },
      "/health":  { target: "http://localhost:8000", changeOrigin: true },
      "/metrics": { target: "http://localhost:8000", changeOrigin: true },
      "/docs":    { target: "http://localhost:8000", changeOrigin: true },
      "/openapi.json": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  build: {
    outDir: "../app/static",
    emptyOutDir: true,
    assetsDir: "assets",
    sourcemap: true,
  },
});
