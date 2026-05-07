"""Row reranking: cosine similarity (sentence-transformers) with keyword fallback.

The MiniLM model is loaded lazily and cached. If sentence-transformers is not
installed (or the model can't download in the test env), we fall back to a
deterministic keyword Jaccard score so the pipeline still returns ordered rows.
"""

from __future__ import annotations

import os
import re
from typing import Any

_model = None
_model_failed = False


def _get_model():
    """Lazy-load sentence-transformers MiniLM. Short-circuits to keyword
    scoring when DISABLE_SEMANTIC_RERANK is set — useful when the HF
    download stalls (e.g. during demos with no network for the model)."""
    global _model, _model_failed
    if os.getenv("DISABLE_SEMANTIC_RERANK"):
        _model_failed = True
        return None
    if _model is not None or _model_failed:
        return _model
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore

        _model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    except Exception:
        _model_failed = True
        _model = None
    return _model


def _row_text(row: dict[str, Any]) -> str:
    parts = [
        row.get("paper_ref") or "",
        row.get("cohort_label") or "",
        row.get("predictors") or "",
        row.get("outcome") or "",
        row.get("model_specification") or "",
        row.get("effect_size_str") or "",
        row.get("population_description") or "",
    ]
    return " ".join(p for p in parts if p)


_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]+")


def _tokens(s: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(s)}


def _keyword_score(query: str, row: dict[str, Any]) -> float:
    qt = _tokens(query)
    rt = _tokens(_row_text(row))
    if not qt or not rt:
        return 0.0
    inter = qt & rt
    return len(inter) / (len(qt | rt) ** 0.5 + 1e-9)


def rerank(query: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return rows sorted descending by relevance to the query.

    Each row gets a `_score` field added. Verifier verdicts of `reject` are
    pushed to the bottom regardless of similarity.
    """
    if not rows:
        return rows

    model = _get_model()
    scores: list[float]
    if model is not None:
        try:
            import numpy as np  # type: ignore

            texts = [_row_text(r) for r in rows]
            qv = model.encode([query], normalize_embeddings=True)
            rv = model.encode(texts, normalize_embeddings=True)
            sim = (qv @ rv.T)[0]
            scores = [float(x) for x in sim]
        except Exception:
            scores = [_keyword_score(query, r) for r in rows]
    else:
        scores = [_keyword_score(query, r) for r in rows]

    out = []
    for r, s in zip(rows, scores):
        rr = dict(r)
        rr["_score"] = float(s)
        if (rr.get("verifier_verdict") or "").lower() == "reject":
            rr["_score"] -= 10.0  # bury rejected rows
        out.append(rr)
    out.sort(key=lambda r: r["_score"], reverse=True)
    return out
