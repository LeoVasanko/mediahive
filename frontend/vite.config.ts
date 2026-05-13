import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { VitePWA } from "vite-plugin-pwa";
import fastapiVue from './vite-plugin-fastapi.js'

// https://vitejs.dev/config/
export default defineConfig(async () => ({
  plugins: [
    fastapiVue(),
    vue(),
    VitePWA({
      registerType: "autoUpdate",
      injectRegister: "script",
      manifest: {
        id: "/",
        name: "MediaHive",
        short_name: "MediaHive",
        description: "Movies and Series",
        start_url: "/",
        scope: "/",
        display_override: ["window-controls-overlay", "fullscreen", "standalone"],
        display: "fullscreen",
        background_color: "#0a0a0a",
        theme_color: "#0a0a0a",
        icons: [
          {
            src: "/mediahive-32.webp",
            sizes: "32x32",
            type: "image/webp"
          },
          {
            src: "/mediahive.webp",
            sizes: "192x192",
            type: "image/webp"
          }
        ]
      },
      workbox: {
        cleanupOutdatedCaches: true,
        clientsClaim: true,
        skipWaiting: true,
        manifestTransforms: [
          async (entries) => {
            const manifest = entries.map((entry) =>
              entry.url === "index.html" ? { ...entry, url: "/" } : entry
            );
            return { manifest, warnings: [] };
          },
        ],
        navigateFallback: null,
        navigateFallbackDenylist: [/^\/api\//],
      },
      devOptions: {
        enabled: false,
      },
    }),
  ],

  // Vite dev server options
  clearScreen: false,
  server: {
    port: 8420,
    strictPort: true,
  },
}));
