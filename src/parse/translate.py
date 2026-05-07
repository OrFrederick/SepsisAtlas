"""Per-item language detection + translation to English for parsed papers.

Operates on the JSON shape produced by ``src.parse.docling_parser.parse_pdf``.
Detection is script-based (Unicode block ratios) — reliable for the
non-English papers we have in this corpus (Russian/CJK/Arabic/Greek). Latin
script is treated as English; if a Latin-script non-English paper is added
later, replace ``_detect_lang`` with a stronger model.

Translation goes through OpenRouter via the shared ``logged_llm_call``
wrapper so cost and latency are tracked alongside extraction calls.
"""

from __future__ import annotations

import re
from typing import Any

from sepsis_atlas.config import MODEL_TRANSLATE
from sepsis_atlas.llm import get_client, logged_llm_call


# Per-item: a text item is treated as non-English when the share of its
# characters falling into a non-Latin script exceeds this.
_NON_LATIN_THRESHOLD = 0.10
# Paper-level: ignore stray Greek/CJK chars that appear in stats notation
# (β, χ², Δ, α). A paper only counts as non-English when a meaningful share
# of its alpha characters live in a non-Latin script.
_PAPER_NON_LATIN_THRESHOLD = 0.05
_MIN_CHARS_FOR_DETECTION = 8


def _script_of(ch: str) -> str:
    cp = ord(ch)
    if 0x0400 <= cp <= 0x04FF or 0x0500 <= cp <= 0x052F:
        return "cyrillic"
    if 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF:
        return "cjk"
    if 0x3040 <= cp <= 0x30FF:
        return "japanese"
    if 0xAC00 <= cp <= 0xD7AF:
        return "korean"
    if 0x0600 <= cp <= 0x06FF or 0x0750 <= cp <= 0x077F:
        return "arabic"
    if 0x0370 <= cp <= 0x03FF:
        return "greek"
    if 0x0590 <= cp <= 0x05FF:
        return "hebrew"
    return ""


_LANG_BY_SCRIPT = {
    "cyrillic": "ru",
    "cjk": "zh",
    "japanese": "ja",
    "korean": "ko",
    "arabic": "ar",
    "greek": "el",
    "hebrew": "he",
}


def detect_lang(text: str) -> str:
    """Return ISO-639-1 code. ``en`` for Latin or empty/short strings."""
    if not text or len(text) < _MIN_CHARS_FOR_DETECTION:
        return "en"
    counts: dict[str, int] = {}
    total = 0
    for ch in text:
        if ch.isspace() or not ch.isalpha():
            continue
        total += 1
        s = _script_of(ch)
        if s:
            counts[s] = counts.get(s, 0) + 1
    if total == 0:
        return "en"
    top_script, top_count = "", 0
    for s, c in counts.items():
        if c > top_count:
            top_script, top_count = s, c
    if top_count / total >= _NON_LATIN_THRESHOLD:
        return _LANG_BY_SCRIPT.get(top_script, "und")
    return "en"


def paper_language(parsed: dict) -> str:
    """Char-weighted paper-level language detection.

    Concatenates title + section headings + section bodies and runs the same
    script-ratio detector. Table cells are excluded because they often hold
    isolated Greek/CJK math symbols (``β``, ``χ²``, ``Δ``) that would falsely
    flip an English paper to non-English. Returns ``en`` if non-Latin share
    sits below ``_PAPER_NON_LATIN_THRESHOLD``.
    """
    parts: list[str] = []
    title = parsed.get("title") or ""
    if title:
        parts.append(title)
    for s in parsed.get("sections") or []:
        h = s.get("heading") or ""
        b = s.get("text") or ""
        if h:
            parts.append(h)
        if b:
            parts.append(b)
    blob = "\n".join(parts)
    if len(blob) < _MIN_CHARS_FOR_DETECTION:
        return "en"

    counts: dict[str, int] = {}
    total = 0
    for ch in blob:
        if ch.isspace() or not ch.isalpha():
            continue
        total += 1
        s = _script_of(ch)
        if s:
            counts[s] = counts.get(s, 0) + 1
    if total == 0:
        return "en"
    top_script, top_count = "", 0
    for s, c in counts.items():
        if c > top_count:
            top_script, top_count = s, c
    if top_count / total >= _PAPER_NON_LATIN_THRESHOLD:
        return _LANG_BY_SCRIPT.get(top_script, "und")
    return "en"


_PROMPT = (
    "You translate biomedical text to English. Hard rules: "
    "(1) Output ONLY the translated text — no preface, no notes, no '[Note: ...]' "
    "blocks, no explanations, no quotation marks around the output. "
    "(2) No Markdown — do not add '#', '##', '*', or '-' prefixes the source did "
    "not have. Plain text only. "
    "(3) Preserve numbers, units, statistical values (p-values, CIs, hazard "
    "ratios, percentages), abbreviations, and proper nouns verbatim. "
    "(4) Preserve line breaks and overall structure of the input. "
    "(5) If the input is already English, return it unchanged. "
    "(6) Do not summarize, expand, or reorder."
)


