"""Docling-based PDF parser.

Produces a JSON-serialisable dict per paper containing:

- ``title`` — best guess at document title
- ``page_count`` — number of pages
- ``sections`` — list of {heading, level, text, page, bbox}
- ``tables`` — list of {page, bbox, n_rows, n_cols, cells:[{row, col, row_span,
  col_span, text, bbox, column_header}], markdown}
- ``full_text`` — concatenated body text of all non-table items
- ``offsets`` — list of {start, end, page, bbox, kind, label, ref} mapping each
  character range in ``full_text`` back to its source item
- ``parser`` — parser version string

bbox values are emitted as ``{l, t, r, b, coord_origin, page_no}`` where
coordinates are the raw Docling values (PDF points, with the origin per the
``coord_origin`` field — typically ``BOTTOMLEFT`` for prov bboxes and
``TOPLEFT`` for table cell bboxes).
"""

from __future__ import annotations

import importlib.metadata
from pathlib import Path
from typing import Any

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import TableItem


try:
    _DOCLING_VERSION = importlib.metadata.version("docling")
except importlib.metadata.PackageNotFoundError:  # pragma: no cover
    _DOCLING_VERSION = "unknown"

PARSER_VERSION = f"docling-{_DOCLING_VERSION}"

# Labels that carry meaningful body text (excluded: page_header, page_footer,
# footnote, picture, formula, table — table is dumped separately).
_BODY_LABELS = {
    "text",
    "paragraph",
    "list_item",
    "title",
    "section_header",
    "caption",
    "code",
    "reference",
}

_HEADING_LABELS = {"section_header", "title"}

# Module-level converter cache so worker processes only build it once.
_converter: DocumentConverter | None = None


def _get_converter() -> DocumentConverter:
    global _converter
    if _converter is None:
        opts = PdfPipelineOptions()
        opts.do_ocr = False  # provided PDFs are programmatic; OCR ~10x slower
        opts.do_table_structure = True
        opts.generate_page_images = False
        _converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )
    return _converter


def _bbox_to_dict(bbox: Any, page_no: int | None) -> dict[str, Any] | None:
    if bbox is None:
        return None
    origin = getattr(bbox, "coord_origin", None)
    return {
        "l": float(bbox.l),
        "t": float(bbox.t),
        "r": float(bbox.r),
        "b": float(bbox.b),
        "coord_origin": getattr(origin, "value", str(origin)) if origin is not None else None,
        "page_no": page_no,
    }


def _prov_bbox(item: Any) -> tuple[int | None, dict | None]:
    prov_list = getattr(item, "prov", None) or []
    if not prov_list:
        return None, None
    prov = prov_list[0]
    return prov.page_no, _bbox_to_dict(prov.bbox, prov.page_no)


def _label_name(item: Any) -> str:
    label = getattr(item, "label", None)
    if label is None:
        return ""
    return getattr(label, "value", None) or getattr(label, "name", "").lower() or str(label)


def _table_to_dict(table: TableItem, doc: Any) -> dict[str, Any]:
    page_no, bbox = _prov_bbox(table)
    data = table.data
    cells_out: list[dict[str, Any]] = []
    n_rows = getattr(data, "num_rows", None) or 0
    n_cols = getattr(data, "num_cols", None) or 0
    for cell in getattr(data, "table_cells", []) or []:
        cells_out.append(
            {
                "row": getattr(cell, "start_row_offset_idx", None),
                "col": getattr(cell, "start_col_offset_idx", None),
                "row_span": getattr(cell, "row_span", 1),
                "col_span": getattr(cell, "col_span", 1),
                "text": getattr(cell, "text", "") or "",
                "column_header": bool(getattr(cell, "column_header", False)),
                "row_header": bool(getattr(cell, "row_header", False)),
                "bbox": _bbox_to_dict(getattr(cell, "bbox", None), page_no),
            }
        )

    try:
        markdown = table.export_to_markdown(doc=doc)
    except Exception:  # noqa: BLE001
        markdown = ""

    caption = ""
    try:
        caption = (table.caption_text(doc) or "").strip()
    except Exception:  # noqa: BLE001
        caption = ""

    return {
        "self_ref": getattr(table, "self_ref", None),
        "page": page_no,
        "bbox": bbox,
        "n_rows": n_rows,
        "n_cols": n_cols,
        "cells": cells_out,
        "markdown": markdown,
        "caption": caption,
    }


