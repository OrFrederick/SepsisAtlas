"""Corpus file-name conventions.

Organizer ships PDFs as ``LastAuthor_YYYY.pdf`` (see ``data/papers/_index.xlsx``),
optionally with a disambiguator suffix (``Li_2020_a.pdf``). The publication
year is part of the contract, so we treat the stem as the source of truth for
``Paper.year`` rather than re-extracting from PDF metadata.
"""

from __future__ import annotations

import re

_YEAR_RE = re.compile(r"_(\d{4})(?:_|$)")
_MIN_YEAR = 1900
_MAX_YEAR = 2099


def year_from_stem(file_name: str | None) -> int | None:
    """Extract a 4-digit publication year from a corpus stem.

    Accepts ``"Baloch_2022"`` or ``"Baloch_2022.pdf"`` or ``"Li_2020_a"``.
    Returns ``None`` if the stem does not match the convention or the year
    is outside ``[1900, 2099]``.
    """
    if not file_name:
        return None
    stem = file_name[:-4] if file_name.lower().endswith(".pdf") else file_name
    m = _YEAR_RE.search(stem)
    if not m:
        return None
    y = int(m.group(1))
    if y < _MIN_YEAR or y > _MAX_YEAR:
        return None
    return y
