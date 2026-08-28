import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/bazaar": "http://localhost:8000",
      "/acp": "http://localhost:8000",
      "/.well-known": "http://localhost:8000",
    },
  },
  build: { outDir: "dist", sourcemap: false },
});
