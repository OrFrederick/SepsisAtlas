#!/usr/bin/env node
/**
 * Copy pdfjs-dist build artifacts into web/public/pdfjs/. Idempotent.
 * Run via `npm run vendor`.
 */
import { existsSync, mkdirSync, copyFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const src = join(root, "node_modules", "pdfjs-dist");
const dest = join(root, "public", "pdfjs");

if (!existsSync(src)) {
  console.error("vendor-pdfjs: pdfjs-dist not installed; run `npm install` first.");
  process.exit(1);
}

function copyDir(srcDir, destDir) {
  mkdirSync(destDir, { recursive: true });
  for (const entry of readdirSync(srcDir)) {
    const s = join(srcDir, entry);
    const d = join(destDir, entry);
    const stat = statSync(s);
    if (stat.isDirectory()) copyDir(s, d);
    else copyFileSync(s, d);
  }
}

function copyOne(rel) {
  const s = join(src, rel);
  const d = join(dest, rel);
  if (!existsSync(s)) {
    console.warn(`vendor-pdfjs: missing ${rel} in pdfjs-dist; skipping.`);
    return;
  }
  mkdirSync(dirname(d), { recursive: true });
  copyFileSync(s, d);
  console.log(`vendor-pdfjs: ${rel}`);
}

copyOne("build/pdf.min.mjs");
copyOne("build/pdf.worker.min.mjs");

// Some pdfjs-dist versions ship pdf_viewer.css under web/.
const viewerCss = join(src, "web", "pdf_viewer.css");
if (existsSync(viewerCss)) {
  mkdirSync(join(dest, "web"), { recursive: true });
  copyFileSync(viewerCss, join(dest, "web", "pdf_viewer.css"));
  console.log("vendor-pdfjs: web/pdf_viewer.css");
}

const cmaps = join(src, "cmaps");
if (existsSync(cmaps)) {
  copyDir(cmaps, join(dest, "cmaps"));
  console.log("vendor-pdfjs: cmaps/");
}
