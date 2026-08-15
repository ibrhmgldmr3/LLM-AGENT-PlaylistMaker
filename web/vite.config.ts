/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    // `EventSource` jsdom'da YOK; testler kendi sahtesini kuruyor. jsdom yine de
    // gerekli: hook bir React agacinda calisiyor ve DOM olmadan render edilemez.
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
  server: {
    port: 5173,
    proxy: {
      // Gelistirmede API'yi ayni kokenden servis et: boylece CORS ve SSE
      // tarayici kisitlarina hic takilmiyoruz.
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