_MD_HEAD = re.compile(r"^\s*#{1,6}\s+", flags=re.MULTILINE)
_NOTE_BLOCK = re.compile(
    r"\[\s*(?:Note|Translator's note|Translation note)\s*:[^\]]*\]",
    flags=re.IGNORECASE,
)


def _clean_translation(text: str) -> str:
    """Strip artifacts the LLM occasionally adds despite the prompt."""
    if not text:
        return text
    text = _MD_HEAD.sub("", text)
    text = _NOTE_BLOCK.sub("", text)
    # Strip wrapping quotes only if both ends carry them.
    s = text.strip()
    if len(s) >= 2 and s[0] in "\"'`" and s[-1] in "\"'`":
        s = s[1:-1]
    return s.strip()


@logged_llm_call(stage="translate")
def _translate_call(messages: list[dict], model: str, **kwargs):
    return get_client().chat.completions.create(messages=messages, model=model, **kwargs)


def translate_text(text: str, source_lang: str, *, paper_id: str | None = None) -> str:
    """Translate ``text`` from ``source_lang`` to English. Returns text unchanged
    if already English or empty."""
    if not text.strip() or source_lang == "en":
        return text
    messages = [
        {"role": "system", "content": _PROMPT},
        {"role": "user", "content": f"Source language: {source_lang}\n\n{text}"},
    ]
    resp = _translate_call(
        messages=messages,
        model=MODEL_TRANSLATE,
        temperature=0.0,
        paper_id=paper_id,
        prompt_id="translate-v1",
    )
    out = resp.choices[0].message.content or ""
    return _clean_translation(out)


