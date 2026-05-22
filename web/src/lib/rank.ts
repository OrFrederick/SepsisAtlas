export type SupportingRow = {
  paper_ref: string | null;
  cohort_label: string | null;
  cohort_id?: string | null;
  predictor: string | null;
  effect_size_str: string | null;
  effect_type: string | null;
  effect_value: number | null;
  auc: number | null;
  c_index: number | null;
  file_name: string | null;
  anchor_page: number | null;
  anchor_bbox: number[] | string | null;
  anchor_text: string | null;
};

export type RankRow = {
  predictor_canonical: string;
  best_metric: string;
  best_value: number | null;
  best_ci_lo: number | null;
  best_ci_hi: number | null;
  n_studies: number;
  best_paper_ref: string | null;
  supporting_rows: SupportingRow[];
};

export type RankResponse = {
  rows: RankRow[];
  fallback_note?: string;
};

export type RankFilters = {
  outcomeType: string;
  windowDays: string;
  paperRef: string;
  populationContains: string;
  topK: number;
};

export function buildRankUrl(backendUrl: string, f: RankFilters): string {
  const base = (backendUrl || "").replace(/\/$/, "") + "/rank_predictors";
  const p = new URLSearchParams();
  p.set("outcome_type", f.outcomeType);
  if (f.windowDays) p.set("outcome_window_days", f.windowDays);
  if (f.paperRef) p.set("paper_ref", f.paperRef);
  if (f.populationContains) p.set("population_contains", f.populationContains);
  p.set("top_k", String(f.topK));
  return `${base}?${p.toString()}`;
}

export function fmtVal(metric: string, v: number | null, lo: number | null, hi: number | null): string {
  if (v == null) return "—";
  const isAuc = metric === "AUC" || metric === "c_index";
  const s = isAuc ? v.toFixed(3) : v.toFixed(2);
  if (lo != null && hi != null) {
    return `${s} (${lo.toFixed(2)}–${hi.toFixed(2)})`;
  }
  return s;
}

export function effectStr(sr: SupportingRow): string {
  if (sr.effect_size_str) return sr.effect_size_str;
  if (sr.auc != null) return `AUC ${sr.auc.toFixed(3)}`;
  if (sr.c_index != null) return `c-index ${sr.c_index.toFixed(3)}`;
  if (sr.effect_type && sr.effect_value != null) {
    return `${sr.effect_type} ${sr.effect_value.toFixed(2)}`;
  }
  return "—";
}

export function viewerHrefFor(
  baseOrigin: string,
  sr: SupportingRow,
): string | null {
  if (!sr.file_name) return null;
  const stem = sr.file_name;
  let page = sr.anchor_page ?? 1;
  if (!Number.isFinite(page) || page < 1) page = 1;
  let url = `${baseOrigin}/viewer/${encodeURIComponent(stem)}?page=${page}`;
  const bbox = sr.anchor_bbox;
  // Backend serializes anchor_bbox either as a [x0,y0,x1,y1] tuple or as
  // its comma-joined string form. Both need to round-trip into the viewer
  // URL — silently dropping the string form was the bug.
  let bboxStr: string | null = null;
  if (Array.isArray(bbox) && bbox.length === 4) {
    bboxStr = bbox.map((v) => Number(v).toFixed(2)).join(",");
  } else if (typeof bbox === "string" && bbox.length > 0) {
    bboxStr = bbox;
  }
  if (bboxStr) url += `&bbox=${bboxStr}&origin=tl`;
  return url;
}
