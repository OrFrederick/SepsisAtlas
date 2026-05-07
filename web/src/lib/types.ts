// Shape contract with scripts/export_static.py (Agent X owns the writer).

export interface Row {
  row_id: string;
  paper_ref: string;
  cohort_label: string | null;
  file_name: string;
  cohort_size_n: number | null;
  population_description: string | null;
  population_location: string | null;
  study_design: string | null;
  mortality_rate_pct: number | null;
  mortality_timepoint: string | null;
  predictors: string | null;
  predictor_canonical: string | null;
  outcome: string | null;
  outcome_type: string | null;
  outcome_window_days: number | null;
  model_specification: string | null;
  effect_size_str: string | null;
  effect_type: string | null;
  effect_value: number | null;
  ci_lo: number | null;
  ci_hi: number | null;
  p_value: number | null;
  auc: number | null;
  anchor_page: number | null;
  /** Comma-joined "x0,y0,x1,y1" or null. */
  anchor_bbox: string | null;
  anchor_text: string | null;
  anchor_section: string | null;
  verifier_verdict: "ok" | "weak" | "fail" | "unverified" | null;
  verifier_score: number | null;
}

export interface PaperVerdicts {
  ok: number;
  weak: number;
  fail: number;
  unverified: number;
}

export interface Paper {
  file_name: string;
  title: string | null;
  year: number | null;
  journal: string | null;
  parsed: boolean;
  translated: boolean;
  n_rows: number;
  verdicts: PaperVerdicts;
  last_update: string | null;
}

export interface Manifest {
  generated_at: string;
  db_sha: string;
  n_papers: number;
  n_rows: number;
  schema_version: string;
}
