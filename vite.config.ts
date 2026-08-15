import {defineConfig} from "vite";

export default defineConfig({
  // Map archives and glyphs are served straight from the release web root
  // (Caddy /srv/assets in production, public/ symlinks in dev), never bundled.
  build: {
    copyPublicDir: false,
    // MapLibre is ~80% of the bundle and changes only on upgrades; keep it in its own
    // long-lived chunk so app edits don't invalidate it for returning visitors.
    rollupOptions: {output: {manualChunks: {maplibre: ["maplibre-gl"], pmtiles: ["pmtiles"]}}}
  },
  server: {
    watch: {ignored: ["**/docs/**", "**/dist/**", "**/dist-server/**"]},
    proxy: {
      // Point at an already-running API (e.g. the compose stack) with MAP_API_ORIGIN.
      "/api": process.env.MAP_API_ORIGIN ?? "http://127.0.0.1:3000"
    }
  }
});
