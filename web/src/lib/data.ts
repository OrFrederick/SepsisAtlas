import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { cache } from "react";
import type { Paper, Row } from "./types";

// Data root resolution order:
//   1. Explicit `root` argument — tests pass a temp dir.
//   2. SEPSIS_ATLAS_WEB_ROOT env var — preferred in production so the
//      systemd unit can pin a path independent of where the binary launches.
//   3. process.cwd() — works for `bun run dev`/`bun run start` from web/.
// Wrapped in React `cache` so a single render request reuses the parsed JSON
// instead of re-reading + re-parsing on every loader call.
function resolveRoot(root?: string): string {
  if (root) return root;
  return process.env.SEPSIS_ATLAS_WEB_ROOT ?? process.cwd();
}

function dataPath(root: string, name: string): string {
  return join(root, "public", "data", name);
}

export const loadPapers = cache(async (root?: string): Promise<Paper[]> => {
  const raw = await readFile(dataPath(resolveRoot(root), "papers.json"), "utf-8");
  return JSON.parse(raw) as Paper[];
});

export const loadRows = cache(async (root?: string): Promise<Row[]> => {
  const raw = await readFile(dataPath(resolveRoot(root), "rows.json"), "utf-8");
  return JSON.parse(raw) as Row[];
});

export async function loadRowsFor(fileName: string, root?: string): Promise<Row[]> {
  const rows = await loadRows(root);
  return rows.filter((r) => r.file_name === fileName);
}