def parse_pdf(pdf_path: str | Path) -> dict[str, Any]:
    """Parse a single PDF and return a structured dict.

    Raises any exception from Docling — caller is responsible for catching.
    """

    pdf_path = Path(pdf_path)
    converter = _get_converter()
    result = converter.convert(str(pdf_path))
    doc = result.document

    page_count = len(getattr(doc, "pages", {}) or {})

    # Title resolution: prefer a TITLE-labeled item; fall back to the first
    # substantive section_header on page 1; fall back to filename stem.
    title = pdf_path.stem
    found_title = False
    for item, _ in doc.iterate_items():
        if _label_name(item) == "title":
            txt = (getattr(item, "text", "") or "").strip()
            if txt:
                title = txt
                found_title = True
                break
    if not found_title:
        # Score page-1 section headers; pick the longest plausible one.
        candidates: list[tuple[int, str]] = []
        for item, _ in doc.iterate_items():
            if _label_name(item) != "section_header":
                continue
            txt = (getattr(item, "text", "") or "").strip()
            if not txt or len(txt) < 15:
                continue
            page_no, _ = _prov_bbox(item)
            if page_no not in (1, None):
                continue
            # Skip page-header-ish strings (no spaces, ALL CAPS run-ons).
            letters = [c for c in txt if c.isalpha()]
            if not letters:
                continue
            upper_ratio = sum(c.isupper() for c in letters) / len(letters)
            if upper_ratio > 0.75 and " " not in txt:
                continue
            # Skip very short journal section labels.
            if txt.lower() in {"original investigation", "research", "research article"}:
                continue
            candidates.append((len(txt), txt))
        if candidates:
            candidates.sort(reverse=True)
            title = candidates[0][1]

    sections: list[dict[str, Any]] = []
    full_text_parts: list[str] = []
    offsets: list[dict[str, Any]] = []
    cursor = 0
    current_section: str | None = None

    # Build a self_ref → table index for skipping during text walk.
    table_refs = {getattr(t, "self_ref", id(t)) for t in doc.tables}

    for item, level in doc.iterate_items():
        label = _label_name(item)
        ref = getattr(item, "self_ref", None)
        if ref in table_refs:
            continue  # tables handled separately
        if label not in _BODY_LABELS:
            continue

        text = (getattr(item, "text", "") or "").strip()
        if not text:
            continue

        page_no, bbox = _prov_bbox(item)

        if label in _HEADING_LABELS:
            current_section = text
            sections.append(
                {
                    "heading": text,
                    "level": int(level) if level is not None else 0,
                    "text": "",
                    "page": page_no,
                    "bbox": bbox,
                    "self_ref": ref,
                }
            )
        else:
            # Append to current section's text accumulator.
            if sections:
                if sections[-1]["text"]:
                    sections[-1]["text"] += "\n" + text
                else:
                    sections[-1]["text"] = text

        chunk = text + "\n"
        full_text_parts.append(chunk)
        offsets.append(
            {
                "start": cursor,
                "end": cursor + len(text),
                "page": page_no,
                "bbox": bbox,
                "kind": "heading" if label in _HEADING_LABELS else "body",
                "label": label,
                "section": current_section,
                "ref": ref,
            }
        )
        cursor += len(chunk)

    full_text = "".join(full_text_parts)

    tables_out = [_table_to_dict(t, doc) for t in doc.tables]

    return {
        "parser": PARSER_VERSION,
        "source_pdf": pdf_path.name,
        "title": title,
        "page_count": page_count,
        "sections": sections,
        "tables": tables_out,
        "full_text": full_text,
        "offsets": offsets,
    }
