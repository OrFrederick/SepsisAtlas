#!/usr/bin/env node
/**
 * Ensure web/public/data/{rows,papers,manifest}.json exist before astro build.
 * Agent X's exporter writes the real ones; if it hasn't run yet, fall back to
 * empty stubs so the build doesn't fail.
 */
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const dataDir = join(here, "..", "public", "data");
mkdirSync(dataDir, { recursive: true });

const seeds = {
  "rows.json": "[]",
  "papers.json": "[]",
  "manifest.json": JSON.stringify(
    {
      generated_at: "",
      db_sha: "",
      n_papers: 0,
      n_rows: 0,
      schema_version: "1",
    },
    null,
    2,
  ),
};

for (const [name, body] of Object.entries(seeds)) {
  const p = join(dataDir, name);
  if (!existsSync(p)) {
    writeFileSync(p, body);
    console.log(`seed-data: wrote stub ${name}`);
  }
}
