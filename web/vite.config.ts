import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
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
