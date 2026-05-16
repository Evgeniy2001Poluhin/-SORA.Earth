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
      "/api":          { target: "http://localhost:8000", changeOrigin: true },
      "/admin":        { target: "http://localhost:8000", changeOrigin: true },
      "/health":       { target: "http://localhost:8000", changeOrigin: true },
      "/metrics":      { target: "http://localhost:8000", changeOrigin: true },
      "/docs":         { target: "http://localhost:8000", changeOrigin: true },
      "/openapi.json": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  build: {
    outDir: "../app/static/spa",
    emptyOutDir: true,
    assetsDir: "assets",
    sourcemap: true,
    chunkSizeWarningLimit: 1000,
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (!id.includes("node_modules")) return undefined;
          if (id.includes("/three/") || id.includes("\\three\\")) return "vendor-three";
          if (id.includes("react-router")) return "vendor-router";
          if (id.includes("@tanstack/react-query")) return "vendor-query";
          if (id.includes("framer-motion")) return "vendor-motion";
          if (id.includes("sonner")) return "vendor-toast";
          if (id.includes("/react-dom/") || id.includes("/react/") || id.includes("/scheduler/")) return "vendor-react";
          if (id.includes("recharts") || id.includes("d3-")) return "vendor-charts";
          if (id.includes("leaflet")) return "vendor-leaflet";
          return "vendor-misc";
        },
      },
    },
  },
});
