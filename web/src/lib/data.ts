import { readFile } from "node:fs/promises";
import { join } from "node:path";
import type { Paper, Row } from "./types";

// Project root defaults to web/ when called from a Next RSC.
// Tests pass an explicit root to exercise the loader against a temp dir.
function dataPath(root: string, name: string): string {
  return join(root, "public", "data", name);
}

export async function loadPapers(root: string = process.cwd()): Promise<Paper[]> {
  const raw = await readFile(dataPath(root, "papers.json"), "utf-8");
  return JSON.parse(raw) as Paper[];
}

export async function loadRows(root: string = process.cwd()): Promise<Row[]> {
  const raw = await readFile(dataPath(root, "rows.json"), "utf-8");
  return JSON.parse(raw) as Row[];
}

export async function loadRowsFor(fileName: string, root: string = process.cwd()): Promise<Row[]> {
  const rows = await loadRows(root);
  return rows.filter((r) => r.file_name === fileName);
}
