import { cache } from "react";
import type { Paper, Row } from "./types";

// Where to reach FastAPI from SSR. `API_URL` mirrors the convention already
// set in web/next.config.ts (server-only, no NEXT_PUBLIC_ prefix); inside
// the production container it's set to `http://backend:8000` by
// docker-compose.prod.yml. Locally `bun run dev` defaults to the dev
// FastAPI on :8000.
function apiBase(override?: string): string {
  if (override) return override;
  return (process.env.API_URL || "http://localhost:8000").replace(/\/$/, "");
}

// Hard upper bound on an SSR fetch. With `force-dynamic` on every paper
// route, an unbounded fetch lets a hung backend pin a Next request-handler
// indefinitely and exhaust the pool — site-wide outage instead of a single
// /papers 500. 5s is well above p99 for these endpoints; anything slower is
// already a degraded state and should surface as a Next error boundary.
const FETCH_TIMEOUT_MS = 5000;

export class NotFoundError extends Error {
  constructor(url: string) {
    super(`fetch ${url} returned 404`);
    this.name = "NotFoundError";
  }
}

async function fetchJson(url: string): Promise<unknown> {
  // `no-store` disables Next's data cache so /papers always reflects the
  // current DB. The page-level cache (revalidate) was what locked the
  // empty "Corpus (0 papers)" view in place during the original bug, so
  // we intentionally bypass it here. React's `cache()` wrappers below
  // still dedupe calls within a single render pass.
  const res = await fetch(url, {
    cache: "no-store",
    signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
  });
  if (res.status === 404) {
    throw new NotFoundError(url);
  }
  if (!res.ok) {
    throw new Error(`fetch ${url} failed: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

export const loadPapers = cache(async (apiUrl?: string): Promise<Paper[]> => {
  const body = (await fetchJson(`${apiBase(apiUrl)}/papers`)) as { papers?: Paper[] };
  return body.papers ?? [];
});

// Cheap existence check for the per-paper page. Returns null on 404 so the
// caller can `notFound()` without paying for the full corpus list (which
// the previous implementation did purely to drive existence).
export const loadPaper = cache(
  async (fileName: string, apiUrl?: string): Promise<Paper | null> => {
    try {
      const body = (await fetchJson(
        `${apiBase(apiUrl)}/papers/${encodeURIComponent(fileName)}`,
      )) as Paper;
      return body;
    } catch (err) {
      if (err instanceof NotFoundError) return null;
      throw err;
    }
  },
);

export const loadRowsFor = cache(
  async (fileName: string, apiUrl?: string): Promise<Row[]> => {
    const body = (await fetchJson(
      `${apiBase(apiUrl)}/papers/${encodeURIComponent(fileName)}/rows`,
    )) as { rows?: Row[] };
    return body.rows ?? [];
  },
);
