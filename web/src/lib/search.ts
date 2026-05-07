import Fuse from "fuse.js";
import type { Row } from "./types";

const FUSE_OPTIONS: ConstructorParameters<typeof Fuse<Row>>[1] = {
  keys: [
    "predictor_canonical",
    "predictors",
    "outcome",
    "paper_ref",
    "effect_size_str",
    "population_description",
    "anchor_text",
  ],
  threshold: 0.35,
  includeScore: true,
  ignoreLocation: true,
};

export function buildIndex(rows: Row[]): Fuse<Row> {
  return new Fuse(rows, FUSE_OPTIONS);
}

export function queryIndex(fuse: Fuse<Row>, q: string, limit = 50): Row[] {
  const trimmed = q.trim();
  if (!trimmed) return [];
  return fuse.search(trimmed, { limit }).map((r) => r.item);
}
