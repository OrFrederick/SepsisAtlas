"""Read-only corpus integrity check.

Cross-references four axes per paper:

1. Filename stem (``data/papers/parsed/<stem>.json``)
2. Parsed-JSON ``title`` (Docling-extracted)
3. First DOI-shaped string in parsed full_text
4. Extracted DOI + ``data_sets`` from ``db.sqlite`` (study_cohort)
5. Gold DOI + ``Data Sets`` from ``data/ground_truth/study_cohort.csv``
   (matched by ``Papers`` paper_ref, e.g. "Zhang 2021")

Surfaces, but does NOT fix, mismatches. Useful as a CI smoke test
(``--strict`` returns exit 1 if any flag fires) and as a manifest of
where the corpus is editorially inconsistent.

Flags:
    T  filename stem (``<Author>_<Year>``) does not appear in parsed
       paper: surname missing from parsed full_text, or year missing.
       Catches wrong-PDF-in-corpus errors.
    D  extracted DOI != DOI grep'd from parsed full_text,
       or extracted DOI != gold DOI
    S  gold Data Sets does not substring-match extracted data_sets
       (case-insensitive, set-wise)

Usage::

    python -m tools.sepsis_atlas.corpus_check
    python -m tools.sepsis_atlas.corpus_check --json
    python -m tools.sepsis_atlas.corpus_check --strict
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "db.sqlite"
DEFAULT_PARSED_DIR = REPO_ROOT / "data" / "papers" / "parsed"
DEFAULT_GOLD_CSV = REPO_ROOT / "data" / "ground_truth" / "study_cohort.csv"

DOI_RE = re.compile(r"\b10\.\d{4,9}/[\w.\-]+\b")
STEM_RE = re.compile(r"^([A-Za-z][A-Za-z\-']+)_(\d{4})$")


def _stem_to_paper_ref(stem: str) -> str:
    """``Zhang_2021`` -> ``Zhang 2021``."""
    return stem.replace("_", " ")


def _stem_match(stem: str, parsed_text: str) -> tuple[bool, float]:
    """Check filename stem (``<Surname>_<Year>``) against parsed paper.

    Surname must appear (case-insensitive, word-bounded) in parsed full_text;
    year too. Returns ``(ok, score)`` where ``score`` is in {0.0, 0.5, 1.0}:
    1.0 both hit, 0.5 one of two, 0.0 neither (or stem unparseable).
    """
    m = STEM_RE.match(stem)
    if not m:
        return (False, 0.0)
    surname, year = m.group(1), m.group(2)
    text = (parsed_text or "")
    surname_hit = re.search(r"\b" + re.escape(surname) + r"\b", text, re.IGNORECASE) is not None
    year_hit = re.search(r"\b" + re.escape(year) + r"\b", text) is not None
    score = 0.0 + (0.5 if surname_hit else 0.0) + (0.5 if year_hit else 0.0)
    return (surname_hit and year_hit, score)


def _first_doi(text: str) -> str | None:
    if not text:
        return None
    m = DOI_RE.search(text)
    return m.group(0) if m else None


def _load_parsed(path: Path) -> tuple[str, str, str | None]:
    """Return ``(title, full_text, first_doi)``."""
    data = json.loads(path.read_text())
    title = data.get("title") or ""
    full_text = data.get("full_text") or ""
    if isinstance(full_text, list):
        full_text = "\n".join(str(x) for x in full_text)
    return title, full_text, _first_doi(full_text)


def _load_extracted(db_path: Path, file_name: str) -> tuple[str | None, set[str]]:
    """Return (doi, set_of_data_sets) for the given parsed file stem."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = conn.execute(
            "SELECT doi, data_sets FROM study_cohort WHERE file_name = ?",
            (file_name,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    doi: str | None = None
    datasets: set[str] = set()
    for row_doi, row_ds in rows:
        if row_doi and not doi:
            doi = row_doi
        if row_ds:
            for piece in re.split(r"[;,/]| and ", row_ds):
                piece = piece.strip()
                if piece:
                    datasets.add(piece)
    return doi, datasets


def _load_gold(csv_path: Path) -> dict[str, dict[str, Any]]:
    """Group gold rows by ``Papers`` (paper_ref)."""
    out: dict[str, dict[str, Any]] = {}
    with csv_path.open() as f:
        for row in csv.DictReader(f):
            ref = (row.get("Papers") or "").strip()
            if not ref:
                continue
            entry = out.setdefault(ref, {"doi": None, "data_sets": set()})
            doi = (row.get("DOI") or "").strip()
            if doi and not entry["doi"]:
                entry["doi"] = doi
            ds = (row.get("Data Sets") or "").strip()
            if ds:
                for piece in re.split(r"[;,/]| and ", ds):
                    piece = piece.strip()
                    if piece:
                        entry["data_sets"].add(piece)
    return out


def _datasets_match(gold: set[str], extracted: set[str]) -> bool:
    """Case-insensitive substring set comparison.

    Every gold dataset must appear (as a substring, either way) in some
    extracted dataset string; only checked when gold is non-empty.
    """
    if not gold:
        return True
    extracted_lower = [e.lower() for e in extracted]
    for g in gold:
        gl = g.lower()
        hit = any(gl in e or e in gl for e in extracted_lower)
        if not hit:
            return False
    return True


def check_corpus(
    parsed_dir: Path = DEFAULT_PARSED_DIR,
    db_path: Path = DEFAULT_DB,
    gold_csv: Path = DEFAULT_GOLD_CSV,
) -> list[dict[str, Any]]:
    gold = _load_gold(gold_csv)
    results: list[dict[str, Any]] = []
    for parsed_path in sorted(parsed_dir.glob("*.json")):
        stem = parsed_path.stem
        paper_ref = _stem_to_paper_ref(stem)
        title, full_text, parsed_doi = _load_parsed(parsed_path)
        extracted_doi, extracted_ds = _load_extracted(db_path, stem)
        gold_entry = gold.get(paper_ref)
        gold_doi = gold_entry["doi"] if gold_entry else None
        gold_ds: set[str] = gold_entry["data_sets"] if gold_entry else set()

        # Search stem (surname + year) anywhere in title or full_text.
        # Surname-anywhere catches author lists; year-anywhere catches
        # journal headers, citations of self, copyright lines.
        stem_search_text = (title or "") + "\n" + full_text
        title_ok, overlap = _stem_match(stem, stem_search_text)

        # DOI agreement: extracted vs parsed-text always evaluable when
        # both present; gold vs extracted only when both present.
        doi_ext_vs_parsed: bool | None
        if extracted_doi and parsed_doi:
            doi_ext_vs_parsed = extracted_doi.lower() == parsed_doi.lower()
        else:
            doi_ext_vs_parsed = None

        doi_gold_vs_extracted: bool | None
        if gold_doi and extracted_doi:
            doi_gold_vs_extracted = gold_doi.lower() == extracted_doi.lower()
        else:
            doi_gold_vs_extracted = None

        ds_match: bool | None
        if gold_entry and gold_ds:
            ds_match = _datasets_match(gold_ds, extracted_ds)
        else:
            ds_match = None

        flags: list[str] = []
        if not title_ok:
            flags.append("T")
        # Any DOI disagreement (where evaluable) fires D.
        if doi_ext_vs_parsed is False or doi_gold_vs_extracted is False:
            flags.append("D")
        if ds_match is False:
            flags.append("S")

        results.append({
            "file_stem": stem,
            "paper_ref": paper_ref,
            "parsed_title": title,
            "title_match_score": round(overlap, 3),
            "title_ok": title_ok,
            "parsed_doi": parsed_doi,
            "extracted_doi": extracted_doi,
            "gold_doi": gold_doi,
            "doi_extracted_vs_parsed": doi_ext_vs_parsed,
            "doi_gold_vs_extracted": doi_gold_vs_extracted,
            "extracted_data_sets": sorted(extracted_ds),
            "gold_data_sets": sorted(gold_ds),
            "datasets_gold_vs_extracted": ds_match,
            "has_gold": gold_entry is not None,
            "flags": "".join(flags) or "-",
        })
    return results


def _truncate(s: str | None, n: int) -> str:
    if not s:
        return ""
    return s if len(s) <= n else s[: n - 1] + "…"


def render_markdown(rows: list[dict[str, Any]]) -> str:
    headers = [
        "file_stem", "stem_match", "parsed_doi", "extracted_doi",
        "gold_doi", "extracted_data_sets", "gold_data_sets", "flags",
    ]
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        lines.append("| " + " | ".join([
            r["file_stem"],
            f"{r['title_match_score']:.2f}",
            _truncate(r["parsed_doi"], 30),
            _truncate(r["extracted_doi"], 30),
            _truncate(r["gold_doi"], 30),
            _truncate(", ".join(r["extracted_data_sets"]), 40) or "-",
            _truncate(", ".join(r["gold_data_sets"]), 40) or "-",
            r["flags"],
        ]) + " |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--parsed-dir", type=Path, default=DEFAULT_PARSED_DIR)
    p.add_argument("--db", dest="db_path", type=Path, default=DEFAULT_DB)
    p.add_argument("--gold", dest="gold_csv", type=Path, default=DEFAULT_GOLD_CSV)
    p.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    p.add_argument("--strict", action="store_true", help="exit 1 if any flag fires")
    args = p.parse_args(argv)

    rows = check_corpus(args.parsed_dir, args.db_path, args.gold_csv)
    if args.json:
        # Sets aren't JSON-serializable but we already converted to list above.
        print(json.dumps(rows, indent=2))
    else:
        print(render_markdown(rows))

    if args.strict and any(r["flags"] != "-" for r in rows):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
