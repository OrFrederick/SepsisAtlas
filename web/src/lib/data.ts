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

async function fetchJson(url: string): Promise<unknown> {
  // `no-store` disables Next's data cache so /papers always reflects the
  // current DB. The page-level cache (revalidate) was what locked the
  // empty "Corpus (0 papers)" view in place during the original bug, so
  // we intentionally bypass it here. React's `cache()` wrapper below
  // still dedupes calls within a single render pass.
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`fetch ${url} failed: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

export const loadPapers = cache(async (apiUrl?: string): Promise<Paper[]> => {
  const body = (await fetchJson(`${apiBase(apiUrl)}/papers`)) as { papers?: Paper[] };
  return body.papers ?? [];
});

export async function loadRowsFor(fileName: string, apiUrl?: string): Promise<Row[]> {
  const body = (await fetchJson(
    `${apiBase(apiUrl)}/papers/${encodeURIComponent(fileName)}/rows`,
  )) as { rows?: Row[] };
  return body.rows ?? [];
}
