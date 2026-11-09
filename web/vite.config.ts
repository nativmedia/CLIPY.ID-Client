import { defineConfig } from "vite";
import { resolve } from "node:path";

// The desktop activation page is served by FastAPI rather than Vite. Give its
// backdrop a stable entry URL while keeping the main app's normal hashed assets.
export default defineConfig({
  server: {
    host: "127.0.0.1",
    // The captioning API (server/app.py) runs separately on 4748.
    proxy: { "/api": "http://127.0.0.1:4748" },
  },
  build: {
    rollupOptions: {
      input: {
        index: resolve(__dirname, "index.html"),
        "desktop-backdrop": resolve(__dirname, "src/desktop-backdrop.ts"),
      },
      output: {
        entryFileNames: (chunk) =>
          chunk.name === "desktop-backdrop" ? "desktop-backdrop.js" : "assets/[name]-[hash].js",
      },
    },
  },
});
