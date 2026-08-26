import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: {
      ignored: ["**/src-tauri/target/**"],
    },
    proxy: { "/api": "http://127.0.0.1:47821", "/health": "http://127.0.0.1:47821" },
  },
  envPrefix: ["VITE_", "TAURI_"],
});
