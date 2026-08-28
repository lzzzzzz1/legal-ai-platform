import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.indexOf("node_modules/@tiptap") >= 0 || id.indexOf("node_modules/prosemirror") >= 0) {
            return "editor-vendor";
          }
          if (id.indexOf("node_modules/react") >= 0 || id.indexOf("node_modules/scheduler") >= 0) {
            return "react-vendor";
          }
          return undefined;
        }
      }
    }
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/health": "http://localhost:8000"
    }
  }
});
