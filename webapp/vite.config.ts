import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

// Served by the FastAPI backend at /app/ (see backend/api/routes.py), so the API is
// same-origin in production. In dev, /api is proxied to the local backend.
export default defineConfig({
  base: "/app/",
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["icon.svg", "icon-192.png", "icon-512.png", "icon-maskable-512.png", "apple-touch-icon.png"],
      manifest: {
        name: "GTD",
        short_name: "GTD",
        description: "Getting Things Done: your Second Brain actions",
        start_url: "/app/",
        scope: "/app/",
        display: "standalone",
        background_color: "#f8fafc",
        theme_color: "#f8fafc",
        icons: [
          { src: "icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "icon-512.png", sizes: "512x512", type: "image/png" },
          { src: "icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
        ],
      },
      workbox: { navigateFallback: "/app/index.html" },
    }),
  ],
  server: { proxy: { "/api": "http://localhost:8080" } },
});
