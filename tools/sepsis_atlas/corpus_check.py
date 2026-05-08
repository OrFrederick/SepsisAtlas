"""Read-only integrity check across parsed JSONs, db.sqlite, and the gold CSV.

Returns one dict per parsed paper with cross-source comparisons and flags.

Flag characters (concatenated, "-" if none):
  D  DOI mismatch between gold and extracted (papers table)
  S  Data-set mismatch between gold and extracted (study_cohort table)
  T  Title absent in parsed JSON
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from pathlib import Path


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

_WS = re.compile(r"\s+")


def _norm_doi(s: str | None) -> str:
    if not s:
        return ""
    s = s.strip().lower()
    s = s.removeprefix("https://doi.org/")
    s = s.removeprefix("http://doi.org/")
    s = s.removeprefix("doi:")
    return _WS.sub("", s)


def _norm_dataset(s: str) -> str:
    """Normalise a single dataset label for comparison.

    Collapses whitespace/hyphens, lowercases, removes version suffixes like
    V.1.4 so MIMIC-III V.1.4 ≈ MIMIC-III ≈ MIMIC III.
    """
    s = s.strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[-_]", " ", s)
    # Drop trailing version qualifiers: "v1.4", "v.1.4", "1.4", "(2013)", etc.
    s = re.sub(r"\s*v\.?\d[\d.]*$", "", s)
    s = re.sub(r"\s*\(\s*\w+\s*et\s+al\..*?\)", "", s)
    s = re.sub(r"\s*\(\s*\d{4}\s*\)", "", s)
    return s.strip()


def _datasets_match(gold: set[str], extracted: set[str]) -> bool:
    """True if every gold dataset has a normalized match in extracted."""
    if not gold:
        return True
    if not extracted:
        return False
    norm_ext = {_norm_dataset(d) for d in extracted}
    return all(any(_norm_dataset(g) in ne or ne in _norm_dataset(g)
                   for ne in norm_ext) for g in gold)


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------


def _load_gold(gold_csv: Path) -> dict[str, dict]:
    """Return {paper_ref: {dois: set, datasets: set}} from the gold CSV."""
    result: dict[str, dict] = {}
    with open(gold_csv, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            ref = (row.get("Papers") or "").strip()
            if not ref:
                continue
            doi = (row.get("DOI") or "").strip()
            ds = (row.get("Data Sets") or "").strip()
            entry = result.setdefault(ref, {"dois": set(), "datasets": set()})
            if doi:
                entry["dois"].add(doi)
            if ds:
                entry["datasets"].add(ds)
    return result


def _load_db(db_path: Path) -> dict[str, dict]:
    """Return {paper_ref: {doi: str|None, data_sets: set[str]}} from db.sqlite."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    result: dict[str, dict] = {}
    try:
        # DOI lives in papers table (keyed by file_name = stem + ".pdf")
        for row in con.execute("SELECT file_name, doi FROM papers"):
            stem = (row["file_name"] or "").removesuffix(".pdf")
            result.setdefault(stem, {"doi": None, "data_sets": set()})
            result[stem]["doi"] = row["doi"] or None

        # Datasets live in study_cohort (joined via file_name without .pdf)
        for row in con.execute(
            "SELECT file_name, paper_ref, data_sets FROM study_cohort"
        ):
            stem = row["file_name"] or ""
            ds = (row["data_sets"] or "").strip()
            entry = result.setdefault(stem, {"doi": None, "data_sets": set()})
            if ds:
                entry["data_sets"].add(ds)
            # Carry paper_ref for matching with gold (uses "Gai 2022" style).
            entry.setdefault("paper_ref", row["paper_ref"] or "")
    finally:
        con.close()
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_corpus(
    parsed_dir: Path,
    db_path: Path,
    gold_csv: Path,
) -> list[dict]:
    """Check integrity across the three corpus sources.

    Parameters
    ----------
    parsed_dir:
        Directory containing ``<stem>.json`` Docling parse outputs.
    db_path:
        Path to ``db.sqlite``.
    gold_csv:
        Path to ``data/ground_truth/study_cohort.csv``.

    Returns
    -------
    list[dict]
        One dict per parsed paper, sorted by file_stem.
    """
    gold = _load_gold(gold_csv)
    db = _load_db(db_path)

    rows: list[dict] = []

    for json_path in sorted(parsed_dir.glob("*.json")):
        stem = json_path.stem  # e.g. "Zhang_2021"
        try:
            parsed = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        parsed_title: str = (parsed.get("title") or "").strip()
        parsed_doi: str | None = (parsed.get("doi") or "").strip() or None

        db_entry = db.get(stem, {})
        extracted_doi: str | None = db_entry.get("doi")
        extracted_data_sets: set[str] = db_entry.get("data_sets", set())
        paper_ref: str = db_entry.get("paper_ref") or stem.replace("_", " ")

        # Match against gold using paper_ref (e.g. "Zhang 2021")
        gold_entry = gold.get(paper_ref, {})
        has_gold = bool(gold_entry)
        gold_dois: set[str] = gold_entry.get("dois", set())
        gold_datasets: set[str] = gold_entry.get("datasets", set())

        # One canonical DOI per source (take first sorted value)
        gold_doi = next(iter(sorted(gold_dois)), None) if gold_dois else None

        # Comparisons
        ext_norm = _norm_doi(extracted_doi)
        parsed_norm = _norm_doi(parsed_doi)
        gold_norm = _norm_doi(gold_doi)

        doi_extracted_vs_parsed: bool | None = (
            ext_norm == parsed_norm if (ext_norm and parsed_norm) else None
        )
        doi_gold_vs_extracted: bool | None = (
            gold_norm == ext_norm if (gold_norm and ext_norm) else None
        )
        datasets_gold_vs_extracted: bool | None = (
            _datasets_match(gold_datasets, extracted_data_sets)
            if has_gold
            else None
        )

        # Flags
        flag_chars = []
        if doi_gold_vs_extracted is False:
            flag_chars.append("D")
        if datasets_gold_vs_extracted is False:
            flag_chars.append("S")
        if not parsed_title:
            flag_chars.append("T")
        flags = "".join(flag_chars) or "-"

        rows.append(
            {
                "file_stem": stem,
                "paper_ref": paper_ref,
                "parsed_title": parsed_title,
                "parsed_doi": parsed_doi,
                "extracted_doi": extracted_doi,
                "gold_doi": gold_doi,
                "extracted_data_sets": extracted_data_sets or None,
                "gold_data_sets": gold_datasets or None,
                "doi_extracted_vs_parsed": doi_extracted_vs_parsed,
                "doi_gold_vs_extracted": doi_gold_vs_extracted,
                "datasets_gold_vs_extracted": datasets_gold_vs_extracted,
                "title_ok": bool(parsed_title),
                "flags": flags,
                "has_gold": has_gold,
            }
        )

    return rows
