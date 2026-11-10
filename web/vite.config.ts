import { defineConfig } from "vite";

export default defineConfig({
  server: {
    host: "127.0.0.1",
    watch: { ignored: ["**/src-tauri/**"] },
    allowedHosts: ["nativspace.tail0761b9.ts.net"],
    // The captioning API (server/app.py) runs separately on 4748.
    proxy: { "/api": "http://127.0.0.1:4748" },
  },
});
