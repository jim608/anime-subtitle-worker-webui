import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

const devBackend = process.env.VITE_DEV_BACKEND?.trim();

export default defineConfig({
  base: "/static/",
  plugins: [vue()],
  server: devBackend ? {
    proxy: {
      "/api": { target: devBackend, changeOrigin: true },
      "/stream": { target: devBackend, changeOrigin: true },
    },
  } : undefined,
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
