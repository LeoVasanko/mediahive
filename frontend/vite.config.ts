import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import fastapiVue from './vite-plugin-fastapi.js'

// https://vitejs.dev/config/
export default defineConfig(async () => ({
  plugins: [
    fastapiVue(),
    vue(),
  ],

  // Vite dev server options
  clearScreen: false,
  server: {
    port: 8420,
    strictPort: true,
  },
}));
