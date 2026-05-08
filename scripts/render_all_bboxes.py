"""Render all anchor bboxes onto their source PDF pages and concatenate into one PDF.

Usage:
    python scripts/render_all_bboxes.py [--out PATH] [--max-per-paper N]
"""
import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

import fitz  # PyMuPDF

ROOT = Path(__file__).resolve().parents[1]
PAPERS_RAW = ROOT / "data" / "papers" / "raw"
DB = ROOT / "db.sqlite"
DEFAULT_OUT = ROOT / "static" / "bbox_proof" / "all_bboxes.pdf"


def build_page_map(conn, max_per_paper: int | None) -> dict[str, dict[int, list[dict]]]:
    """Returns {file_name: {page_num: [row, ...]}}."""
    rows = conn.execute("""
        SELECT sc.file_name, pm.anchor_page, pm.anchor_bbox,
               pm.predictor_canonical, pm.effect_size_str, pm.verifier_verdict
        FROM predictor_model pm
        JOIN study_cohort sc ON sc.cohort_id = pm.cohort_id
        WHERE pm.anchor_bbox IS NOT NULL AND pm.anchor_bbox != 'null'
        ORDER BY sc.file_name, pm.anchor_page
    """).fetchall()

    by_paper: dict[str, dict[int, list]] = defaultdict(lambda: defaultdict(list))
    counts: dict[str, int] = defaultdict(int)
    for file_name, page, bbox_str, pred, effect, verdict in rows:
        if max_per_paper and counts[file_name] >= max_per_paper:
            continue
        try:
            bbox = json.loads(bbox_str)
        except (ValueError, TypeError):
            continue
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        by_paper[file_name][page].append({
            "bbox": bbox,
            "pred": pred or "",
            "effect": (effect or "")[:40],
            "verdict": verdict or "",
        })
        counts[file_name] += 1
    return by_paper


def verdict_color(v: str) -> tuple[float, float, float]:
    v = (v or "").lower()
    if v in ("pass", "ok"):
        return (0.0, 0.7, 0.2)   # green
    if v in ("weak", "partial", "warn"):
        return (1.0, 0.6, 0.0)   # amber
    if v in ("fail", "reject"):
        return (0.85, 0.1, 0.1)  # red
    return (0.1, 0.45, 0.7)      # teal (unknown)


def render_page(src_doc: fitz.Document, page_num: int, annotations: list[dict]) -> fitz.Page:
    """Return a copy of the page with bbox rectangles drawn."""
    page = src_doc[page_num - 1]
    page_h = page.rect.height
    for ann in annotations:
        x0, y0, x1, y1 = ann["bbox"]
        # bboxes are already TL screen coords (y increases downward)
        rect = fitz.Rect(x0, y0, x1, y1)
        color = verdict_color(ann["verdict"])
        # Highlight rectangle
        page.draw_rect(rect, color=color, fill=(*color, 0.18), width=1.5, overlay=True)
        # Small label above the box
        label = f"{ann['pred']} | {ann['effect']}"
        fs = 5.5
        label_y = max(y0 - fs - 1, 2)
        page.insert_text(
            fitz.Point(x0, label_y),
            label[:60],
            fontsize=fs,
            color=color,
            overlay=True,
        )
    return page


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--max-per-paper", type=int, default=None,
                        help="Cap annotations per paper (useful for quick preview)")
    args = parser.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB)
    page_map = build_page_map(conn, args.max_per_paper)
    conn.close()

    out_doc = fitz.open()
    total_pages = 0
    total_ann = 0

    for file_name in sorted(page_map):
        pdf_path = PAPERS_RAW / f"{file_name}.pdf"
        if not pdf_path.exists():
            print(f"  SKIP {file_name} (PDF not found)")
            continue

        src = fitz.open(pdf_path)
        pages_with_ann = sorted(page_map[file_name])

        for page_num in pages_with_ann:
            anns = page_map[file_name][page_num]
            render_page(src, page_num, anns)
            out_doc.insert_pdf(src, from_page=page_num - 1, to_page=page_num - 1)
            total_pages += 1
            total_ann += len(anns)

        src.close()
        print(f"  {file_name}: {len(pages_with_ann)} pages, "
              f"{sum(len(v) for v in page_map[file_name].values())} annotations")

    out_doc.save(str(args.out), garbage=4, deflate=True)
    out_doc.close()
    print(f"\nWrote {args.out}  ({total_pages} pages, {total_ann} annotations)")


if __name__ == "__main__":
    main()