def translate_parsed(parsed: dict[str, Any], *, paper_id: str | None = None) -> dict[str, Any]:
    """Return a new parsed-JSON dict with non-English items translated.

    Item-level translation preserves bbox→span alignment: each section text,
    each table cell, and each offset entry remain in 1:1 correspondence with
    their original parse item, but ``full_text`` is rebuilt from the
    translated chunks so character ranges in ``offsets`` stay accurate.

    Adds fields:
      - ``translated_to_en``: bool
      - ``source_language``: paper-level ISO-639-1 code
      - ``original_full_text``, ``original_sections``, ``original_tables``,
        ``original_title`` (only when translated_to_en is True)
      - per-section ``source_language`` and per-table-cell ``source_language``
    """

    sections = parsed.get("sections") or []
    tables = parsed.get("tables") or []
    offsets = parsed.get("offsets") or []
    title = parsed.get("title") or ""

    # Paper-level decision uses char-weighted detection over title+sections
    # (excludes table cells: math symbols would create false positives).
    paper_lang = paper_language(parsed)

    if paper_lang == "en":
        # Nothing to translate — return parsed as-is, marked.
        out = dict(parsed)
        out["translated_to_en"] = False
        out["source_language"] = "en"
        return out

    # Per-item language decisions (used to skip already-English items and
    # avoid LLM cost on, e.g., English abstracts inside a Russian paper).
    section_langs = [
        detect_lang((s.get("heading") or "") + "\n" + (s.get("text") or "")) for s in sections
    ]
    cell_langs: list[list[str]] = [
        [detect_lang(c.get("text") or "") for c in (t.get("cells") or [])] for t in tables
    ]
    title_lang = detect_lang(title)

    # Snapshot originals before mutating.
    original_full_text = parsed.get("full_text", "")
    original_sections = [dict(s) for s in sections]
    original_tables = [
        {**t, "cells": [dict(c) for c in (t.get("cells") or [])]} for t in tables
    ]
    original_title = title

    # Translate title.
    new_title = title
    if title_lang != "en" and title.strip():
        new_title = translate_text(title, title_lang, paper_id=paper_id)

    # Translate sections in place (heading + body together if non-en).
    new_sections: list[dict[str, Any]] = []
    translated_section_text: list[tuple[str, str]] = []  # (new_heading, new_body)
    for s, lg in zip(sections, section_langs):
        heading = s.get("heading", "") or ""
        body = s.get("text", "") or ""
        if lg != "en" and (heading.strip() or body.strip()):
            new_heading = (
                translate_text(heading, lg, paper_id=paper_id) if heading.strip() else heading
            )
            new_body = (
                translate_text(body, lg, paper_id=paper_id) if body.strip() else body
            )
        else:
            new_heading, new_body = heading, body
        ns = dict(s)
        ns["heading"] = new_heading
        ns["text"] = new_body
        ns["source_language"] = lg
        new_sections.append(ns)
        translated_section_text.append((new_heading, new_body))

    # Translate table cells + captions.
    new_tables: list[dict[str, Any]] = []
    for t, langs in zip(tables, cell_langs):
        new_cells: list[dict[str, Any]] = []
        for c, lg in zip(t.get("cells") or [], langs):
            txt = c.get("text") or ""
            new_txt = (
                translate_text(txt, lg, paper_id=paper_id)
                if lg != "en" and txt.strip()
                else txt
            )
            nc = dict(c)
            nc["text"] = new_txt
            nc["source_language"] = lg
            new_cells.append(nc)
        cap = t.get("caption", "") or ""
        cap_lang = detect_lang(cap)
        new_cap = (
            translate_text(cap, cap_lang, paper_id=paper_id)
            if cap_lang != "en" and cap.strip()
            else cap
        )
        nt = dict(t)
        nt["cells"] = new_cells
        nt["caption"] = new_cap
        new_tables.append(nt)

    # Rebuild full_text + offsets from translated section items.
    # Mirrors the layout produced by docling_parser: each item contributes
    # ``text + "\n"`` to full_text; offsets[*].start/end cover the text only.
    new_full_text_parts: list[str] = []
    new_offsets: list[dict[str, Any]] = []
    cursor = 0

    # Walk original offsets and rewrite the text portion. Headings come from
    # the section heading; body items come from the section's translated text
    # split back into the same number of body offset entries by paragraph.
    # Because the parser glued multi-paragraph bodies with "\n", we split on
    # "\n" — the count must match the number of body offsets in the section.
    # If the LLM collapses or splits paragraphs, fall back to whole-section
    # text on a single (synthetic) offset so the invariant "offsets cover
    # full_text" is preserved.
    section_iter = iter(zip(original_sections, new_sections))
    cur_orig: dict | None = None
    cur_new: dict | None = None
    cur_body_chunks: list[str] = []
    cur_body_offsets: list[dict] = []

    def _advance_section():
        nonlocal cur_orig, cur_new, cur_body_chunks, cur_body_offsets
        try:
            cur_orig, cur_new = next(section_iter)
        except StopIteration:
            cur_orig, cur_new = None, None
            cur_body_chunks, cur_body_offsets = [], []
            return
        body = cur_new.get("text", "") or ""
        cur_body_chunks = body.split("\n") if body else []
        cur_body_offsets = []

    _advance_section()

    # We process offsets in order. When we encounter a heading offset, it
    # corresponds to the next section's heading. Body offsets between two
    # headings belong to the prior section's body.
    pending: list[dict] = []
    grouped: list[tuple[dict | None, list[dict]]] = []  # (heading_offset, body_offsets)
    current_head: dict | None = None
    current_bodies: list[dict] = []
    for off in offsets:
        if off.get("kind") == "heading":
            if current_head is not None or current_bodies:
                grouped.append((current_head, current_bodies))
            current_head = off
            current_bodies = []
        else:
            current_bodies.append(off)
    if current_head is not None or current_bodies:
        grouped.append((current_head, current_bodies))

    # Emit heading + body chunks per section, tracking the new section list.
    sec_idx = 0
    for head_off, body_offs in grouped:
        # Match this group to translated section if heading is present.
        if head_off is not None and sec_idx < len(new_sections):
            new_sec = new_sections[sec_idx]
            sec_idx += 1
            heading_text = new_sec.get("heading", "") or ""
        else:
            new_sec = None
            heading_text = ""

        if head_off is not None and heading_text:
            chunk = heading_text + "\n"
            new_offsets.append(
                {
                    **head_off,
                    "start": cursor,
                    "end": cursor + len(heading_text),
                }
            )
            new_full_text_parts.append(chunk)
            cursor += len(chunk)

        if not body_offs:
            continue

        # Body re-emission: try to align translated paragraphs 1:1 with
        # the original body offsets. If counts mismatch, emit the whole
        # translated body under the first body offset and drop the rest's
        # start/end coverage (set to zero-length sentinels) so anchors still
        # find substrings via full_text search.
        body_text = (new_sec or {}).get("text", "") if new_sec else ""
        chunks = body_text.split("\n") if body_text else [""] * len(body_offs)

        if len(chunks) == len(body_offs):
            for off, chunk_text in zip(body_offs, chunks):
                piece = chunk_text + "\n"
                new_offsets.append(
                    {**off, "start": cursor, "end": cursor + len(chunk_text)}
                )
                new_full_text_parts.append(piece)
                cursor += len(piece)
        else:
            # Mismatch: emit whole body under first offset; collapse rest to
            # zero-length markers at end-of-body.
            piece = body_text + "\n" if body_text else ""
            first = body_offs[0]
            new_offsets.append(
                {**first, "start": cursor, "end": cursor + len(body_text)}
            )
            new_full_text_parts.append(piece)
            cursor += len(piece)
            collapsed_pos = cursor  # zero-length at boundary
            for off in body_offs[1:]:
                new_offsets.append({**off, "start": collapsed_pos, "end": collapsed_pos})

    new_full_text = "".join(new_full_text_parts)

    out = dict(parsed)
    out["title"] = new_title
    out["sections"] = new_sections
    out["tables"] = new_tables
    out["full_text"] = new_full_text
    out["offsets"] = new_offsets
    out["translated_to_en"] = True
    out["source_language"] = paper_lang
    out["original_title"] = original_title
    out["original_full_text"] = original_full_text
    out["original_sections"] = original_sections
    out["original_tables"] = original_tables
    return out
