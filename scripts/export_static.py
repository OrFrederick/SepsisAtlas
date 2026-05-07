"""Export the SepsisAtlas SQLite DB into static JSON for the Astro web app.

Reads `db.sqlite` (or path passed via --db), writes:
  <out-dir>/data/rows.json
  <out-dir>/data/papers.json
  <out-dir>/data/manifest.json
and copies every PDF from `data/papers/raw/` into <out-dir>/pdfs/.

Pure stdlib + sqlalchemy. No `sepsis_atlas` package imports so this runs
cleanly in CI without dependency on the rest of the codebase.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


# ---------- helpers ----------

def _table_columns(engine: Engine, table: str) -> set[str]:
    """Return set of column names for a table; empty set if table absent."""
    with engine.connect() as conn:
        res = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {r[1] for r in res}


def _table_exists(engine: Engine, table: str) -> bool:
    with engine.connect() as conn:
        res = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name=:n"),
            {"n": table},
        ).fetchone()
    return res is not None


def _parse_bbox(raw: Any) -> str | None:
    """Convert anchor_bbox (JSON-text or list) into 'x0,y0,x1,y1' (2-decimals).

    Returns None on missing/bad input.
    """
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        vals = list(raw)
    elif isinstance(raw, (bytes, bytearray)):
        try:
            vals = json.loads(raw.decode("utf-8"))
        except Exception:
            return None
    elif isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            vals = json.loads(s)
        except Exception:
            # maybe already comma-joined
            try:
                vals = [float(x) for x in s.split(",")]
            except Exception:
                return None
    else:
        return None

    if not isinstance(vals, (list, tuple)) or len(vals) != 4:
        return None
    try:
        floats = [float(v) for v in vals]
    except Exception:
        return None
    return ",".join(f"{v:.2f}" for v in floats)


def _coerce(val: Any, kind: str) -> Any:
    """Best-effort cast; return None on failure."""
    if val is None:
        return None
    try:
        if kind == "int":
            return int(val)
        if kind == "float":
            return float(val)
        if kind == "str":
            s = str(val)
            return s if s != "" else None
        if kind == "bool":
            return bool(val)
    except Exception:
        return None
    return val


def _norm_verdict(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip().lower()
    return s or None


# ---------- core export ----------

ROW_FIELDS_ORDER = [
    "row_id", "paper_ref", "cohort_label", "file_name", "cohort_size_n",
    "population_description", "population_location", "study_design",
    "mortality_rate_pct", "mortality_timepoint",
    "predictors", "predictor_canonical", "outcome", "outcome_type",
    "outcome_window_days", "model_specification",
    "effect_size_str", "effect_type", "effect_value", "ci_lo", "ci_hi",
    "p_value", "auc",
    "anchor_page", "anchor_bbox", "anchor_text", "anchor_section",
    "verifier_verdict", "verifier_score",
]


def export_rows(engine: Engine) -> list[dict]:
    if not (_table_exists(engine, "predictor_model") and _table_exists(engine, "study_cohort")):
        return []

    pm_cols = _table_columns(engine, "predictor_model")
    sc_cols = _table_columns(engine, "study_cohort")

    # row_id can be `id` or `row_id`
    pm_id_col = "id" if "id" in pm_cols else ("row_id" if "row_id" in pm_cols else None)
    if pm_id_col is None:
        return []

    # Build SELECT list with safe NULL fallbacks for missing columns.
    def pm(col: str, alias: str | None = None) -> str:
        a = alias or f"pm_{col}"
        return (f"pm.{col} AS {a}" if col in pm_cols else f"NULL AS {a}")

    def sc(col: str, alias: str | None = None) -> str:
        a = alias or f"sc_{col}"
        return (f"sc.{col} AS {a}" if col in sc_cols else f"NULL AS {a}")

    select_parts = [
        f"pm.{pm_id_col} AS row_id",
        sc("paper_ref"),
        sc("cohort_label"),
        sc("file_name"),
        sc("cohort_size_n"),
        sc("population_description"),
        sc("population_location"),
        sc("study_design"),
        sc("mortality_rate_pct"),
        sc("mortality_timepoint"),
        pm("predictors"),
        pm("predictor_canonical"),
        pm("outcome"),
        pm("outcome_type"),
        pm("outcome_window_days"),
        pm("model_specification"),
        pm("effect_size_str"),
        pm("effect_type"),
        pm("effect_value"),
        pm("ci_lo"),
        pm("ci_hi"),
        pm("p_value"),
        pm("auc"),
        pm("anchor_page"),
        pm("anchor_bbox"),
        pm("anchor_text"),
        pm("anchor_section"),
        pm("verifier_verdict"),
        pm("verifier_score"),
    ]
    sql = (
        "SELECT " + ", ".join(select_parts)
        + " FROM predictor_model pm LEFT JOIN study_cohort sc ON sc.cohort_id = pm.cohort_id"
    )

    out: list[dict] = []
    with engine.connect() as conn:
        result = conn.execute(text(sql))
        for r in result.mappings():
            row = {
                "row_id": _coerce(r["row_id"], "str"),
                "paper_ref": _coerce(r["sc_paper_ref"], "str"),
                "cohort_label": _coerce(r["sc_cohort_label"], "str"),
                "file_name": _coerce(r["sc_file_name"], "str"),
                "cohort_size_n": _coerce(r["sc_cohort_size_n"], "str"),
                "population_description": _coerce(r["sc_population_description"], "str"),
                "population_location": _coerce(r["sc_population_location"], "str"),
                "study_design": _coerce(r["sc_study_design"], "str"),
                "mortality_rate_pct": _coerce(r["sc_mortality_rate_pct"], "float"),
                "mortality_timepoint": _coerce(r["sc_mortality_timepoint"], "str"),
                "predictors": _coerce(r["pm_predictors"], "str"),
                "predictor_canonical": _coerce(r["pm_predictor_canonical"], "str"),
                "outcome": _coerce(r["pm_outcome"], "str"),
                "outcome_type": _coerce(r["pm_outcome_type"], "str"),
                "outcome_window_days": _coerce(r["pm_outcome_window_days"], "int"),
                "model_specification": _coerce(r["pm_model_specification"], "str"),
                "effect_size_str": _coerce(r["pm_effect_size_str"], "str"),
                "effect_type": _coerce(r["pm_effect_type"], "str"),
                "effect_value": _coerce(r["pm_effect_value"], "float"),
                "ci_lo": _coerce(r["pm_ci_lo"], "float"),
                "ci_hi": _coerce(r["pm_ci_hi"], "float"),
                "p_value": _coerce(r["pm_p_value"], "float"),
                "auc": _coerce(r["pm_auc"], "float"),
                "anchor_page": _coerce(r["pm_anchor_page"], "int"),
                "anchor_bbox": _parse_bbox(r["pm_anchor_bbox"]),
                "anchor_text": _coerce(r["pm_anchor_text"], "str"),
                "anchor_section": _coerce(r["pm_anchor_section"], "str"),
                "verifier_verdict": _norm_verdict(r["pm_verifier_verdict"]),
                "verifier_score": _coerce(r["pm_verifier_score"], "float"),
            }
            # Preserve declared field order for stable diffs
            out.append({k: row[k] for k in ROW_FIELDS_ORDER})
    return out


# verdict bucket mapping
_OK_SET = {"ok", "pass"}
_WEAK_SET = {"weak", "warn", "partial"}
_FAIL_SET = {"fail", "reject"}


def _bucket(verdict: str | None) -> str:
    if verdict is None or verdict == "unverified":
        return "unverified"
    if verdict in _OK_SET:
        return "ok"
    if verdict in _WEAK_SET:
        return "weak"
    if verdict in _FAIL_SET:
        return "fail"
    return "unverified"


def export_papers(engine: Engine, rows: list[dict], repo_root: Path) -> list[dict]:
    have_papers = _table_exists(engine, "papers")
    papers_meta: dict[str, dict] = {}

    if have_papers:
        papers_cols = _table_columns(engine, "papers")
        sel = ["file_name"]
        for opt in ("title", "year", "journal"):
            sel.append(opt if opt in papers_cols else f"NULL AS {opt}")
        sql = f"SELECT {', '.join(sel)} FROM papers"
        with engine.connect() as conn:
            for r in conn.execute(text(sql)).mappings():
                fn = r["file_name"]
                if not fn:
                    continue
                # Normalize to stem (no .pdf) so keys join cleanly with study_cohort.file_name
                stem = fn[:-4] if fn.lower().endswith(".pdf") else fn
                papers_meta[stem] = {
                    "title": _coerce(r.get("title"), "str"),
                    "year": _coerce(r.get("year"), "int"),
                    "journal": _coerce(r.get("journal"), "str"),
                }

    # Aggregate row-level data per file_name
    pm_extracted_ts_present = (
        _table_exists(engine, "predictor_model")
        and "extracted_ts" in _table_columns(engine, "predictor_model")
    )

    last_update: dict[str, str | None] = {}
    if pm_extracted_ts_present and _table_exists(engine, "study_cohort"):
        sql = (
            "SELECT sc.file_name AS fn, MAX(pm.extracted_ts) AS lu "
            "FROM predictor_model pm LEFT JOIN study_cohort sc ON sc.cohort_id=pm.cohort_id "
            "WHERE sc.file_name IS NOT NULL GROUP BY sc.file_name"
        )
        with engine.connect() as conn:
            for r in conn.execute(text(sql)):
                fn, lu = r[0], r[1]
                if fn:
                    last_update[fn] = str(lu) if lu is not None else None

    # Build set of file_names from rows + papers table
    file_names: set[str] = {fn for fn in papers_meta.keys()}
    for r in rows:
        if r.get("file_name"):
            file_names.add(r["file_name"])

    # Aggregate row counts and verdict buckets per paper from rows list
    counts: dict[str, dict[str, int]] = {}
    n_rows: dict[str, int] = {}
    for r in rows:
        fn = r.get("file_name")
        if not fn:
            continue
        n_rows[fn] = n_rows.get(fn, 0) + 1
        b = _bucket(r.get("verifier_verdict"))
        bucket = counts.setdefault(fn, {"ok": 0, "weak": 0, "fail": 0, "unverified": 0})
        bucket[b] += 1

    parsed_dir = repo_root / "data" / "papers" / "parsed"
    translated_dir = repo_root / "data" / "papers" / "translated"

    def _is_parsed(fn: str) -> bool:
        return (parsed_dir / fn).is_dir() or (parsed_dir / f"{fn}.json").exists()

    def _is_translated(fn: str) -> bool:
        if (parsed_dir / fn / "translated.json").exists():
            return True
        if translated_dir.exists():
            for ext in ("json", "md", "txt"):
                if (translated_dir / f"{fn}.{ext}").exists():
                    return True
        return False

    out: list[dict] = []
    for fn in sorted(file_names):
        meta = papers_meta.get(fn, {})
        verdicts = counts.get(fn, {"ok": 0, "weak": 0, "fail": 0, "unverified": 0})
        out.append({
            "file_name": fn,
            "title": meta.get("title"),
            "year": meta.get("year"),
            "journal": meta.get("journal"),
            "parsed": _is_parsed(fn),
            "translated": _is_translated(fn),
            "n_rows": n_rows.get(fn, 0),
            "verdicts": verdicts,
            "last_update": last_update.get(fn),
        })
    return out


def copy_pdfs(repo_root: Path, out_dir: Path) -> int:
    src_dir = repo_root / "data" / "papers" / "raw"
    if not src_dir.exists():
        print(f"warning: {src_dir} not found, skipping PDF copy", file=sys.stderr)
        return 0
    dst_dir = out_dir / "pdfs"
    dst_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for src in src_dir.glob("*.pdf"):
        dst = dst_dir / src.name
        if dst.exists():
            try:
                if dst.stat().st_mtime >= src.stat().st_mtime:
                    n += 1
                    continue
            except OSError:
                pass
        shutil.copy2(src, dst)
        n += 1
    return n


def db_sha(db_path: Path) -> str:
    h = hashlib.sha256()
    with db_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def main() -> int:
    p = argparse.ArgumentParser(description="Export Sepsis Atlas DB to static JSON.")
    p.add_argument("--db", default="db.sqlite", help="path to sqlite DB (default: db.sqlite)")
    p.add_argument("--out-dir", default="web/public", help="output directory (default: web/public)")
    args = p.parse_args()

    repo_root = Path.cwd()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = (repo_root / db_path).resolve()
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = (repo_root / out_dir).resolve()

    data_dir = out_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    papers: list[dict] = []
    sha = "0" * 12

    if db_path.exists():
        engine = create_engine(f"sqlite:///{db_path}")
        try:
            rows = export_rows(engine)
            papers = export_papers(engine, rows, repo_root)
        finally:
            engine.dispose()
        sha = db_sha(db_path)
    else:
        print(f"warning: DB not found at {db_path}; emitting empty rows/papers", file=sys.stderr)

    n_pdfs = copy_pdfs(repo_root, out_dir)

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "db_sha": sha,
        "n_papers": len(papers),
        "n_rows": len(rows),
        "schema_version": "1",
    }

    def _dump(path: Path, obj: Any) -> None:
        with path.open("w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False, sort_keys=False)
            f.write("\n")

    _dump(data_dir / "rows.json", rows)
    _dump(data_dir / "papers.json", papers)
    _dump(data_dir / "manifest.json", manifest)

    print(f"wrote {len(rows)} rows, {len(papers)} papers, {n_pdfs} PDFs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
