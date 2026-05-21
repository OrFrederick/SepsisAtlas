import { defineConfig } from "astro/config";
import react from "@astrojs/react";
import tailwindcss from "@tailwindcss/vite";

const API_TARGET = process.env.API_URL ?? "http://localhost:8000";
const forward = { target: API_TARGET, changeOrigin: true };

export default defineConfig({
  output: "static",
  integrations: [react()],
  devToolbar: { enabled: false },
  vite: {
    plugins: [tailwindcss()],
    server: {
      proxy: {
        "/query": forward,
        "/rank_predictors": forward,
        "/viewer": forward,
        "/ingest_pubmed": forward,
        "/health": forward,
        "/phenotypes": forward,
        "^/papers/[^/]+/pdf$": forward,
      },
    },
  },
});
