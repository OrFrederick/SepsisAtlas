import type { NextConfig } from "next";

const API_URL = process.env.API_URL ?? "http://localhost:8000";

const config: NextConfig = {
  // Standalone output: emits a minimal `.next/standalone/server.js` plus the
  // exact node_modules subset it imports. Lets the production Docker image
  // ship a ~30MB runtime instead of dragging the full bun-installed tree.
  // Required by docker/Dockerfile.frontend's COPY layout.
  output: "standalone",
  // Mirror the Astro/Vite dev proxy so /query and friends hit FastAPI in `next dev`.
  // In prod, Caddy terminates these paths before they ever reach Node — these rules
  // exist so dev and prod use the same code path on the client side.
  async rewrites() {
    return [
      { source: "/query", destination: `${API_URL}/query` },
      { source: "/rank_predictors", destination: `${API_URL}/rank_predictors` },
      { source: "/ingest_pubmed", destination: `${API_URL}/ingest_pubmed` },
      { source: "/health", destination: `${API_URL}/health` },
      { source: "/health/:path*", destination: `${API_URL}/health/:path*` },
      // Only /papers/:stem/rows is reachable from the client side; the
      // /papers list and /papers/:stem detail are Next pages and must NOT
      // be rewritten, or Caddy/Next would shadow them on hard navigations.
      // SSR reaches /papers and /papers/:stem directly via process.env.API_URL.
      { source: "/papers/:stem/rows", destination: `${API_URL}/papers/:stem/rows` },
      { source: "/phenotypes", destination: `${API_URL}/phenotypes` },
      { source: "/phenotypes/:path*", destination: `${API_URL}/phenotypes/:path*` },
    ];
  },
};

export default config;
