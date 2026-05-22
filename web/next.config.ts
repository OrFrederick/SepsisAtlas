import type { NextConfig } from "next";

const API_URL = process.env.API_URL ?? "http://localhost:8000";

const config: NextConfig = {
  // Mirror the Astro/Vite dev proxy so /query and friends hit FastAPI in `next dev`.
  // In prod, nginx terminates these paths before they ever reach Node — these rules
  // exist so dev and prod use the same code path on the client side.
  async rewrites() {
    return [
      { source: "/query", destination: `${API_URL}/query` },
      { source: "/rank_predictors", destination: `${API_URL}/rank_predictors` },
      { source: "/ingest_pubmed", destination: `${API_URL}/ingest_pubmed` },
      { source: "/health", destination: `${API_URL}/health` },
      { source: "/health/:path*", destination: `${API_URL}/health/:path*` },
      { source: "/phenotypes", destination: `${API_URL}/phenotypes` },
      { source: "/phenotypes/:path*", destination: `${API_URL}/phenotypes/:path*` },
    ];
  },
};

export default config;
